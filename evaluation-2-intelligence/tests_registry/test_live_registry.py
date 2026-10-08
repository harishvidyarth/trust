from __future__ import annotations

import httpx

from firewall.enrichment.roles import LiveRegistry, OrcidRegistry, PatentRecord, RegistryRecord, SafeFetcher, UsptoOdpRegistry, run_role_checks
from firewall.enrichment.roles.live import MAX_BYTES, USPTO_KEY_ENV
from tests_intel.conftest import make_application

ORCID = "0000-0002-1825-0097"
ENV = {"FIREWALL_ENRICH": "1"}

PERSON = {
    "last-modified-date": {"value": 1700000000000},
    "name": {
        "created-date": {"value": 1500000000000},
        "given-names": {"value": "Ada"},
        "family-name": {"value": "Synthetic"},
        "credit-name": {"value": "A. Synthetic"},
        "visibility": "public",
        "path": ORCID,
    },
    "other-names": {"other-name": [{"content": "Ada S.", "visibility": "public"}], "path": "/" + ORCID + "/other-names"},
    "path": "/" + ORCID + "/person",
}
PRIVATE_PERSON = {"name": None, "other-names": {"other-name": []}, "path": "/" + ORCID + "/person"}
PATENT_BODY = {
    "count": 1,
    "patentFileWrapperDataBag": [
        {
            "applicationMetaData": {
                "patentNumber": "10123456",
                "inventionTitle": "Synthetic sensor housing",
                "inventorBag": [
                    {"firstName": "Ada", "lastName": "Synthetic", "inventorNameText": "Ada Synthetic"},
                    {"firstName": "Zed", "lastName": "Quux"},
                ],
            }
        }
    ],
}


def json_response(payload, status=200, headers=None):
    return httpx.Response(status, json=payload, headers=headers)


def client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def candidate(name="Ada Synthetic"):
    return make_application(name=name, email="ada@example.com").candidate


def test_orcid_found_with_name_and_aliases():
    seen = []

    def handler(request):
        seen.append(request)
        return json_response(PERSON)

    record = OrcidRegistry(SafeFetcher(client(handler))).orcid(ORCID)
    assert record == RegistryRecord(found=True, name="Ada Synthetic", url="https://orcid.org/" + ORCID, aliases=("A. Synthetic", "Ada S."))
    request = seen[0]
    assert request.url.host == "pub.orcid.org" and request.url.path == f"/v3.0/{ORCID}/person"
    assert request.headers["accept"] == "application/json"
    assert "AI-Application-Firewall" in request.headers["user-agent"]


def test_orcid_private_name_found_without_name():
    record = OrcidRegistry(SafeFetcher(client(lambda r: json_response(PRIVATE_PERSON)))).orcid(ORCID)
    assert record.found and record.name is None and record.aliases == ()


def test_orcid_missing_is_not_found_and_other_statuses_are_unknown():
    assert OrcidRegistry(SafeFetcher(client(lambda r: json_response({"developer-message": "not found"}, 404)))).orcid(ORCID).found is False
    for status in (401, 409, 429, 500, 503):
        assert OrcidRegistry(SafeFetcher(client(lambda r, s=status: httpx.Response(s)))).orcid(ORCID) is None


def test_orcid_bad_checksum_never_hits_network():
    def handler(request):
        raise AssertionError("network used")

    assert OrcidRegistry(SafeFetcher(client(handler))).orcid("0000-0002-1825-0098") is None


def test_orcid_schema_violations_are_unknown():
    for payload in ([], "text", {"name": "Ada"}, {"name": [1]}):
        assert OrcidRegistry(SafeFetcher(client(lambda r, p=payload: json_response(p)))).orcid(ORCID) is None
    assert OrcidRegistry(SafeFetcher(client(lambda r: httpx.Response(200, text="<html>", headers={"content-type": "text/html"})))).orcid(ORCID) is None
    assert OrcidRegistry(SafeFetcher(client(lambda r: httpx.Response(200, content=b"{not json", headers={"content-type": "application/json"})))).orcid(ORCID) is None


def test_orcid_size_cap_declared_and_streamed():
    big = b'{"name": null, "pad": "' + b"x" * (MAX_BYTES + 10) + b'"}'
    assert OrcidRegistry(SafeFetcher(client(lambda r: httpx.Response(200, content=big, headers={"content-type": "application/json"})))).orcid(ORCID) is None
    declared = httpx.Response(200, json=PERSON, headers={"content-length": str(MAX_BYTES + 1)})
    assert OrcidRegistry(SafeFetcher(client(lambda r: declared))).orcid(ORCID) is None


def test_network_errors_and_timeouts_are_unknown():
    def boom(request):
        raise httpx.ConnectTimeout("slow")

    assert OrcidRegistry(SafeFetcher(client(boom))).orcid(ORCID) is None


def test_timeout_is_four_seconds():
    seen = []

    def handler(request):
        seen.append(request.extensions["timeout"])
        return json_response(PERSON)

    OrcidRegistry(SafeFetcher(client(handler))).orcid(ORCID)
    assert seen[0]["read"] == 4.0 and seen[0]["connect"] == 4.0


def test_redirects_are_not_followed():
    calls = []

    def handler(request):
        calls.append(request.url.host)
        return httpx.Response(302, headers={"location": "https://evil.example/x"})

    assert OrcidRegistry(SafeFetcher(client(handler))).orcid(ORCID) is None
    assert calls == ["pub.orcid.org"]


def test_fetcher_refuses_other_hosts_and_plain_http():
    def handler(request):
        raise AssertionError("network used")

    fetcher = SafeFetcher(client(handler))
    assert fetcher.get_json("https://evil.example/x") is None
    assert fetcher.get_json("http://pub.orcid.org/v3.0/x") is None
    assert fetcher.get_json("https://pub.orcid.org.evil.example/x") is None


def test_cache_hits_and_expiry():
    now = [0.0]
    count = [0]

    def handler(request):
        count[0] += 1
        return json_response(PERSON)

    registry = OrcidRegistry(SafeFetcher(client(handler), ttl=10, clock=lambda: now[0]))
    registry.orcid(ORCID)
    registry.orcid(ORCID)
    assert count[0] == 1
    now[0] = 11.0
    registry.orcid(ORCID)
    assert count[0] == 2


def test_unknown_results_are_not_cached():
    count = [0]

    def handler(request):
        count[0] += 1
        return httpx.Response(503)

    registry = OrcidRegistry(SafeFetcher(client(handler)))
    registry.orcid(ORCID)
    registry.orcid(ORCID)
    assert count[0] == 2


def test_uspto_found_with_inventors_and_key_header():
    seen = []

    def handler(request):
        seen.append(request)
        return json_response(PATENT_BODY)

    record = UsptoOdpRegistry(SafeFetcher(client(handler)), "synthetic-key").patent("US10123456B2")
    assert record.found and record.title == "Synthetic sensor housing"
    assert record.inventors == ("Ada Synthetic", "Zed Quux")
    request = seen[0]
    assert request.url.host == "api.uspto.gov" and request.headers["x-api-key"] == "synthetic-key"
    assert "10123456" in str(request.url)


def test_uspto_not_returned_or_mismatched_is_unknown_never_not_found():
    empty = {"count": 0, "patentFileWrapperDataBag": []}
    assert UsptoOdpRegistry(SafeFetcher(client(lambda r: json_response(empty))), "k").patent("US10123456") is None
    other = {"patentFileWrapperDataBag": [{"applicationMetaData": {"patentNumber": "10999999"}}]}
    assert UsptoOdpRegistry(SafeFetcher(client(lambda r: json_response(other))), "k").patent("US10123456") is None
    for payload in ([], {"patentFileWrapperDataBag": "x"}, {"patentFileWrapperDataBag": [1]}):
        assert UsptoOdpRegistry(SafeFetcher(client(lambda r, p=payload: json_response(p))), "k").patent("US10123456") is None


def test_uspto_skips_non_us_numbers_and_missing_key():
    def handler(request):
        raise AssertionError("network used")

    registry = UsptoOdpRegistry(SafeFetcher(client(handler)), "k")
    for number in ("EP1234567A1", "WO2020123456", "IN201841000123", "US2020012345A1", "garbage"):
        assert registry.patent(number) is None
    assert UsptoOdpRegistry(SafeFetcher(client(handler)), "").patent("US10123456") is None


def test_uspto_401_is_unknown():
    assert UsptoOdpRegistry(SafeFetcher(client(lambda r: httpx.Response(401))), "bad").patent("US10123456") is None


def test_live_registry_routing_and_key_gate():
    def handler(request):
        if request.url.host == "pub.orcid.org":
            return json_response(PERSON)
        return json_response(PATENT_BODY)

    without = LiveRegistry(client(handler), env={})
    assert without.orcid(ORCID).found
    assert without.patent("US10123456") is None
    assert without.membership("ICAI", "123456") is None and without.directorship("A", "B") is None and without.regulator("INA000012345") is None
    with_key = LiveRegistry(client(handler), env={USPTO_KEY_ENV: "k"})
    assert isinstance(with_key.patent("US10123456"), PatentRecord)


def run(text, handler, name="Ada Synthetic", profile="general"):
    return run_role_checks(profile, text, candidate(name), transport=client(handler), env=ENV)


def test_pipeline_orcid_verified_and_mismatch_and_not_found():
    text = f"ORCID: {ORCID}\n"
    assert {s.code for s in run(text, lambda r: json_response(PERSON))} == {"ORCID_VERIFIED"}
    assert {s.code for s in run(text, lambda r: json_response(PERSON), name="Zed Quux")} == {"ORCID_NAME_MISMATCH"}
    assert {s.code for s in run(text, lambda r: json_response({}, 404))} == {"ORCID_NOT_FOUND"}
    assert run(text, lambda r: httpx.Response(503)) == []
    assert run(text, lambda r: json_response(PRIVATE_PERSON)) == []


def test_pipeline_orcid_alias_matches():
    signals = run(f"ORCID: {ORCID}\n", lambda r: json_response(PERSON), name="Ada S.")
    assert {s.code for s in signals} == {"ORCID_VERIFIED"}


def test_pipeline_orcid_bad_checksum_flagged_offline_too():
    signals = run_role_checks("general", "ORCID: 0000-0002-1825-0098\n", candidate(), env={})
    assert {s.code for s in signals} == {"ORCID_ID_INVALID_FORMAT"}


def test_pipeline_no_network_when_env_off():
    def handler(request):
        raise AssertionError("network used")

    assert run_role_checks("general", f"ORCID: {ORCID}\n", candidate(), transport=client(handler), env={}) == []


def test_pipeline_patent_with_key_uses_uspto_and_matches_inventor():
    def handler(request):
        return json_response(PATENT_BODY)

    env = {**ENV, USPTO_KEY_ENV: "k"}
    text = "Patent No. US 10,123,456 B2\n"
    signals = run_role_checks("hardware", text, candidate(), transport=client(handler), env=env)
    assert {s.code for s in signals} == {"PATENT_VERIFIED"}
    other = run_role_checks("hardware", text, candidate("Mia Other"), transport=client(handler), env=env)
    assert {s.code for s in other} == {"PATENT_INVENTOR_MISMATCH"}
    keyless = run_role_checks("hardware", text, candidate(), transport=client(handler), env=ENV)
    assert keyless == []
