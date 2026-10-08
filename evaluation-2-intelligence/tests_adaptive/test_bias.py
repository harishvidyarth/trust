from firewall.adaptive.bias import cohort_markdown_table, impact_ratio_report
from firewall.models import Route


def test_impact_ratio_flags_below_four_fifths():
    records = [
        {"group": "A", "route": Route.PASS_TO_ATS},
        {"group": "A", "route": Route.PASS_TO_ATS},
        {"group": "B", "route": Route.PASS_TO_ATS},
        {"group": "B", "route": Route.MANUAL_REVIEW},
        {"group": "B", "route": Route.MANUAL_REVIEW},
        {"group": "B", "route": Route.ADDITIONAL_VERIFICATION},
    ]
    report = impact_ratio_report(records, "group")

    by_group = {row["group"]: row for row in report["groups"]}
    assert by_group["A"]["selection_rate"] == 1.0
    assert by_group["B"]["impact_ratio"] == 0.25
    assert by_group["B"]["flagged"] is True


def test_cohort_slicing_produces_markdown_table():
    records = [
        {"phrasing": "native", "education": "degree", "route": "PASS_TO_ATS"},
        {"phrasing": "non-native", "education": "bootcamp", "route": "MANUAL_REVIEW"},
    ]
    table = cohort_markdown_table(records, ["phrasing", "education"])
    assert "| Cohort | Group |" in table
    assert "| phrasing | non-native |" in table
