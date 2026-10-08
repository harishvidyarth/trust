import json

from firewall.adaptive.auditlog import AuditLog
from firewall.models import Decision, Reason, Route


def decision(application_id: str) -> Decision:
    return Decision(
        application_id=application_id,
        score=55,
        route=Route.ADDITIONAL_VERIFICATION,
        reasons=[Reason(code="FAST_SUBMIT", severity="medium", detail="fast", weight=12)],
        summary="verification required",
    )


def test_valid_chain_and_tamper_detection_in_memory():
    log = AuditLog()
    log.append(decision("a1"), version="weights-v1")
    log.append(decision("a2"), version="weights-v1", actor="recruiter:r1")
    assert log.verify_chain() is None

    log.entries[0]["score"] = 99
    assert log.verify_chain() == 0


def test_file_chain_detects_removal_and_reordering(tmp_path):
    path = tmp_path / "audit.jsonl"
    log = AuditLog(path)
    log.append(decision("a1"), version="v1")
    log.append(decision("a2"), version="v1")
    log.append(decision("a3"), version="v1")

    lines = path.read_text(encoding="utf-8").splitlines()
    path.write_text("\n".join([lines[1], lines[0], lines[2]]) + "\n", encoding="utf-8")
    assert log.verify_chain() == 0

    path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    assert log.verify_chain() == 2


def test_entry_is_canonical_json_serializable():
    log = AuditLog()
    entry = log.append(decision("a1"), config_version="config-v2", weights_version="weights-v4")
    assert json.loads(json.dumps(entry))["reason_codes"] == ["FAST_SUBMIT"]
    assert entry["config_version"] == "config-v2"
    assert entry["weights_version"] == "weights-v4"
