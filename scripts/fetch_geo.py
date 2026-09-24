#!/usr/bin/env python3
"""Fetch and simplify TIGER cartographic boundaries (SPEC.md §3.4).

  cb_<vintage>_17_place_500k   -- Illinois places, the map's polygons.
  cb_<vintage>_us_county_500k  -- county outlines, filtered to Illinois. Counties
                                  are published only as a national file at this
                                  vintage (verified against the GENZ2025/shp
                                  directory listing), so the state filter happens
                                  here. SPEC.md §6.1 allows rendering
                                  on a plain background with county outlines when
                                  no keyless basemap is usable, which is the choice
                                  this build makes (see README). The county file is
                                  also how a county name is attached to a place
                                  that has no BPS record to read one from.
  cb_<vintage>_us_state_500k   -- the Illinois outline, filtered from the national
                                  file (states, like counties, are national-only).
                                  Drawn as a silhouette beneath the places so the
                                  unincorporated land between them reads as
                                  territory rather than as a hole in the map.

Simplification runs here, in the fetch step, because it needs ``npx mapshaper``
and therefore the network; SPEC.md §8 requires ``build.py`` to run with no network
access.  Output goes to ``data/processed/`` and is what ``build.py`` consumes.

mapshaper's ``-simplify`` is topology-aware: shared borders between neighbouring
places are simplified identically, so no slivers or gaps open up between them.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bps_layout as L

TIGER_BASE = f"https://www2.census.gov/geo/tiger/GENZ{L.TIGER_VINTAGE}/shp"
MAPSHAPER = "mapshaper@0.6.109"          # pinned

COUNTY_ZIP = L.RAW_GEO / f"cb_{L.TIGER_VINTAGE}_us_county_500k.zip"
COUNTIES_GEOJSON = L.PROCESSED / "counties_simplified.geojson"

# SPEC.md §3.4: "Start around 5% retention and adjust."  5% produced 0.51 MB of
# geometry -- far under the 3 MB budget -- and visibly faceted small villages, so
# the ladder is adjusted upward and steps DOWN only if the budget is not met.  The
# value actually used is printed and recorded in data/SOURCES.md.
RETENTION_LADDER = ["12%", "10%", "8%", "5%", "3%", "2%", "1%"]
# The built places.geojson adds ~20 numeric properties per feature on top of the
# geometry, so the simplified geometry is held well under the 3 MB budget.
GEOMETRY_BUDGET_BYTES = 2_100_000
COORD_PRECISION = "0.00001"              # ~5 decimal places (SPEC.md §3.4)
# One polygon with a long river boundary. Its edge is drawn over the county
# lines, so it is kept finer than the county layer's 3% to stay crisp at the
# state-wide zoom the page opens at.
STATE_RETENTION = "10%"


def mapshaper(args: list[str]) -> None:
    cmd = ["npx", "-y", MAPSHAPER] + args
    print(f"  $ {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(
            "mapshaper failed:\n"
            + "\n".join((proc.stdout + proc.stderr).splitlines()[-25:])
        )
    for line in (proc.stdout + proc.stderr).splitlines():
        if line.strip() and "deprecated" not in line and "npm warn" not in line:
            print(f"    {line.rstrip()}")


def simplify_places() -> str:
    """Simplify the place layer, stepping retention down until it fits."""
    for retention in RETENTION_LADDER:
        mapshaper([
            str(L.TIGER_ZIP),
            "-filter-fields", "GEOID,NAME,NAMELSAD,ALAND,AWATER",
            # keep-shapes stops a tiny place collapsing to nothing. `-clean` is
            # deliberately NOT used: it removes sliver polygons, and on this layer
            # it deleted one whole place (1730835), which would break the 1:1
            # correspondence with TIGER that check_data.py check 1 requires.
            "-simplify", retention, "keep-shapes",
            "-o", f"precision={COORD_PRECISION}", "format=geojson", "force",
            str(L.SIMPLIFIED_GEOJSON),
        ])
        size = L.SIMPLIFIED_GEOJSON.stat().st_size
        print(f"  retention {retention:>5}  ->  {size:,} bytes "
              f"({size / 1024 / 1024:.2f} MB), geometry budget "
              f"{GEOMETRY_BUDGET_BYTES / 1024 / 1024:.2f} MB")
        if size <= GEOMETRY_BUDGET_BYTES:
            return retention
    raise RuntimeError(
        "Could not get the simplified place geometry under "
        f"{GEOMETRY_BUDGET_BYTES:,} bytes even at {RETENTION_LADDER[-1]} retention. "
        "SPEC.md §10 says to note this in BLOCKERS.md before reaching for PMTiles."
    )


def main() -> int:
    print("=" * 78)
    print(f"Fetching TIGER cartographic boundary files (vintage {L.TIGER_VINTAGE})")
    print("=" * 78)
    L.download(
        f"{TIGER_BASE}/cb_{L.TIGER_VINTAGE}_{L.STATE_FIPS}_place_500k.zip",
        L.TIGER_ZIP,
        "TIGER cartographic boundary: Illinois places, 1:500k",
    )
    L.download(
        f"{TIGER_BASE}/cb_{L.TIGER_VINTAGE}_us_county_500k.zip",
        COUNTY_ZIP,
        "TIGER cartographic boundary: US counties, 1:500k, filtered to Illinois "
        "(county outlines basemap + county names)",
    )
    L.download(
        f"{TIGER_BASE}/cb_{L.TIGER_VINTAGE}_us_state_500k.zip",
        L.STATE_ZIP,
        "TIGER cartographic boundary: US states, 1:500k, filtered to Illinois "
        "(state silhouette and outline; its bbox sets the initial map view)",
    )

    print("\nTIGER place attribute table (read straight from the archive):")
    places = L.tiger_places()
    print(f"  {len(places):,} Illinois place records, "
          f"{len({p['geoid'] for p in places}):,} distinct GEOIDs")

    print("\nSimplifying the place layer (topology-aware, so borders stay shared):")
    retention = simplify_places()
    gj = json.loads(L.SIMPLIFIED_GEOJSON.read_text(encoding="utf-8"))
    print(f"  {len(gj['features']):,} features in {L.rel(L.SIMPLIFIED_GEOJSON)} "
          f"at {retention} retention")
    kept = {f["properties"].get("GEOID") for f in gj["features"]}
    dropped = sorted({p["geoid"] for p in places} - kept)
    if dropped:
        print(f"  WARNING: simplification dropped {len(dropped)} places: {dropped[:10]}")
        print("  `keep-shapes` should prevent this. check_data.py check 1 will fail.")

    print("\nSimplifying the county layer (outlines only):")
    mapshaper([
        str(COUNTY_ZIP),
        "-filter", f'STATEFP == "{L.STATE_FIPS}"',
        "-filter-fields", "GEOID,NAME",
        "-simplify", "3%", "keep-shapes",
        "-o", f"precision={COORD_PRECISION}", "format=geojson", "force",
        str(COUNTIES_GEOJSON),
    ])
    cj = json.loads(COUNTIES_GEOJSON.read_text(encoding="utf-8"))
    print(f"  {len(cj['features']):,} counties in {L.rel(COUNTIES_GEOJSON)} "
          f"({COUNTIES_GEOJSON.stat().st_size:,} bytes)")

    print("\nSimplifying the state outline (silhouette beneath the places):")
    mapshaper([
        str(L.STATE_ZIP),
        "-filter", f'STATEFP == "{L.STATE_FIPS}"',
        "-filter-fields", "GEOID,NAME,STATEFP",
        "-simplify", STATE_RETENTION, "keep-shapes",
        "-o", f"precision={COORD_PRECISION}", "format=geojson", "force",
        str(L.STATE_GEOJSON),
    ])
    sj = json.loads(L.STATE_GEOJSON.read_text(encoding="utf-8"))
    print(f"  {len(sj['features']):,} feature in {L.rel(L.STATE_GEOJSON)} "
          f"({L.STATE_GEOJSON.stat().st_size:,} bytes)")
    if len(sj["features"]) != 1:
        raise RuntimeError(f"Expected exactly one Illinois feature, got "
                           f"{len(sj['features'])}")
    L.record_source(
        f"npx {MAPSHAPER} -filter STATEFP==\"{L.STATE_FIPS}\" "
        f"-simplify {STATE_RETENTION} keep-shapes -o precision={COORD_PRECISION}",
        L.rel(L.STATE_GEOJSON),
        f"Simplification step, not a download. Illinois outline, retention "
        f"{STATE_RETENTION}, coordinates quantized to {COORD_PRECISION}.",
    )

    L.record_source(
        f"npx {MAPSHAPER} -simplify {retention} keep-shapes "
        f"-o precision={COORD_PRECISION}",
        L.rel(L.SIMPLIFIED_GEOJSON),
        f"Simplification step, not a download. Retention {retention}, "
        f"coordinates quantized to {COORD_PRECISION}.",
    )
    print(f"\nProvenance appended to {L.rel(L.SOURCES_MD)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
