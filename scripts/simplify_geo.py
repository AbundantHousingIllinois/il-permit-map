#!/usr/bin/env python3
"""Offline, topology-preserving simplification of a TIGER shapefile.

SPEC.md §2 allows "mapshaper (npx) or Python equivalent" for geometry
simplification.  ``fetch_geo.py`` uses mapshaper, which is the better tool but
needs the network to run via npx.  SPEC.md §8 requires ``build.py`` to run with
no network access, so this module is the Python equivalent: ``build.py`` calls it
when ``data/processed/places_simplified.geojson`` is absent, and a clean checkout
therefore builds offline from ``data/raw/`` alone.

Topology matters here.  Simplifying each polygon independently pulls shared
borders apart and opens slivers between neighbouring municipalities, so this goes
through ``topojson``, which simplifies the shared arcs once.
"""

from __future__ import annotations

import io
import json
import sys
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bps_layout as L

# Chosen to land near the mapshaper output's size and vertex count; stepped down
# (less simplification) only if the size budget allows. Units are degrees.
TOPO_LADDER = [0.0006, 0.0009, 0.0013, 0.002, 0.003, 0.005]
COORD_DECIMALS = 5          # SPEC.md §3.4
KEEP_FIELDS = ("GEOID", "NAME", "NAMELSAD", "ALAND", "AWATER", "STATEFP")

# The county layer is published only as a national archive at this vintage.
COUNTY_ZIP_FOR_BUILD = L.RAW_GEO / f"cb_{L.TIGER_VINTAGE}_us_county_500k.zip"


def read_shapefile_zip(zip_path: Path, state_fips: str | None = None) -> dict:
    """Read a zipped shapefile into a GeoJSON FeatureCollection."""
    import shapefile

    if not zip_path.exists():
        raise FileNotFoundError(
            f"{zip_path} is missing. Run `uv run scripts/fetch_geo.py` first."
        )
    with zipfile.ZipFile(zip_path) as zf:
        names = {Path(n).suffix.lower(): n for n in zf.namelist()}
        need = [".shp", ".dbf", ".shx"]
        missing = [s for s in need if s not in names]
        if missing:
            raise ValueError(f"{zip_path} has no {missing} member")
        parts = {s: io.BytesIO(zf.read(names[s])) for s in need}
        reader = shapefile.Reader(shp=parts[".shp"], dbf=parts[".dbf"], shx=parts[".shx"])
        features = []
        for rec in reader.iterShapeRecords():
            attrs = rec.record.as_dict()
            if state_fips is not None and attrs.get("STATEFP") != state_fips:
                continue
            props = {k: attrs[k] for k in KEEP_FIELDS if k in attrs}
            features.append({
                "type": "Feature",
                "properties": props,
                "geometry": rec.shape.__geo_interface__,
            })
        reader.close()
    return {"type": "FeatureCollection", "features": features}


def _round_coords(obj, nd: int):
    if isinstance(obj, (int, float)):
        return round(obj, nd)
    if isinstance(obj, list):
        return [_round_coords(o, nd) for o in obj]
    return obj


def simplify(fc: dict, epsilon: float) -> dict:
    import topojson

    topo = topojson.Topology(fc, prequantize=False, shared_coords=True)
    simplified = topo.toposimplify(epsilon)
    out = json.loads(simplified.to_geojson())
    for f in out["features"]:
        if f.get("geometry"):
            f["geometry"]["coordinates"] = _round_coords(
                f["geometry"]["coordinates"], COORD_DECIMALS)
    return out


def simplify_to(zip_path: Path, dest: Path, budget_bytes: int,
                state_fips: str | None = None, verbose: bool = True) -> float:
    """Simplify ``zip_path`` into ``dest``, keeping every feature.

    Raises if any feature would be lost -- check_data.py check 1 requires an exact
    1:1 correspondence with the TIGER place list.
    """
    fc = read_shapefile_zip(zip_path, state_fips=state_fips)
    want = len(fc["features"])
    if verbose:
        print(f"    read {want:,} features from {L.rel(zip_path)}")
    for eps in TOPO_LADDER:
        out = simplify(fc, eps)
        got = [f for f in out["features"] if f.get("geometry")]
        text = json.dumps(out, separators=(",", ":"))
        size = len(text.encode("utf-8"))
        if verbose:
            print(f"    toposimplify {eps:<8} -> {len(got):,}/{want:,} features, "
                  f"{size:,} bytes ({size / 1024 / 1024:.2f} MB)")
        if len(got) != want:
            continue                      # too aggressive; it dropped something
        if size <= budget_bytes:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
            return eps
    raise RuntimeError(
        f"Could not simplify {zip_path.name} under {budget_bytes:,} bytes without "
        "losing a feature. SPEC.md §10 says to note this in BLOCKERS.md before "
        "reaching for PMTiles."
    )


if __name__ == "__main__":
    print("Simplifying (offline Python path)")
    eps = simplify_to(L.TIGER_ZIP, L.SIMPLIFIED_GEOJSON, 2_100_000)
    print(f"  places  -> {L.rel(L.SIMPLIFIED_GEOJSON)} at toposimplify {eps}")
