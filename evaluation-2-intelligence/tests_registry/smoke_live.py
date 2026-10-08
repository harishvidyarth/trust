from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from firewall.enrichment.roles.live import USPTO_KEY_ENV, OrcidRegistry, SafeFetcher, UsptoOdpRegistry

ORCID_SAMPLE = "0000-0002-1825-0097"
PATENT_SAMPLE = "US7000000B2"


def main() -> int:
    if os.environ.get("FIREWALL_SMOKE_LIVE") != "1":
        print("skipped, set FIREWALL_SMOKE_LIVE=1 to run at most 2 live calls")
        return 0
    fetcher = SafeFetcher()
    record = OrcidRegistry(fetcher).orcid(ORCID_SAMPLE)
    if record is None:
        print("orcid: unknown (no usable answer)")
        orcid_ok = False
    else:
        print(f"orcid: found={record.found} name_present={record.name is not None} aliases={len(record.aliases)}")
        orcid_ok = record.found
    key = os.environ.get(USPTO_KEY_ENV, "").strip()
    if not key:
        print(f"patent: skipped, {USPTO_KEY_ENV} is not set")
        return 0 if orcid_ok else 1
    patent = UsptoOdpRegistry(fetcher, key).patent(PATENT_SAMPLE)
    if patent is None:
        print("patent: unknown (no usable answer)")
        return 1
    print(f"patent: found={patent.found} title_present={patent.title is not None} inventors={len(patent.inventors)}")
    return 0 if orcid_ok and patent.found else 1


if __name__ == "__main__":
    sys.exit(main())
