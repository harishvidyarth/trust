from __future__ import annotations

import httpx
import pytest

from firewall.enrichment.models import Claims, EnrichmentSignal
from firewall.enrichment.roles import (
    NullRegistry,
    PatentRecord,
    RegistryRecord,
    RoleConnector,
    extract_role_claims,
    pending_references,
    role_reasons,
    run_role_checks,
    select_profile,
)
from firewall.enrichment.roles import formats
from firewall.enrichment.runner import enrich

from tests_intel.conftest import make_application, mock_client

ENV = {"FIREWALL_ENRICH": "1"}


def candidate(name="Ada Synthetic", email="ada@example.com"):
    return make_application(name=name, email=email).candidate


def codes(signals):
    return {signal.code for signal in signals}


class StaticRegistry:
    def __init__(self, membership=None, directorship=None, regulator=None, patent=None):
        self._membership = membership
        self._directorship = directorship
        self._regulator = regulator
        self._patent = patent
        self.calls = []

    def membership(self, body, number):
        self.calls.append(("membership", body, number))
        return self._membership

    def directorship(self, person, company):
        self.calls.append(("directorship", person, company))
        return self._directorship

    def regulator(self, registration_id):
        self.calls.append(("regulator", registration_id))
        return self._regulator

    def patent(self, number):
        self.calls.append(("patent", number))
        return self._patent


def no_http(request):
    raise AssertionError("unexpected request " + str(request.url))


@pytest.mark.parametrize(
    "title,skills,expected",
    [
        ("Chartered Accountant", [], "finance"),
        ("Embedded Firmware Engineer", ["C", "RTOS"], "hardware"),
        ("Product Designer", ["Figma"], "sales_ops_design"),
        ("Regional Sales Manager", [], "sales_ops_design"),
        ("Backend Engineer", ["Python"], "general"),
        ("", [], "general"),
        ("Hardware Sales", [], "general"),
    ],
)
def test_select_profile(title, skills, expected):
    assert select_profile(title, skills) == expected


def test_format_validators():
    assert formats.valid_credential_id("ABC12345XYZ")
    assert not formats.valid_credential_id("123456")
    assert not formats.valid_credential_id("AAAAAA1")
    assert not formats.valid_credential_id("12")
    for body in ("ICAI", "ACCA", "CFA", "XYZ"):
        assert formats.valid_membership(body, "123456") is None and formats.valid_membership(body, "1") is None
    assert formats.valid_regulator_id("INA000012345") is True and formats.valid_regulator_id("INA12") is None
    assert formats.valid_din("12345678") is True and formats.valid_din("12345") is None
    assert formats.valid_patent("US 10,123,456 B2") is True and formats.valid_patent("US 2020/0123456 A1") is True
    assert formats.valid_patent("US12") is False and formats.valid_patent("WO 2020/123456") is True
    assert formats.valid_patent("WO 2020/12345") is False and formats.valid_patent("WO02/12345") is None
    assert formats.valid_patent("EP1234567A1") is True and formats.valid_patent("EP12") is None
    assert formats.valid_patent("IN 201841000123") is True and formats.valid_patent("IN123456") is None
    assert formats.valid_patent("ZZ 99999") is None
    assert formats.valid_arxiv("2101.01234") and not formats.valid_arxiv("2113.01234") and not formats.valid_arxiv("2101.0123")
    assert formats.valid_arxiv("0706.0001") and not formats.valid_arxiv("0706.00001") and formats.valid_arxiv("hep-th/9901001")
    assert formats.valid_arxiv("math.GT/0309136v2") and not formats.valid_arxiv("hep-th/0704001")
    assert formats.valid_ieee_doi("10.1109/ACCESS.2020.3012345") and formats.valid_ieee_doi("10.1109/5.771073")
    assert not formats.valid_ieee_doi("10.1110/abc") and not formats.valid_ieee_doi("10.1109/")
    assert formats.valid_orcid("0000-0002-1825-0097") and formats.valid_orcid("0000-0002-1694-233X")
    assert not formats.valid_orcid("0000-0002-1825-0098") and not formats.valid_orcid("0000000218250097")


def test_extraction_is_verbatim():
    text = (
        "Credential ID: ABC12345XYZ\nICAI Membership No: 123456\nIndependent Director at Example Industries Ltd\n"
        "DIN: 12345678\nSEBI RA INH000012345\nPatent No. US 10,123,456 B2\narXiv: 2101.01234\n"
        "doi: 10.1109/ACCESS.2020.3012345\nWon Design Excellence Award 2023\nCase study: Checkout redesign\n"
    )
    claims = extract_role_claims(text, candidate())
    assert claims.credential_ids[0].value == "ABC12345XYZ"
    assert (claims.memberships[0].body, claims.memberships[0].number) == ("ICAI", "123456")
    assert claims.directorships[0].company == "Example Industries Ltd"
    assert claims.dins[0].value == "12345678"
    assert claims.regulator_ids[0].value == "INH000012345"
    assert claims.patents[0].value == "US 10,123,456 B2"
    assert claims.arxiv_ids[0].value == "2101.01234"
    assert claims.ieee_dois[0].value == "10.1109/ACCESS.2020.3012345"
    assert "Design Excellence Award" in claims.awards[0].value
    assert "Checkout redesign" in claims.case_studies[0].value
    for group in (claims.credential_ids, claims.dins, claims.regulator_ids, claims.patents, claims.awards, claims.case_studies):
        for item in group:
            assert item.value in text and item.quote in text


def test_reference_loop_plan_creates_pending_record_without_sending():
    text = "Experience: Analytical Engines\nReference: Grace Hopper, Analytical Engines, grace@analytical-engines.example"
    references = pending_references(text, candidate())
    assert len(references) == 1
    assert references[0].reference_email == "grace@analytical-engines.example"
    assert references[0].status == "pending" and references[0].sent is False
    assert references[0].reference_name == "Grace Hopper"
    assert references[0].source_quote in text


def test_general_reference_is_candidate_and_freemail():
    text = "Reference: Ada Again, ada+ref@example.com\n"
    signals = run_role_checks("general", text, candidate(), env={})
    assert codes(signals) == {"REFERENCE_EMAIL_IS_CANDIDATE"}


def test_general_bad_cert_id_flagged_good_not():
    bad = run_role_checks("general", "Certificate ID: 111111", candidate(), env={})
    good = run_role_checks("general", "Certificate ID: ABC12345XYZ", candidate(), env={})
    assert codes(bad) == {"CERT_ID_INVALID_FORMAT"}
    assert "111111" in bad[0].detail
    assert good == []


def test_general_employer_domain_unreachable_and_reachable():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "rdap.org":
            return httpx.Response(404)
        if request.url.host == "good.example.org":
            return httpx.Response(200, text="ok")
        return httpx.Response(503)

    text = "Employer domain: good.example.org\nCompany website: bad.example.org\n"
    signals = run_role_checks("general", text, candidate(), transport=mock_client(handler), env=ENV)
    assert "DOMAIN_REACHABLE" in codes(signals)
    unreachable = [item for item in signals if item.code == "EMPLOYER_DOMAIN_UNREACHABLE"]
    assert [item.matched_claim for item in unreachable] == ["https://bad.example.org"]


def test_network_gated_by_env():
    text = "Employer domain: good.example.org\n"
    assert run_role_checks("general", text, candidate(), transport=mock_client(no_http), env={}) == []


def test_finance_membership_format_and_registry_results():
    assert run_role_checks("finance", "ICAI Membership No: 12345\n", candidate(), env={}) == []
    text = "ICAI Membership No: 123456\n"
    assert run_role_checks("finance", text, candidate(), registry=NullRegistry(), env=ENV) == []
    missing = StaticRegistry(membership=RegistryRecord(found=False))
    assert codes(run_role_checks("finance", text, candidate(), registry=missing, env=ENV)) == {"MEMBERSHIP_NOT_FOUND"}
    other = StaticRegistry(membership=RegistryRecord(found=True, name="Zed Quux"))
    assert codes(run_role_checks("finance", text, candidate(), registry=other, env=ENV)) == {"MEMBERSHIP_NAME_MISMATCH"}
    good = StaticRegistry(membership=RegistryRecord(found=True, name="Ada Synthetic"))
    signals = run_role_checks("finance", text, candidate(), registry=good, env=ENV)
    assert codes(signals) == {"MEMBERSHIP_VERIFIED"} and signals[0].polarity == "positive"


def test_registry_never_called_without_env():
    registry = StaticRegistry(membership=RegistryRecord(found=False))
    run_role_checks("finance", "ICAI Membership No: 123456\n", candidate(), registry=registry, env={})
    assert registry.calls == []


def test_finance_directorship_and_regulator():
    text = "Independent Director at Example Industries Ltd\nSEBI RA INH000012345\n"
    registry = StaticRegistry(directorship=RegistryRecord(found=False), regulator=RegistryRecord(found=True, name="Ada Synthetic"))
    signals = run_role_checks("finance", text, candidate(), registry=registry, env=ENV)
    assert codes(signals) == {"DIRECTORSHIP_NOT_FOUND", "REGULATOR_VERIFIED"}
    assert ("directorship", "Ada Synthetic", "Example Industries Ltd") in registry.calls
    unlisted = run_role_checks("finance", "SEBI INA000012345\n", candidate(), registry=StaticRegistry(regulator=RegistryRecord(found=False)), env=ENV)
    assert codes(unlisted) == {"REGULATOR_NOT_LISTED"}
    assert run_role_checks("finance", "Registration INA12345\n", candidate(), env={}) == []


def test_finance_din_invalid():
    assert run_role_checks("finance", "DIN: 12345\n", candidate(), env={}) == []


def test_hardware_patent_checks():
    text = "Patent No. US 10,123,456 B2\n"
    ok = StaticRegistry(patent=PatentRecord(found=True, inventors=("Ada Synthetic",)))
    assert codes(run_role_checks("hardware", text, candidate(), registry=ok, env=ENV)) == {"PATENT_VERIFIED"}
    wrong = StaticRegistry(patent=PatentRecord(found=True, inventors=("Zed Quux",)))
    assert codes(run_role_checks("hardware", text, candidate(), registry=wrong, env=ENV)) == {"PATENT_INVENTOR_MISMATCH"}
    absent = StaticRegistry(patent=PatentRecord(found=False))
    assert codes(run_role_checks("hardware", text, candidate(), registry=absent, env=ENV)) == {"PATENT_NOT_FOUND"}
    assert ok.calls == [("patent", "US10123456B2")]
    assert codes(run_role_checks("hardware", "Patent No. US 123456789\n", candidate(), env={})) == {"PATENT_NUMBER_INVALID_FORMAT"}
    assert codes(run_role_checks("hardware", "Patent No. WO 2020/12345\n", candidate(), env={})) == {"PATENT_NUMBER_INVALID_FORMAT"}
    assert run_role_checks("hardware", "Patent number: ZZ 99999\n", candidate(), env={}) == []
    assert run_role_checks("hardware", "Patent number: EP 12345\n", candidate(), env={}) == []


def test_hardware_arxiv_and_ieee_format():
    text = "arXiv: 2113.99999\ndoi: 10.1109/\n"
    assert "ARXIV_ID_INVALID_FORMAT" in codes(run_role_checks("hardware", text, candidate(), env={}))


def test_hardware_paper_and_repo_via_fake_transport():
    def handler(request: httpx.Request) -> httpx.Response:
        host = request.url.host
        if host == "api.github.com" and request.url.path == "/repos/ada-real/stm32-driver":
            return httpx.Response(200, json={"name": "stm32-driver", "description": "STM32 SPI driver", "language": "C", "topics": ["firmware"]})
        if host == "api.crossref.org":
            return httpx.Response(
                200,
                json={"message": {"items": [{"title": ["Low Power Sensor Nodes"], "author": [{"given": "Ada", "family": "Synthetic"}], "issued": {"date-parts": [[2021]]}, "URL": "https://doi.org/10.1109/x"}]}},
            )
        return httpx.Response(404, json={})

    text = (
        "GitHub: https://github.com/ada-real\nProject: https://github.com/ada-real/stm32-driver\n"
        "Paper: Low Power Sensor Nodes | authors: Ada Synthetic | 2021\n"
    )
    signals = run_role_checks("hardware", text, candidate(), transport=mock_client(handler), env=ENV)
    assert "HARDWARE_REPO_CORROBORATED" in codes(signals)
    assert "PAPER_CORROBORATED" in codes(signals)


def test_hardware_non_hardware_repo_not_corroborated():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "api.github.com":
            return httpx.Response(200, json={"name": "todo-app", "description": "A todo list", "language": "JavaScript", "topics": []})
        return httpx.Response(404)

    text = "GitHub: https://github.com/ada-real\nProject: https://github.com/ada-real/todo-app\n"
    signals = run_role_checks("hardware", text, candidate(), transport=mock_client(handler), env=ENV)
    assert "HARDWARE_REPO_CORROBORATED" not in codes(signals)


def portfolio_handler(status=200, body="Ada Synthetic portfolio. Won Design Excellence Award 2023"):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "ada.example.dev":
            return httpx.Response(status, text=body)
        return httpx.Response(404)

    return handler


PORTFOLIO_TEXT = "Portfolio: https://ada.example.dev\nWon Design Excellence Award 2023\nCase study: Checkout redesign\n"


def test_creative_portfolio_live_and_claims_found():
    signals = run_role_checks("sales_ops_design", PORTFOLIO_TEXT, candidate(), transport=mock_client(portfolio_handler()), env=ENV)
    assert {"PORTFOLIO_LIVE", "PORTFOLIO_NAME_MATCH", "CLAIM_FOUND_ON_PORTFOLIO"} <= codes(signals)
    assert all(item.polarity == "positive" for item in signals)
    claim = next(item for item in signals if item.code == "CLAIM_FOUND_ON_PORTFOLIO")
    assert claim.matched_claim in PORTFOLIO_TEXT


def test_creative_portfolio_dead_is_negative_and_awards_not_checked():
    signals = run_role_checks("sales_ops_design", PORTFOLIO_TEXT, candidate(), transport=mock_client(portfolio_handler(404)), env=ENV)
    assert codes(signals) == {"PORTFOLIO_UNREACHABLE"}


def test_creative_ignores_private_hosts():
    text = "Portfolio: http://127.0.0.1/me\n"
    assert run_role_checks("sales_ops_design", text, candidate(), transport=mock_client(no_http), env=ENV) == []


def test_unknown_profile_rejected():
    with pytest.raises(ValueError):
        run_role_checks("nope", "", candidate())
    with pytest.raises(ValueError):
        RoleConnector("nope", "", candidate())


def test_role_connector_plugs_into_enrich_runner():
    connector = RoleConnector("general", "Certificate ID: 111111\n", candidate(), env={})
    signals, summary = enrich(Claims(), connectors=[connector])
    assert codes(signals) == {"CERT_ID_INVALID_FORMAT"}
    assert summary.ran == [connector.name]


def test_role_reasons_weights_caps_and_trust():
    signals = [
        EnrichmentSignal(code="MEMBERSHIP_NOT_FOUND", polarity="negative", severity="high", confidence=1.0, source="t", detail="d"),
        EnrichmentSignal(code="PATENT_INVENTOR_MISMATCH", polarity="negative", severity="high", confidence=1.0, source="t", detail="d"),
        EnrichmentSignal(code="MEMBERSHIP_VERIFIED", polarity="positive", severity="info", confidence=1.0, source="t", detail="d"),
        EnrichmentSignal(code="GITHUB_CORROBORATED", polarity="positive", severity="info", confidence=1.0, source="t", detail="d"),
    ]
    reasons, trust = role_reasons(signals)
    assert [item["weight"] for item in reasons] == [15, 10]
    assert trust == 14
    uncapped, _ = role_reasons(signals, negative_cap=100)
    assert [item["weight"] for item in uncapped] == [15, 14]
    _, small = role_reasons(signals, bonus_cap=5)
    assert small == 5
