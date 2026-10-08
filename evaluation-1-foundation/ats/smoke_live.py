from __future__ import annotations

import argparse
import sys

import httpx

from firewall.connectors.boards import GreenhouseJobBoardClient, to_job_requirements


def main() -> int:
    parser = argparse.ArgumentParser(description="Opt-in live Greenhouse public-board smoke test")
    parser.add_argument("--live", action="store_true", help="allow a real read-only network request")
    parser.add_argument("--board", default="discord", help="public Greenhouse board token")
    args = parser.parse_args()
    if not args.live:
        print("Network disabled. Re-run with --live to query a public Greenhouse board.")
        return 2

    try:
        with httpx.Client(follow_redirects=True) as client:
            board = GreenhouseJobBoardClient(args.board, transport=client, timeout=15.0)
            jobs = board.list_jobs()
            if not jobs:
                print(f"board={args.board} jobs=0")
                return 1
            job = board.get_job(jobs[0]["id"])
            requirements = to_job_requirements(job)
            print(f"board={args.board} jobs={len(jobs)}")
            print(f"job_id={job.get('id')} title={job.get('title', '')}")
            print(f"requirements={requirements.model_dump()}")
            return 0
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
        print(f"live smoke failed: {type(error).__name__}: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
