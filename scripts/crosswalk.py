#!/usr/bin/env python3
"""Map BPS place records to TIGER place GEOIDs (SPEC.md §3.1).

SPEC.md calls this the single most likely place for the build to go silently
wrong, so the join is explicit, every method is labelled, and every record that
does not join is written out with its unit totals.

Match order, most to least trustworthy:

1. ``fips_place``        The modern-era files (2007+) carry the 5-digit FIPS place
                         code directly.  GEOID = ``17`` + that code.  Not one
                         Illinois BPS id changes its FIPS place code anywhere in
                         2007-2025, so this is an identifier match, not a guess.
2. ``bps_id_inherited``  The 2000-2006 files have no FIPS place column at all.
                         The 6-digit BPS id is stable across the whole series, so
                         a legacy record inherits the GEOID its own id resolved to
                         in the modern era.
3. ``name`` /            Fallback for records whose identifier resolves to nothing
   ``name_county``       in TIGER: normalised name, required to be unique across
                         Illinois places, or unique within the record's county.
                         Deliberately exact-after-normalisation -- no fuzzy or
                         edit-distance matching, because a wrong name match
                         silently credits one municipality's permits to another.

Anything left over goes to ``data/processed/unmatched.csv`` with a reason.  The
name matching is never loosened to raise the join rate (SPEC.md §3.1).

Also writes ``data/processed/place_geo.csv``: an interior point per place, and
the county each place falls in, derived from the TIGER county layer.  The map
needs the interior point to open a detail panel; the county is a label.
"""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bps_layout as L

PLACE_GEO_CSV = L.PROCESSED / "place_geo.csv"
COUNTIES_GEOJSON = L.PROCESSED / "counties_simplified.geojson"

CROSSWALK_FIELDS = [
    "bps_id", "fips_place", "bps_name", "county_fips", "county_name",
    "geoid", "tiger_name", "match_method", "first_year", "last_year",
    "units_2010_ymax", "units_2000_ymax",
]
UNMATCHED_FIELDS = [
    "bps_id", "bps_name", "county_fips", "county_name", "fips_place", "fips_mcd",
    "record_class", "reason", "first_year", "last_year",
    "units_2010_ymax", "units_2000_ymax", "sf", "du", "mf34", "mf5p",
]


# --------------------------------------------------------------------------

def county_names() -> dict[str, str]:
    """``{3-digit county FIPS: county name}`` from the 2010 SF1 county query."""
    rows = L.read_census_json(L.RAW_CENSUS / "county_names_17.json")
    out = {}
    for r in rows:
        name = r["NAME"].split(",")[0].strip()
        out[r["county"].zfill(3)] = name
    return out


def place_geometry() -> tuple[dict, dict]:
    """Return ``(interior points, county assignment)`` for every TIGER place."""
    from shapely.geometry import shape, Point
    from shapely.strtree import STRtree

    if not L.SIMPLIFIED_GEOJSON.exists():
        raise FileNotFoundError(
            f"{L.rel(L.SIMPLIFIED_GEOJSON)} is missing. "
            "Run `uv run scripts/fetch_geo.py` first."
        )
    places = json.loads(L.SIMPLIFIED_GEOJSON.read_text(encoding="utf-8"))
    counties = json.loads(COUNTIES_GEOJSON.read_text(encoding="utf-8"))

    cgeoms, cnames = [], []
    for f in counties["features"]:
        cgeoms.append(shape(f["geometry"]))
        cnames.append((f["properties"]["GEOID"][-3:], f["properties"]["NAME"]))
    tree = STRtree(cgeoms)

    points, assigned = {}, {}
    for f in places["features"]:
        geoid = f["properties"]["GEOID"]
        geom = shape(f["geometry"])
        pt = geom.representative_point()
        points[geoid] = (round(pt.x, 5), round(pt.y, 5))
        hit = None
        for idx in tree.query(pt):
            if cgeoms[idx].contains(pt):
                hit = cnames[idx]
                break
        if hit is None:                      # point on a border: nearest county
            idx = min(range(len(cgeoms)), key=lambda i: cgeoms[i].distance(pt))
            hit = cnames[idx]
        assigned[geoid] = hit
    return points, assigned


# --------------------------------------------------------------------------

def main() -> int:
    print("=" * 78)
    print("Building the BPS -> TIGER place crosswalk")
    print("=" * 78)

    records = L.read_all_place_years()
    L.learn_municipal_ids(records)
    tiger = L.tiger_places()
    tiger_by_geoid = {t["geoid"]: t for t in tiger}
    cnames = county_names()
    print(f"  BPS Illinois records   : {len(records):,} rows, "
          f"{len({r['bps_id'] for r in records}):,} distinct BPS ids")
    print(f"  TIGER Illinois places  : {len(tiger_by_geoid):,}")
    print(f"  Municipal BPS ids      : {len(L.MUNICIPAL_BPS_IDS):,} "
          "(proven by a real FIPS place code in a modern-era file)")

    print("\n  Deriving an interior point and a county for every TIGER place ...")
    points, place_county = place_geometry()
    print(f"    {len(points):,} interior points, "
          f"{len({v[0] for v in place_county.values()}):,} distinct counties used")

    # TIGER name index for the fallback.
    by_name: dict[str, list[str]] = defaultdict(list)
    for t in tiger:
        by_name[L.normalize_name(t["name"])].append(t["geoid"])
        nl = L.normalize_name(t["namelsad"])
        if nl != L.normalize_name(t["name"]):
            by_name[nl].append(t["geoid"])

    # ---------------- aggregate per BPS id ----------------
    agg: dict[str, dict] = {}
    for rec in records:
        a = agg.setdefault(rec["bps_id"], {
            "bps_id": rec["bps_id"], "names": {}, "fips_place": "", "fips_mcd": "",
            "county_fips": rec["county_fips"], "classes": set(),
            "first_year": rec["year"], "last_year": rec["year"],
            "units_2010_ymax": 0, "units_2000_ymax": 0,
            **{k: 0 for k in L.STRUCTURE_TYPES},
        })
        a["names"][rec["year"]] = rec["name"]
        a["first_year"] = min(a["first_year"], rec["year"])
        a["last_year"] = max(a["last_year"], rec["year"])
        a["classes"].add(L.record_class(rec))
        fp = rec["fips_place"].strip().zfill(5) if rec["fips_place"].strip() else ""
        if fp and fp not in L.NON_PLACE_FIPS:
            a["fips_place"] = fp
        if rec["fips_mcd"].strip():
            a["fips_mcd"] = rec["fips_mcd"].strip()
        if rec["county_fips"] != "000":
            a["county_fips"] = rec["county_fips"]
        a["units_2000_ymax"] += rec["units_total"]
        if rec["year"] >= L.METRIC_START:
            a["units_2010_ymax"] += rec["units_total"]
            for k in L.STRUCTURE_TYPES:
                a[k] += rec[k]
    for a in agg.values():
        a["bps_name"] = a["names"][max(a["names"])]     # most recent spelling
        a["record_class"] = (
            "municipal" if "municipal" in a["classes"]
            else sorted(a["classes"])[0]
        )
        a["county_name"] = cnames.get(a["county_fips"], "")

    # ---------------- match ----------------
    matched: dict[str, dict] = {}
    unmatched: list[dict] = []
    method_counts: dict[str, int] = defaultdict(int)

    # Years each GEOID is already claimed for, so a name match can never make two
    # BPS records report the same place in the same year and double-count it.
    claimed_years: dict[str, set[int]] = defaultdict(set)
    for rec in records:
        a = agg[rec["bps_id"]]
        if a["record_class"] == "municipal" and a["fips_place"]:
            cand = L.STATE_FIPS + a["fips_place"]
            if cand in tiger_by_geoid:
                claimed_years[cand].add(rec["year"])
    years_of: dict[str, set[int]] = defaultdict(set)
    for rec in records:
        years_of[rec["bps_id"]].add(rec["year"])

    # `other_non_place` is a legacy-era record with no FIPS place column and a name
    # that matches none of the not-a-place patterns. SPEC.md §3.1 says to fall back
    # to normalised name + county for exactly this case, so these are offered to
    # the matcher rather than discarded.
    ELIGIBLE = ("municipal", "other_non_place")

    for bps_id, a in sorted(agg.items()):
        if a["record_class"] not in ELIGIBLE:
            a["reason"] = a["record_class"]
            unmatched.append(a)
            continue

        geoid = method = None
        # 1 / 2: identifier.
        if a["fips_place"]:
            cand = L.STATE_FIPS + a["fips_place"]
            if cand in tiger_by_geoid:
                geoid = cand
                method = "fips_place" if a["last_year"] >= 2007 else "bps_id_inherited"
        # 3: name fallback.
        if geoid is None:
            key = L.normalize_name(a["bps_name"])
            cands = sorted(set(by_name.get(key, [])))
            if len(cands) == 1:
                geoid, method = cands[0], "name"
            elif len(cands) > 1:
                same = [g for g in cands
                        if place_county.get(g, ("", ""))[0] == a["county_fips"]]
                if len(same) == 1:
                    geoid, method = same[0], "name_county"
                else:
                    a["reason"] = "ambiguous_name"
                    a["ambiguous_candidates"] = cands
                    unmatched.append(a)
                    continue
            if geoid is not None:
                overlap = sorted(years_of[bps_id] & claimed_years.get(geoid, set()))
                if overlap:
                    # Accepting this would credit the same place twice in the same
                    # year. Refuse and report rather than inflate the numerator.
                    a["reason"] = "would_double_count"
                    a["overlap_years"] = overlap
                    unmatched.append(a)
                    continue
                claimed_years[geoid] |= years_of[bps_id]
        if geoid is None:
            a["reason"] = ("no_tiger_match" if a["record_class"] == "municipal"
                           else "no_place_code_and_no_name_match")
            unmatched.append(a)
            continue

        a["geoid"] = geoid
        a["match_method"] = method
        a["tiger_name"] = tiger_by_geoid[geoid]["namelsad"]
        method_counts[method] += 1
        if geoid in matched:
            # Two BPS ids resolving to one place is legitimate (an office renamed
            # or re-coded); their units are summed downstream. Recorded, not hidden.
            matched[geoid].setdefault("also", []).append(bps_id)
        else:
            matched[geoid] = a

    # ---------------- write ----------------
    L.PROCESSED.mkdir(parents=True, exist_ok=True)
    rows = sorted(
        (a for a in agg.values() if a.get("geoid")),
        key=lambda a: (a["geoid"], a["bps_id"]),
    )
    with L.CROSSWALK_CSV.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=CROSSWALK_FIELDS, extrasaction="ignore")
        w.writeheader()
        for a in rows:
            w.writerow(a)

    unmatched.sort(key=lambda a: -a["units_2010_ymax"])
    with L.UNMATCHED_CSV.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=UNMATCHED_FIELDS, extrasaction="ignore")
        w.writeheader()
        for a in unmatched:
            w.writerow(a)

    with PLACE_GEO_CSV.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["geoid", "lon", "lat", "county_fips", "county_name"])
        for geoid in sorted(points):
            lon, lat = points[geoid]
            cf, cn = place_county.get(geoid, ("", ""))
            w.writerow([geoid, lon, lat, cf, cn])

    # ---------------- report ----------------
    print(f"\n  Crosswalk rows written : {len(rows):,}  -> {L.rel(L.CROSSWALK_CSV)}")
    print("  Match methods:")
    for m in ("fips_place", "bps_id_inherited", "name", "name_county"):
        print(f"    {m:<20} {method_counts.get(m, 0):>6,}")
    print(f"  Distinct TIGER places matched: {len(matched):,}")
    multi = {g: a["also"] for g, a in matched.items() if a.get("also")}
    if multi:
        print(f"  Places matched by more than one BPS id: {len(multi)}")
        for g, ids in list(multi.items())[:10]:
            print(f"    {g} {matched[g]['bps_name']}: {matched[g]['bps_id']} + {ids}")

    mun_units = sum(a["units_2010_ymax"] for a in agg.values()
                    if a["record_class"] == "municipal")
    hit_units = sum(a["units_2010_ymax"] for a in agg.values() if a.get("geoid"))
    print(f"\n  Municipal units {L.METRIC_START}-{L.YMAX}      : {mun_units:,}")
    print(f"  Of those, joined to geometry : {hit_units:,}  "
          f"({hit_units / mun_units * 100:.3f}%)" if mun_units else "")

    print(f"\n  Unmatched records written : {len(unmatched):,}  "
          f"-> {L.rel(L.UNMATCHED_CSV)}")
    by_reason: dict[str, list] = defaultdict(list)
    for a in unmatched:
        by_reason[a["reason"]].append(a)
    print(f"    {'reason':<24}{'records':>9}{'units 2010-':>13}{'units 2000-':>13}")
    for reason in sorted(by_reason, key=lambda r: -sum(
            a["units_2010_ymax"] for a in by_reason[r])):
        g = by_reason[reason]
        print(f"    {reason:<24}{len(g):>9,}"
              f"{sum(a['units_2010_ymax'] for a in g):>13,}"
              f"{sum(a['units_2000_ymax'] for a in g):>13,}")

    genuine = [a for a in unmatched
               if a["reason"] in ("no_tiger_match", "ambiguous_name",
                                  "would_double_count",
                                  "no_place_code_and_no_name_match")]
    print(f"\n  Genuine join failures (a municipality we could not place): "
          f"{len(genuine)}")
    for a in sorted(genuine, key=lambda a: -a["units_2010_ymax"]):
        print(f"    {a['bps_id']} {a['units_2010_ymax']:>6,} units  "
              f"fips={a['fips_place'] or '-':<6} {a['county_name']:<14} "
              f"{a['bps_name']}  [{a['reason']}]")

    print(f"\n  {L.rel(PLACE_GEO_CSV)}: {len(points):,} interior points + counties")
    return 0


if __name__ == "__main__":
    sys.exit(main())
