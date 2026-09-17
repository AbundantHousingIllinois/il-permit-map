#!/usr/bin/env python3
"""Fetch the Census Building Permits Survey flat files (SPEC.md §3.1).

BPS is not an API; it is a directory of comma-delimited flat files on
www2.census.gov.  Navigated from the landing page https://www.census.gov/construction/bps/:

  Place-level annual, Midwest region : /econ/bps/Place/Midwest Region/mw<YYYY>a.txt
  State-level annual                 : /econ/bps/State/st<YYYY>a.txt
  Record layout documentation        : /econ/bps/Documentation/placeasc.pdf
                                       /econ/bps/Documentation/stateasc.pdf

Illinois is in the Midwest region.  The state file is downloaded for two
reasons: its ``Illinois`` and ``United States`` rows are the published totals
SPEC.md §4 needs for ``il_pct_growth`` and ``us_pct_growth``, and it is an
independent check on whether the place-file column positions are being read
correctly (see check_data.py check A).

Idempotent: already-downloaded files are left alone.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bps_layout as L

BASE = "https://www2.census.gov/econ/bps"
PLACE_DIR = f"{BASE}/Place/Midwest%20Region"
STATE_DIR = f"{BASE}/State"
DOC_DIR = f"{BASE}/Documentation"


def main() -> int:
    print("=" * 78)
    print("Fetching Census Building Permits Survey flat files")
    print(f"years {L.YMIN}-{L.YMAX}   region {L.BPS_REGION}   state FIPS {L.STATE_FIPS}")
    print("=" * 78)

    print("\nRecord layout documentation (read, not inferred):")
    for name, note in (
        ("placeasc.pdf", "BPS place-file record layout (Attachment B: annual, 41 fields)"),
        ("stateasc.pdf", "BPS state-file record layout"),
    ):
        L.download(f"{DOC_DIR}/{name}", L.RAW_BPS_DOC / name, note)

    print(f"\nPlace-level annual files, Midwest region ({len(L.YEARS)} years):")
    for year in L.YEARS:
        L.download(
            f"{PLACE_DIR}/{L.BPS_REGION}{year}a.txt",
            L.place_file(year),
            f"BPS place-level annual, Midwest region, {year}",
        )

    print(f"\nState-level annual files ({len(L.YEARS)} years):")
    for year in L.YEARS:
        L.download(
            f"{STATE_DIR}/st{year}a.txt",
            L.state_file(year),
            f"BPS state-level annual (Illinois + United States totals), {year}",
        )

    # Confirm every file is parseable with a known layout before declaring success,
    # so a truncated download surfaces here rather than in the middle of the build.
    print("\nVerifying every downloaded place file parses with a known layout:")
    total = 0
    for year in L.YEARS:
        recs = L.read_place_year(year)
        total += sum(r["units_total"] for r in recs)
        print(f"  {year}  {len(recs):>5,} Illinois records  era={recs[0]['era'] if recs else '?'}")
    print(f"\nIllinois BPS units {L.YMIN}-{L.YMAX}: {total:,}")
    print(f"Provenance appended to {L.rel(L.SOURCES_MD)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
