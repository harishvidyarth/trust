from __future__ import annotations

import pytest

from firewall.intake_routes import MAX_PDF_BYTES, normalise_public_https_url
from tests_intake.conftest import make_pdf


def post(client, files=None, **data):
    data.setdefault("consent", "true")
    return client.post("/v1/intake/profile", data=data, files=files)


def pdf_file(data: bytes, name: str = "profile.pdf"):
    return {"linkedin_pdf": (name, data, "application/pdf")}


def test_consent_required(client):
    assert post(client, consent="false", github_url="https://github.com/mayaraman").status_code == 400
    response = client.post("/v1/intake/profile", data={"github_url": "https://github.com/mayaraman"})
    assert response.status_code == 400


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/mayaraman",
        "ftp://github.com/mayaraman",
        "javascript:alert(1)",
        "https://user:pw@github.com/mayaraman",
        "https://127.0.0.1/x",
        "https://10.0.0.5/x",
        "https://169.254.169.254/latest/meta-data",
        "https://[::1]/x",
        "https://metadata.google.internal/x",
        "https://localhost/x",
        "https://2130706433/x",
        "https://evil.example.com/mayaraman",
    ],
)
def test_bad_github_urls_rejected(client, url):
    assert post(client, github_url=url).status_code in (400, 422)


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://192.168.1.1/",
        "https://169.254.169.254/",
        "https://printer.local/",
        "https://metadata/",
        "https://example.com:8443/",
        "https://example.com/#frag",
        "https://intranet/",
    ],
)
def test_bad_portfolio_urls_rejected(client, url):
    assert post(client, portfolio_url=url).status_code == 422


def test_bad_doi_and_certificate_rejected(client):
    assert post(client, doi_links=["https://evil.example.com/10.1234/x"]).status_code == 422
    assert post(client, doi_links=["not a doi"]).status_code == 422
    assert post(client, certificate_ids=["bad id with spaces"]).status_code == 422
    assert post(client, application_id="../etc").status_code == 422


def test_query_string_dropped():
    assert normalise_public_https_url("https://example.com/p?token=abc") == "https://example.com/p"


def test_oversized_upload_rejected(client):
    big = b"%PDF-1.4\n" + b"0" * (MAX_PDF_BYTES + 10)
    assert post(client, files=pdf_file(big)).status_code == 413


def test_non_pdf_upload_rejected(client):
    assert post(client, files=pdf_file(b"MZ not a pdf")).status_code == 400
    assert post(client, files=pdf_file(b"%PDF-1.4 garbage")).status_code == 400


def test_page_cap(client):
    assert post(client, files=pdf_file(make_pdf(["Maya Raman"], pages=11))).status_code == 413


def test_full_flow_and_grounding(client, linkedin_pdf, resume_store):
    response = post(
        client,
        files=pdf_file(linkedin_pdf),
        github_url="https://github.com/mayaraman?tab=repos",
        portfolio_url="https://maya.example.com/",
        doi_links=["https://doi.org/10.1234/sample.2024.01", "10.9999/not.in.inputs"],
        certificate_ids=["CERT-2024-0042", "CERT-9999-0000"],
        application_id="app-1",
    )
    assert response.status_code == 200
    body = response.json()
    assert body["consent_id"]
    inputs = "\n".join(
        [
            resume_store["app-1"],
            "https://github.com/mayaraman",
            "https://maya.example.com/",
            "10.1234/sample.2024.01",
            "10.9999/not.in.inputs",
            "CERT-2024-0042",
            "CERT-9999-0000",
            "Maya Raman",
        ]
    )
    assert body["findings"]
    for finding in body["findings"]:
        assert finding["fact"] in inputs
        assert finding["status"] in {"verified", "unverified", "mismatch", "disputed"}
    by_fact = {item["fact"]: item["status"] for item in body["findings"]}
    assert by_fact["mayaraman"] == "verified"
    assert by_fact["10.1234/sample.2024.01"] == "verified"
    assert by_fact["10.9999/not.in.inputs"] == "unverified"
    assert by_fact["CERT-2024-0042"] == "verified"
    assert by_fact["CERT-9999-0000"] == "unverified"
    assert by_fact["Maya Raman"] == "verified"
    assert "text" not in body["parsed"]


def test_github_mismatch(client, resume_store):
    response = post(client, github_url="https://github.com/someoneelse", application_id="app-1")
    assert response.status_code == 200
    assert response.json()["findings"][0]["status"] == "mismatch"


def test_no_invented_facts_without_inputs(client):
    response = post(client)
    assert response.status_code == 200
    assert response.json()["findings"] == []


def test_dispute_flow(client, resume_store):
    body = post(
        client,
        github_url="https://github.com/mayaraman",
        certificate_ids=["CERT-2024-0042"],
        application_id="app-1",
    ).json()
    consent_id = body["consent_id"]
    disputed = client.post(
        "/v1/intake/dispute", json={"consent_id": consent_id, "finding_index": 0, "note": "wrong account"}
    )
    assert disputed.status_code == 200
    assert disputed.json() == {"ok": True}
    record = client.get(f"/v1/intake/{consent_id}").json()
    assert record["findings"][0]["status"] == "disputed"
    assert record["findings"][1]["status"] != "disputed"
    assert client.get(f"/v1/intake/{consent_id}/recruiter").status_code == 403


def test_dispute_validation(client):
    consent_id = post(client, github_url="https://github.com/mayaraman").json()["consent_id"]
    assert client.post("/v1/intake/dispute", json={"consent_id": consent_id, "finding_index": 5, "note": ""}).status_code == 404
    assert client.post("/v1/intake/dispute", json={"consent_id": "nope", "finding_index": 0, "note": ""}).status_code == 404
    assert client.post("/v1/intake/dispute", json={"consent_id": consent_id, "finding_index": 0, "note": "x" * 501}).status_code == 422
    assert client.get("/v1/intake/unknown").status_code == 404


def test_recruiter_view_hides_disputed(resume_store):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from firewall.intake_routes import build_intake_router

    app = FastAPI()
    app.include_router(
        build_intake_router(
            recruiter_guard=lambda: None,
            resume_text_for=lambda key: resume_store.get(key, ""),
        )
    )
    client = TestClient(app)
    consent_id = post(
        client, github_url="https://github.com/mayaraman", certificate_ids=["CERT-2024-0042"], application_id="app-1"
    ).json()["consent_id"]
    client.post("/v1/intake/dispute", json={"consent_id": consent_id, "finding_index": 0, "note": "n"})
    view = client.get(f"/v1/intake/{consent_id}/recruiter").json()
    assert view["withheld_count"] == 1
    assert [item["fact"] for item in view["findings"]] == ["CERT-2024-0042"]


def test_guard_blocks_when_failing():
    from fastapi import FastAPI, HTTPException
    from fastapi.testclient import TestClient

    from firewall.intake_routes import build_intake_router

    def guard():
        raise HTTPException(status_code=401, detail="no")

    app = FastAPI()
    app.include_router(build_intake_router(guard=guard))
    client = TestClient(app)
    assert post(client).status_code == 401


def test_swappable_intel_service(linkedin_pdf):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from firewall.intake_routes import build_intake_router

    calls = []

    class Fake:
        def parse_linkedin_pdf(self, data):
            return {"text": "t", "name": "N"}

        def build_findings(self, resume_text, profile):
            return [{"source": "x", "fact": "t", "status": "unverified", "evidence": "e"}]

        def record_consent(self, application_id, scopes, **extra):
            return "cid-1"

        def dispute(self, consent_id, finding_index, note):
            calls.append((consent_id, finding_index))
            return True

    app = FastAPI()
    app.include_router(build_intake_router(Fake()))
    client = TestClient(app)
    assert post(client, files=pdf_file(linkedin_pdf)).json()["consent_id"] == "cid-1"
    assert client.post("/v1/intake/dispute", json={"consent_id": "cid-1", "finding_index": 0, "note": ""}).json() == {"ok": True}
    assert calls == [("cid-1", 0)]
