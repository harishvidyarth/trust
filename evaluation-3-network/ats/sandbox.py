from __future__ import annotations

import argparse
import os
import secrets
import threading
from collections.abc import Mapping
from typing import Annotated, Any

import uvicorn
from fastapi import Depends, FastAPI, HTTPException
from fastapi.security import HTTPBasic, HTTPBasicCredentials

from ats.ranker import rank_candidates
from firewall.models import JobRequirements


SANDBOX_NOTICE = "ATS sandbox simulator; not Greenhouse or Lever"
_basic = HTTPBasic(auto_error=False)


def create_app(api_key: str | None = None) -> FastAPI:
    configured_key = api_key or os.getenv("ATS_SANDBOX_API_KEY", "sandbox-key")
    app = FastAPI(
        title="ATS Sandbox Simulator",
        description="A local test double exposing a small Greenhouse/Lever-shaped API subset.",
        version="0.1.0",
    )
    candidates: list[dict[str, Any]] = []
    applications: list[dict[str, Any]] = []
    lock = threading.RLock()

    def authenticate(
        credentials: Annotated[HTTPBasicCredentials | None, Depends(_basic)],
    ) -> None:
        if credentials is None or not secrets.compare_digest(
            credentials.username.encode("utf-8"), configured_key.encode("utf-8")
        ):
            raise HTTPException(
                status_code=401,
                detail=f"unauthorized: {SANDBOX_NOTICE}",
                headers={"WWW-Authenticate": "Basic"},
            )

    def store_candidate(payload: Mapping[str, Any], provider_shape: str) -> dict[str, Any]:
        with lock:
            candidate_id = str(payload.get("id") or f"sandbox-candidate-{len(candidates) + 1}")
            first_name = str(payload.get("first_name", "")).strip()
            last_name = str(payload.get("last_name", "")).strip()
            name = str(payload.get("name") or f"{first_name} {last_name}".strip())
            record = {
                **dict(payload),
                "id": candidate_id,
                "name": name,
                "provider_shape": provider_shape,
                "sandbox_simulator": True,
            }
            candidates.append(record)

            submitted = payload.get("applications", [])
            if not isinstance(submitted, list):
                submitted = []
            if not submitted and payload.get("posting") is not None:
                submitted = [{"job_id": payload["posting"]}]
            if not submitted and payload.get("job_id") is not None:
                submitted = [{"job_id": payload["job_id"]}]

            for raw in submitted:
                item = dict(raw) if isinstance(raw, Mapping) else {"job_id": raw}
                applications.append(
                    {
                        **item,
                        "id": str(item.get("id") or f"sandbox-application-{len(applications) + 1}"),
                        "candidate_id": candidate_id,
                        "resume_text": str(payload.get("resume_text", "")),
                        "name": name,
                        "provider_shape": provider_shape,
                        "sandbox_simulator": True,
                    }
                )
            return record

    @app.post("/v1/candidates", dependencies=[Depends(authenticate)])
    def create_greenhouse_candidate(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "notice": SANDBOX_NOTICE,
            "candidate": store_candidate(payload, "greenhouse-harvest-subset"),
        }

    @app.get("/v1/candidates", dependencies=[Depends(authenticate)])
    def list_greenhouse_candidates() -> dict[str, Any]:
        with lock:
            return {"notice": SANDBOX_NOTICE, "candidates": list(candidates)}

    @app.get("/v1/applications", dependencies=[Depends(authenticate)])
    def list_greenhouse_applications() -> dict[str, Any]:
        with lock:
            return {"notice": SANDBOX_NOTICE, "applications": list(applications)}

    @app.post("/lever/v1/candidates", dependencies=[Depends(authenticate)])
    @app.post("/lever/v1/opportunities", dependencies=[Depends(authenticate)])
    def create_lever_candidate(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "notice": SANDBOX_NOTICE,
            "candidate": store_candidate(payload, "lever-like-subset"),
        }

    @app.get("/v1/ranking", dependencies=[Depends(authenticate)])
    def get_ranking(job_id: str) -> dict[str, Any]:
        with lock:
            matching = [item for item in applications if str(item.get("job_id")) == job_id]
        if not matching:
            raise HTTPException(status_code=404, detail=f"job {job_id!r} has no sandbox applications")

        source = matching[0]
        requirements = JobRequirements(
            must_have_skills=source.get("must_have_skills", []),
            nice_to_have=source.get("nice_to_have", []),
            min_years=source.get("min_years", 0),
        )
        return {
            "notice": SANDBOX_NOTICE,
            "warning": "Deliberately naive keyword ranker; not a model of any vendor's proprietary ranking.",
            "job_id": job_id,
            "ranking": rank_candidates(matching, requirements),
        }

    @app.get("/healthz")
    def healthcheck() -> dict[str, Any]:
        return {"status": "ok", "sandbox_simulator": True, "notice": SANDBOX_NOTICE}

    return app


app = create_app()


def main() -> None:
    parser = argparse.ArgumentParser(description=SANDBOX_NOTICE)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8090)
    args = parser.parse_args()
    uvicorn.run("ats.sandbox:app", host=args.host, port=args.port, reload=False)


if __name__ == "__main__":
    main()
