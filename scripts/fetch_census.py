#!/usr/bin/env python3
"""Fetch the Decennial Census tables (SPEC.md §3.2, §3.3).

  H1 / H001001, 2010 SF1  -- total housing units, the pct_growth DENOMINATOR.
                             The April 1, 2010 complete count, not an estimate.
  P1 / P1_001N, 2020 PL   -- total population, used only for the size filters.
  H1 / H1_001N, 2020 DHC  -- total housing units, April 1, 2020. Used ONLY for the
                             permits-vs-built comparison (CLAUDE.md deviation 27).
                             It is never a denominator: pct_growth stays on the
                             2010 complete count, as SPEC.md §3.2 requires.
                             Checked 2026-09-23: the 2020 PL file's H1_001N
                             returns identical figures for every Illinois place,
                             the state and the U.S.; DHC is used because its H1 is
                             the direct "HOUSING UNITS" successor of SF1 H1.

Verified live on 2026-09-16, so the FTP fallback described in SPEC.md §3.2 is
not needed and no ACS estimate is substituted anywhere.

The API key is read from the environment variable ``CENSUS_API_KEY``; it is
never written to disk and the URL recorded in data/SOURCES.md has no key on it.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bps_layout as L

API = "https://api.census.gov/data"

QUERIES = [
    (
        f"{API}/2010/dec/sf1?get=NAME,H001001&for=place:*&in=state:{L.STATE_FIPS}",
        L.RAW_CENSUS / "h1_2010_place_17.json",
        "2010 Decennial SF1 table H1 (H001001) total housing units, Illinois places "
        "-- pct_growth denominator",
    ),
    (
        f"{API}/2010/dec/sf1?get=NAME,H001001&for=state:{L.STATE_FIPS}",
        L.RAW_CENSUS / "h1_2010_state_17.json",
        "2010 Decennial SF1 H001001, Illinois statewide -- il_pct_growth denominator",
    ),
    (
        f"{API}/2010/dec/sf1?get=NAME,H001001&for=us:1",
        L.RAW_CENSUS / "h1_2010_us.json",
        "2010 Decennial SF1 H001001, United States -- us_pct_growth denominator",
    ),
    (
        f"{API}/2020/dec/pl?get=NAME,P1_001N&for=place:*&in=state:{L.STATE_FIPS}",
        L.RAW_CENSUS / "p1_2020_place_17.json",
        "2020 Decennial PL table P1 (P1_001N) total population, Illinois places "
        "-- size filters only",
    ),
    (
        f"{API}/2020/dec/dhc?get=NAME,H1_001N&for=place:*&in=state:{L.STATE_FIPS}",
        L.RAW_CENSUS / "h1_2020_place_17.json",
        "2020 Decennial DHC table H1 (H1_001N) total housing units, Illinois places "
        "-- permits-vs-built comparison only, never a denominator",
    ),
    (
        f"{API}/2020/dec/dhc?get=NAME,H1_001N&for=state:{L.STATE_FIPS}",
        L.RAW_CENSUS / "h1_2020_state_17.json",
        "2020 Decennial DHC H1_001N, Illinois statewide -- permits-vs-built reference",
    ),
    (
        f"{API}/2020/dec/dhc?get=NAME,H1_001N&for=us:1",
        L.RAW_CENSUS / "h1_2020_us.json",
        "2020 Decennial DHC H1_001N, United States -- permits-vs-built reference",
    ),
    (
        f"{API}/2010/dec/sf1?get=NAME&for=county:*&in=state:{L.STATE_FIPS}",
        L.RAW_CENSUS / "county_names_17.json",
        "County names for the Illinois county FIPS codes carried on BPS records "
        "(same 2010 SF1 dataset as §3.2; no new source)",
    ),
]


def main() -> int:
    print("=" * 78)
    print("Fetching Decennial Census tables")
    print("=" * 78)
    for url, dest, note in QUERIES:
        L.census_api(url, dest, note)

    print("\nVerifying what came back:")
    places = L.read_census_json(L.RAW_CENSUS / "h1_2010_place_17.json")
    pops = L.read_census_json(L.RAW_CENSUS / "p1_2020_place_17.json")
    il = L.read_census_json(L.RAW_CENSUS / "h1_2010_state_17.json")
    us = L.read_census_json(L.RAW_CENSUS / "h1_2010_us.json")
    counties = L.read_census_json(L.RAW_CENSUS / "county_names_17.json")
    h20 = L.read_census_json(L.RAW_CENSUS / "h1_2020_place_17.json")
    il20 = L.read_census_json(L.RAW_CENSUS / "h1_2020_state_17.json")
    us20 = L.read_census_json(L.RAW_CENSUS / "h1_2020_us.json")

    zero_h1 = [r for r in places if int(r["H001001"]) <= 0]
    print(f"  2010 H1, Illinois places : {len(places):,} places, "
          f"{sum(int(r['H001001']) for r in places):,} housing units")
    print(f"     of which H001001 <= 0 : {len(zero_h1)}  "
          "(these get a null pct_growth, never a divide-by-zero)")
    print(f"  2010 H1, Illinois total  : {int(il[0]['H001001']):,}")
    print(f"  2010 H1, United States   : {int(us[0]['H001001']):,}")
    print(f"  2020 P1, Illinois places : {len(pops):,} places, "
          f"{sum(int(r['P1_001N']) for r in pops):,} people")
    print(f"  County names             : {len(counties)} Illinois counties")
    print(f"  2020 H1, Illinois places : {len(h20):,} places, "
          f"{sum(int(r['H1_001N']) for r in h20):,} housing units")
    print(f"  2020 H1, Illinois total  : {int(il20[0]['H1_001N']):,}")
    print(f"  2020 H1, United States   : {int(us20[0]['H1_001N']):,}")
    print(f"\nProvenance appended to {L.rel(L.SOURCES_MD)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
