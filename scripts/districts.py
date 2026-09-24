"""Place-to-district overlaps and the legislator table (the legislator view).

Imported by build.py and check_data.py so that membership is defined once.

Membership is an areal overlap computed on the UNSIMPLIFIED TIGER geometry: a place
is listed under a district when at least ``L.DISTRICT_SHARE_MIN`` of its land area
falls inside it, and the share is carried with it. Places and districts come from
the same cartographic-boundary vintage, so shared edges coincide and the overlaps
below 1% are slivers, not neighbourhoods.

Areas are compared in degrees. A share is a ratio of two areas inside one place,
a few miles across at most, so the latitude scaling cancels.
"""

from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

import bps_layout as L


def _read_zip(path: Path) -> list[tuple[dict, object]]:
    import shapefile
    from shapely.geometry import shape

    if not path.exists():
        raise FileNotFoundError(f"{path} is missing. Run `uv run scripts/"
                                f"{'fetch_geo.py' if path == L.TIGER_ZIP else 'fetch_districts.py'}` first.")
    with zipfile.ZipFile(path) as zf:
        names = {Path(n).suffix.lower(): n for n in zf.namelist()}
        r = shapefile.Reader(shp=io.BytesIO(zf.read(names[".shp"])),
                             dbf=io.BytesIO(zf.read(names[".dbf"])),
                             shx=io.BytesIO(zf.read(names[".shx"])))
        out = [(rec.record.as_dict(), shape(rec.shape.__geo_interface__))
               for rec in r.iterShapeRecords()]
        r.close()
    return out


def memberships() -> dict[str, dict[str, list[tuple[int, float]]]]:
    """``{chamber: {geoid: [(district, share), ...]}}``, largest share first."""
    from shapely import STRtree

    places = _read_zip(L.TIGER_ZIP)
    out: dict[str, dict[str, list[tuple[int, float]]]] = {}
    for chamber, c in L.CHAMBERS.items():
        ds = _read_zip(L.district_zip(chamber))
        tree = STRtree([g for _, g in ds])
        by_place: dict[str, list[tuple[int, float]]] = {}
        for attrs, g in places:
            area = g.area
            hits = []
            for i in tree.query(g):
                share = g.intersection(ds[i][1]).area / area if area else 0.0
                if share >= L.DISTRICT_SHARE_MIN:
                    hits.append((int(ds[i][0][c["field"]]), round(min(share, 1.0), 4)))
            by_place[attrs["GEOID"]] = sorted(hits, key=lambda h: -h[1])
        out[chamber] = by_place
    return out


def label_points(chamber: str) -> dict[int, tuple[float, float, float]]:
    """``{district: (lon, lat, land_area)}``: where the district number is drawn.

    The pole of inaccessibility -- the interior point farthest from the edge --
    of the district's largest polygon, so the number sits inside even a crescent-
    or C-shaped district, where a centroid can fall outside. The area orders the
    labels: when two would collide, the larger district keeps its number."""
    from shapely.ops import polylabel

    out = {}
    for attrs, g in _read_zip(L.district_zip(chamber)):
        part = max(getattr(g, "geoms", [g]), key=lambda p: p.area)
        pt = polylabel(part, tolerance=0.0005)
        out[int(attrs[L.CHAMBERS[chamber]["field"]])] = (
            round(pt.x, 5), round(pt.y, 5), int(attrs.get("ALAND") or 0))
    return out


def _ilga_url(links: str) -> str | None:
    """The member's most recent ilga.gov page (Open States lists every GA)."""
    urls = [u for u in (links or "").split(";") if "ilga.gov" in u]
    return urls[-1] if urls else None


def read_overrides() -> list[dict]:
    if not L.LEGISLATOR_OVERRIDES_CSV.exists():
        return []
    with L.LEGISLATOR_OVERRIDES_CSV.open(newline="", encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if any((v or "").strip() for v in r.values())]


OVERRIDE_FIELDS = ("name", "party", "email", "phone", "url", "vacant")


def legislators() -> dict[str, dict[int, dict]]:
    """``{chamber: {district: member}}`` from the Open States snapshot, with
    data/manual/legislator_overrides.csv applied. A missing contact field is
    None, never an invented value."""
    if not L.LEGISLATORS_CSV.exists():
        raise FileNotFoundError(f"{L.LEGISLATORS_CSV} is missing. "
                                "Run `uv run scripts/fetch_districts.py` first.")
    with L.LEGISLATORS_CSV.open(newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    by_os = {c["openstates"]: ch for ch, c in L.CHAMBERS.items()}
    out: dict[str, dict[int, dict]] = {ch: {} for ch in L.CHAMBERS}
    for r in rows:
        ch = by_os.get(r["current_chamber"])
        if ch is None:
            continue
        out[ch][int(r["current_district"])] = {
            "name": r["name"].strip() or None,
            "party": r["current_party"].strip() or None,
            "email": r["email"].strip() or None,
            "phone": (r["district_voice"] or r["capitol_voice"]).strip() or None,
            "office": r["district_address"].strip() or None,
            "url": _ilga_url(r["links"]),
            "source": "openstates",
        }
    for o in read_overrides():
        ch, d, field = o["chamber"].strip(), int(o["district"]), o["field"].strip()
        member = out[ch].setdefault(d, {})
        if field == "vacant" and o["value"].strip().lower() == "true":
            out[ch][d] = {"vacant": True, "source": "override"}
        else:
            member[field] = o["value"].strip()
            member["source"] = "override"
    return out
