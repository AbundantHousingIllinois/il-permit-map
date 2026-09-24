#!/usr/bin/env python3
"""Fetch Illinois state legislative districts and their current members.

  cb_<vintage>_17_sldu_500k  -- State Senate districts (59).
  cb_<vintage>_17_sldl_500k  -- State House districts (118).
                                Both carry LSY 2024: the map adopted in 2021 and in
                                force from the 2022 election, the map the current
                                General Assembly was elected on. Same host and
                                vintage as the place file (SPEC.md §3.4).
  data.openstates.org/people/current/il.csv
                             -- Current legislators: name, party, district, email,
                                phones, links. A keyless bulk static file. Checked
                                2026-09-23: 177 rows, 59 upper and 118 lower,
                                each district exactly once.

The legislator file is a fetched snapshot, so it lives in data/raw/ like every
other download. A row the snapshot has wrong is corrected in
data/manual/legislator_overrides.csv, which is hand-maintained and never written
by a script. Nothing about a legislator is generated from a model's recollection.

Simplification runs here for the same reason fetch_geo.py's does: it needs
``npx mapshaper``. build.py has an offline fallback (simplify_geo.py).
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bps_layout as L
import fetch_geo as FG

OPENSTATES_URL = "https://data.openstates.org/people/current/il.csv"
# Drawn as outlines; finer than the county layer because the page zooms into
# Chicago, where a House district can be a few square miles.
DISTRICT_RETENTION = "10%"


def main() -> int:
    print("=" * 78)
    print("Fetching Illinois state legislative districts and legislators")
    print("=" * 78)
    for chamber, c in L.CHAMBERS.items():
        L.download(
            f"{FG.TIGER_BASE}/cb_{L.TIGER_VINTAGE}_{L.STATE_FIPS}_{c['zip_key']}_500k.zip",
            L.district_zip(chamber),
            f"TIGER cartographic boundary: Illinois {c['label']} districts, 1:500k "
            "(LSY 2024) -- legislator view",
        )
    L.download(
        OPENSTATES_URL,
        L.LEGISLATORS_CSV,
        "Open States bulk file, current Illinois legislators (name, party, "
        "district, contact) -- legislator view",
        force=True,       # "current" changes; the snapshot is re-taken on every fetch
    )

    import datetime
    L.LEGISLATORS_RETRIEVED.write_text(datetime.date.today().isoformat() + "\n",
                                       encoding="utf-8")
    with L.LEGISLATORS_CSV.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    print(f"\n  {len(rows)} legislators in {L.rel(L.LEGISLATORS_CSV)}")
    ok = True
    for chamber, c in L.CHAMBERS.items():
        ds = sorted(int(r["current_district"]) for r in rows
                    if r["current_chamber"] == c["openstates"])
        want = list(range(1, c["n"] + 1))
        good = ds == want
        ok = ok and good
        print(f"  {c['label']:<12} {len(ds)} rows, districts 1-{c['n']} each once: "
              f"{'yes' if good else 'NO'}")
    if not ok:
        print("  The snapshot does not have exactly one member per district. A vacancy "
              "or a data error: correct it in data/manual/legislator_overrides.csv.")

    print("\nSimplifying the district layers (outlines only):")
    for chamber, c in L.CHAMBERS.items():
        dest = L.district_geojson(chamber)
        FG.mapshaper([
            str(L.district_zip(chamber)),
            "-filter-fields", f"GEOID,NAME,{c['field']}",
            "-simplify", DISTRICT_RETENTION, "keep-shapes",
            "-o", f"precision={FG.COORD_PRECISION}", "format=geojson", "force",
            str(dest),
        ])
        n = len(json.loads(dest.read_text(encoding="utf-8"))["features"])
        print(f"  {n} {c['label']} districts in {L.rel(dest)} "
              f"({dest.stat().st_size:,} bytes)")
        L.record_source(
            f"npx {FG.MAPSHAPER} -simplify {DISTRICT_RETENTION} keep-shapes "
            f"-o precision={FG.COORD_PRECISION}",
            L.rel(dest),
            f"Simplification step, not a download. {c['label']} districts, retention "
            f"{DISTRICT_RETENTION}.",
        )
    print(f"\nProvenance appended to {L.rel(L.SOURCES_MD)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
