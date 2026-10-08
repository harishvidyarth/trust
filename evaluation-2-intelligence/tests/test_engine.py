from __future__ import annotations

import pytest

from firewall.config import DEFAULT_WEIGHTS, Config
from firewall.engine import evaluate
from firewall.models import Experience, Project, Route
from firewall.signals.duplicates import normalize_email, normalize_phone
from firewall.signals.qualification import canonical_skill
from firewall.store import InMemoryApplicationStore


def codes(decision):
    return {reason.code for reason in decision.reasons}


def test_honest_candidate_passes(application_factory, job_factory):
    decision = evaluate(application_factory(), job_factory(), InMemoryApplicationStore(), Config())
    assert decision.score == 100
    assert decision.route == Route.PASS_TO_ATS
    assert decision.reasons == []


def test_honest_candidate_can_apply_to_ten_different_jobs_slowly(application_factory, job_factory):
    store = InMemoryApplicationStore()
    decisions = []
    for index in range(10):
        application = application_factory(
            application_id=f"app-{index}",
            job_id=f"job-{index}",
            submitted_at=1_767_225_600 + index * 120,
        )
        decisions.append(evaluate(application, job_factory(), store, Config()))
    assert all(decision.route == Route.PASS_TO_ATS for decision in decisions)
    assert all(not codes(decision) & {"DUP_EMAIL", "DUP_PHONE", "DUP_RESUME_NEAR"} for decision in decisions)


def test_bot_burst_escalates(application_factory, job_factory):
    store = InMemoryApplicationStore()
    final = None
    for index in range(30):
        final = evaluate(
            application_factory(
                application_id=f"bot-{index}",
                name=f"Bot Candidate {index}",
                email=f"bot{index}@example.com",
                phone=f"900000{index:04d}",
                device_id="bot-device",
                ip="198.51.100.9",
                session_seconds=5,
                paste_char_ratio=0.99,
                submitted_at=1_767_225_600 + index,
                projects=[Project(name=f"P{index}", description=f"Unique automated submission material number {index}")],
            ),
            job_factory(),
            store,
            Config(),
        )
    assert final is not None
    assert final.route == Route.MANUAL_REVIEW
    assert "VELOCITY_HIGH" in codes(final)


def test_gmail_dot_and_plus_tag_duplicate_detected(application_factory, job_factory):
    store = InMemoryApplicationStore()
    evaluate(application_factory(email="a.da+first@gmail.com"), job_factory(), store, Config())
    second = application_factory(application_id="app-2", email="ada+second@googlemail.com")
    decision = evaluate(second, job_factory(), store, Config())
    assert {"DUP_EMAIL", "DUP_SAME_JOB"} <= codes(decision)


def test_near_duplicate_resume_detected(application_factory, job_factory):
    store = InMemoryApplicationStore()
    evaluate(application_factory(), job_factory(), store, Config())
    copied = application_factory(
        application_id="copy",
        name="Grace Hopper",
        email="grace@example.com",
        phone="9999999999",
    )
    decision = evaluate(copied, job_factory(), store, Config())
    assert "DUP_RESUME_NEAR" in codes(decision)
    assert decision.route == Route.ADDITIONAL_VERIFICATION


def test_shared_employer_and_title_without_projects_is_not_a_near_duplicate(application_factory, job_factory):
    store = InMemoryApplicationStore()
    experience = [Experience(company="Infosys", title="Software Engineer", start="2021-01", end="2025-01")]
    evaluate(application_factory(experience=experience, projects=[]), job_factory(), store, Config())

    unrelated = application_factory(
        application_id="unrelated",
        job_id="job-2",
        name="Grace Hopper",
        email="grace@example.com",
        phone="9999999999",
        experience=experience,
        projects=[],
    )
    decision = evaluate(unrelated, job_factory(), store, Config())

    assert "DUP_RESUME_NEAR" not in codes(decision)


def test_project_names_without_descriptions_are_not_resume_similarity_evidence(application_factory, job_factory):
    store = InMemoryApplicationStore()
    project = Project(name="Identical Long Shared Project Name With Many Words For Everyone", description="")
    evaluate(application_factory(projects=[project]), job_factory(), store, Config())
    decision = evaluate(
        application_factory(
            application_id="different-person",
            name="Different Person",
            email="different@example.com",
            phone="8000000000",
            projects=[project],
        ),
        job_factory(),
        store,
        Config(),
    )

    assert "DUP_RESUME_NEAR" not in codes(decision)


def test_same_job_reapply_flagged(application_factory, job_factory):
    store = InMemoryApplicationStore()
    evaluate(application_factory(), job_factory(), store, Config())
    decision = evaluate(application_factory(application_id="again"), job_factory(), store, Config())
    assert "DUP_SAME_JOB" in codes(decision)
    assert decision.route == Route.MANUAL_REVIEW


def test_missing_must_have_lowers_route(application_factory, job_factory):
    application = application_factory(skills=["Python"])
    decision = evaluate(application, job_factory(), InMemoryApplicationStore(), Config())
    assert decision.route == Route.MANUAL_REVIEW
    assert "QUAL_MISSING_MUST_HAVE" in codes(decision)


@pytest.mark.parametrize(
    ("required_count", "skills_present", "expected_weight"),
    [(10, 9, 4), (10, 0, 35), (100, 99, 1)],
    ids=["one-of-ten-missing", "ten-of-ten-missing", "minimum-one-point"],
)
def test_missing_must_have_penalty_scales_with_missing_fraction(
    application_factory, job_factory, required_count, skills_present, expected_weight
):
    required = [f"skill-{index}" for index in range(required_count)]
    application = application_factory(skills=required[:skills_present])
    decision = evaluate(
        application,
        job_factory(must_have_skills=required, min_years=0),
        InMemoryApplicationStore(),
        Config(),
    )

    reason = next(item for item in decision.reasons if item.code == "QUAL_MISSING_MUST_HAVE")
    assert reason.weight == expected_weight


@pytest.mark.parametrize(
    ("skills_present", "expected_route"),
    [(6, Route.PASS_TO_ATS), (5, Route.MANUAL_REVIEW)],
    ids=["coverage-at-threshold", "coverage-below-threshold"],
)
def test_qualification_only_route_uses_coverage(application_factory, job_factory, skills_present, expected_route):
    required = [f"skill-{index}" for index in range(10)]
    decision = evaluate(
        application_factory(skills=required[:skills_present]),
        job_factory(must_have_skills=required, min_years=0),
        InMemoryApplicationStore(),
        Config(),
    )

    assert codes(decision) == {"QUAL_MISSING_MUST_HAVE"}
    assert decision.route == expected_route


def test_under_experience_is_qualification_concern(application_factory, job_factory):
    application = application_factory(
        experience=[Experience(company="New Co", title="Engineer", start="2025-01", end="2025-07")]
    )
    decision = evaluate(application, job_factory(), InMemoryApplicationStore(), Config())
    assert "QUAL_UNDER_EXPERIENCE" in codes(decision)
    assert decision.reasons[0].severity == "low"
    assert decision.route == Route.PASS_TO_ATS


@pytest.mark.parametrize(
    ("variant", "equivalent"),
    [
        ("React.js", "react"),
        ("reactjs", "react"),
        ("NodeJS", "node.js"),
        ("node", "node.js"),
        ("cpp", "c++"),
        ("golang", "go"),
        ("ts", "typescript"),
        ("js", "javascript"),
        ("ml", "machine learning"),
        ("Machine-Learning", "machine learning"),
        ("k8s", "kubernetes"),
        ("postgres", "postgresql"),
        ("py", "python"),
        ("tf", "tensorflow"),
        ("sklearn", "scikit-learn"),
        ("scikit learn", "scikit-learn"),
    ],
)
def test_canonical_skill_variants(variant, equivalent):
    assert canonical_skill(variant) == canonical_skill(equivalent)


def test_end_before_start_is_invalid(application_factory, job_factory):
    application = application_factory(
        experience=[Experience(company="Reverse Co", title="Engineer", start="2025-06", end="2024-01")]
    )
    decision = evaluate(application, job_factory(min_years=0), InMemoryApplicationStore(), Config())
    assert "TIMELINE_INVALID" in codes(decision)


def test_future_start_date_is_invalid(application_factory, job_factory):
    application = application_factory(
        experience=[Experience(company="Future Co", title="Engineer", start="2027-01", end="present")]
    )
    decision = evaluate(application, job_factory(min_years=0), InMemoryApplicationStore(), Config())
    assert "TIMELINE_INVALID" in codes(decision)


def test_future_end_date_within_allowed_window_is_valid(application_factory, job_factory):
    application = application_factory(
        experience=[Experience(company="Current Co", title="Engineer", start="2025-01", end="2026-02")]
    )
    decision = evaluate(application, job_factory(min_years=0), InMemoryApplicationStore(), Config())

    assert "TIMELINE_INVALID" not in codes(decision)


def test_future_expected_end_counts_experience_only_through_present(application_factory, job_factory):
    application = application_factory(
        experience=[Experience(company="Current Co", title="Engineer", start="2020-01", end="2026-02")]
    )
    decision = evaluate(application, job_factory(min_years=5), InMemoryApplicationStore(), Config())

    assert "QUAL_UNDER_EXPERIENCE" not in codes(decision)
    assert "TIMELINE_INVALID" not in codes(decision)


def test_future_end_date_beyond_allowed_window_is_invalid(application_factory, job_factory):
    application = application_factory(
        experience=[Experience(company="Planned Co", title="Engineer", start="2025-01", end="2027-02")]
    )
    decision = evaluate(application, job_factory(min_years=0), InMemoryApplicationStore(), Config())

    assert "TIMELINE_INVALID" in codes(decision)


def test_long_timeline_overlap_detected(application_factory, job_factory):
    experience = [
        Experience(company="One", title="Engineer", start="2020-01", end="2022-12"),
        Experience(company="Two", title="Engineer", start="2022-01", end="2024-01"),
    ]
    decision = evaluate(
        application_factory(experience=experience), job_factory(min_years=0), InMemoryApplicationStore(), Config()
    )
    assert "TIMELINE_OVERLAP" in codes(decision)


def test_claimed_experience_checked(application_factory, job_factory):
    application = application_factory(claimed_experience_years=9)
    decision = evaluate(application, job_factory(), InMemoryApplicationStore(), Config())
    assert "TIMELINE_INVALID" in codes(decision)


def test_evaluation_is_deterministic_and_idempotent(application_factory, job_factory):
    application = application_factory()
    first_store = InMemoryApplicationStore()
    first = evaluate(application, job_factory(), first_store, Config())
    repeated = evaluate(application, job_factory(), first_store, Config())
    independent = evaluate(application, job_factory(), InMemoryApplicationStore(), Config())
    assert first == repeated == independent


def test_config_threshold_changes_routing(application_factory, job_factory):
    application = application_factory(session_seconds=20)
    default = evaluate(application, job_factory(), InMemoryApplicationStore(), Config())
    stricter = evaluate(application, job_factory(), InMemoryApplicationStore(), Config(pass_min=90))
    assert default.route == Route.PASS_TO_ATS
    assert stricter.route == Route.ADDITIONAL_VERIFICATION


def test_reason_order_is_weight_descending(application_factory, job_factory):
    application = application_factory(skills=[], session_seconds=5, paste_char_ratio=0.99)
    decision = evaluate(application, job_factory(), InMemoryApplicationStore(), Config())
    weights = [item.weight for item in decision.reasons]
    assert weights == sorted(weights, reverse=True)


def test_bulk_paste_requires_fast_submission(application_factory, job_factory):
    application = application_factory(session_seconds=120, paste_char_ratio=1)
    decision = evaluate(application, job_factory(), InMemoryApplicationStore(), Config())
    assert "PASTE_BULK" not in codes(decision)


@pytest.mark.parametrize("stable_identity", ["email", "phone"])
def test_identity_velocity_catches_rotating_devices_and_ips(application_factory, job_factory, stable_identity):
    store = InMemoryApplicationStore()
    final = None
    for index in range(7):
        email = (
            f"same.person+job{index}@googlemail.com"
            if stable_identity == "email"
            else f"person{index}@example.com"
        )
        phone = (
            "+91 98765 43210" if index % 2 == 0 else "(98765) 43210"
        ) if stable_identity == "phone" else f"900000{index:04d}"
        final = evaluate(
            application_factory(
                application_id=f"identity-{stable_identity}-{index}",
                job_id=f"job-{index}",
                email=email,
                phone=phone,
                device_id=f"device-{index}",
                ip=f"198.51.100.{index + 1}",
                session_seconds=120,
                submitted_at=1_767_225_600 + index,
                projects=[Project(name=f"Project {index}", description=f"Distinct human project narrative {index}")],
            ),
            job_factory(),
            store,
            Config(),
        )

    assert final is not None
    velocity_reason = next(item for item in final.reasons if item.code == "VELOCITY_HIGH")
    assert "identity=7" in velocity_reason.detail
    assert final.route == Route.MANUAL_REVIEW


def test_template_reuse_across_candidates(application_factory, job_factory):
    store = InMemoryApplicationStore()
    final = None
    for index in range(4):
        final = evaluate(
            application_factory(
                application_id=f"template-{index}",
                name=f"Candidate {index}",
                email=f"candidate{index}@example.com",
                phone=f"811111{index:04d}",
                projects=[Project(name="Different Name", description="The exact same screening project answer every time")],
            ),
            job_factory(),
            store,
            Config(),
        )
    assert final is not None
    assert "TEMPLATE_REUSE" in codes(final)


def test_normalizers():
    assert normalize_email(" A.Da+jobs@GoogleMail.com ") == "ada@gmail.com"
    assert normalize_phone("+91 (98765) 43210") == "9876543210"


def test_config_new_safety_defaults():
    config = Config()
    assert config.min_shingles_for_similarity == 8
    assert config.qual_pass_coverage == 0.6
    assert config.max_future_end_months == 12
    assert config.identity_velocity_limit == 6


@pytest.mark.parametrize("invalid_weight", [-1, 1.5, "35", True])
def test_config_rejects_negative_or_non_integer_weights(invalid_weight):
    weights = {**DEFAULT_WEIGHTS, "DUP_EMAIL": invalid_weight}
    with pytest.raises(ValueError, match="weights"):
        Config(weights=weights)


def test_config_copies_and_freezes_weights():
    supplied = dict(DEFAULT_WEIGHTS)
    config = Config(weights=supplied)
    supplied["DUP_EMAIL"] = 999

    assert config.weights["DUP_EMAIL"] == DEFAULT_WEIGHTS["DUP_EMAIL"]
    with pytest.raises(TypeError):
        config.weights["DUP_EMAIL"] = 999
