#!/usr/bin/env python3
"""Build everything under docs/data/ (SPEC.md §4, §5, §8).

Runs with no network access: every input is already under ``data/raw/`` or
``data/manual/``.  If an input is missing the script says which fetch script
produces it and stops.

Outputs
  docs/data/places.geojson      one feature per TIGER Illinois place, carrying the
                                properties the choropleth and the table need
  docs/data/places/<geoid>.json one shard per place, carrying the per-year series
  docs/data/meta.json           il_pct_growth, us_pct_growth, ymax, build_date,
                                sources, and the coverage/join counts the page
                                cites in its footer

Hard rules this file exists to honour:
  * A place with no BPS record gets ``coverage = "no_permit_office"`` and NULL
    metrics.  It is never given a zero (SPEC.md §1.3, §5).
  * A place with no 2010 H1 count gets a NULL ``pct_growth``.  Nothing is
    interpolated (SPEC.md §1.1).
  * ``ahpaa_status`` comes only from data/manual/ahpaa.csv.  With zero rows it is
    NULL everywhere and the site disables the filter (SPEC.md §1.2, §3.5).
"""

from __future__ import annotations

import csv
import datetime
import json
import shutil
import sys
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bps_layout as L
import crosswalk as CW
import simplify_geo as SG


def ensure_geometry() -> None:
    """Make sure the simplified geometry exists, generating it offline if not.

    ``fetch_geo.py`` normally produces these with mapshaper, which is the better
    simplifier but needs the network to run through npx.  SPEC.md §8 requires
    this script to run with no network access, so if the artefacts are absent --
    a fresh checkout that has the raw archives but not the processed ones -- they
    are rebuilt here with the pure-Python topology-preserving equivalent that
    SPEC.md §2 allows.
    """
    import simplify_geo as SGM

    if not L.SIMPLIFIED_GEOJSON.exists():
        print(f"  {L.rel(L.SIMPLIFIED_GEOJSON)} absent -- simplifying offline "
              "(Python path; fetch_geo.py would use mapshaper)")
        eps = SGM.simplify_to(L.TIGER_ZIP, L.SIMPLIFIED_GEOJSON, 1_300_000)
        print(f"  places simplified at toposimplify {eps}")
    if not CW.COUNTIES_GEOJSON.exists():
        print(f"  {L.rel(CW.COUNTIES_GEOJSON)} absent -- simplifying offline")
        eps = SGM.simplify_to(SGM.COUNTY_ZIP_FOR_BUILD, CW.COUNTIES_GEOJSON,
                              400_000, state_fips=L.STATE_FIPS)
        print(f"  counties simplified at toposimplify {eps}")


def need(path: Path, fetcher: str) -> Path:
    if not path.exists():
        raise SystemExit(
            f"\nMissing input: {L.rel(path)}\n"
            f"Run `uv run scripts/{fetcher}` first.\n"
        )
    return path


def pct(num, den):
    """Percent, or None when the denominator is not usable.  Never a zero stand-in."""
    if den is None or num is None:
        return None
    den = float(den)
    if den <= 0:
        return None
    return round(num / den * 100, 4)


def main() -> int:
    print("=" * 78)
    print("Building docs/data/")
    print(f"Illinois (FIPS {L.STATE_FIPS})   BPS {L.YMIN}-{L.YMAX}   "
          f"metrics {L.METRIC_START}-{L.YMAX}   TIGER cb_{L.TIGER_VINTAGE}")
    print("=" * 78)

    # ---------------- inputs ----------------
    for year in L.YEARS:
        need(L.place_file(year), "fetch_bps.py")
        need(L.state_file(year), "fetch_bps.py")
    h1_path = need(L.RAW_CENSUS / "h1_2010_place_17.json", "fetch_census.py")
    pop_path = need(L.RAW_CENSUS / "p1_2020_place_17.json", "fetch_census.py")
    il_h1_path = need(L.RAW_CENSUS / "h1_2010_state_17.json", "fetch_census.py")
    us_h1_path = need(L.RAW_CENSUS / "h1_2010_us.json", "fetch_census.py")
    need(L.TIGER_ZIP, "fetch_geo.py")
    need(SG.COUNTY_ZIP_FOR_BUILD, "fetch_geo.py")
    ensure_geometry()

    # ---------------- crosswalk ----------------
    print("\n[1/6] Crosswalk (SPEC.md §3.1)")
    print("-" * 78)
    CW.main()

    # ---------------- load ----------------
    print("\n[2/6] Loading raw sources")
    print("-" * 78)
    records = L.read_all_place_years()
    L.learn_municipal_ids(records)

    h1 = {}
    for r in L.read_census_json(h1_path):
        h1[r["state"] + r["place"]] = int(r["H001001"])
    pop = {}
    for r in L.read_census_json(pop_path):
        pop[r["state"] + r["place"]] = int(r["P1_001N"])
    il_h1 = int(L.read_census_json(il_h1_path)[0]["H001001"])
    us_h1 = int(L.read_census_json(us_h1_path)[0]["H001001"])
    print(f"  2010 H1 by place : {len(h1):,} places")
    print(f"  2020 P1 by place : {len(pop):,} places")
    print(f"  2010 H1 Illinois : {il_h1:,}    United States: {us_h1:,}")

    with L.CROSSWALK_CSV.open(newline="", encoding="utf-8") as fh:
        xwalk = list(csv.DictReader(fh))
    geoid_of: dict[str, str] = {}
    method_of: dict[str, str] = {}
    bps_county: dict[str, tuple[str, str]] = {}
    for row in xwalk:
        geoid_of[row["bps_id"]] = row["geoid"]
        method_of[row["bps_id"]] = row["match_method"]
        bps_county.setdefault(row["geoid"], (row["county_fips"], row["county_name"]))

    place_geo: dict[str, dict] = {}
    with CW.PLACE_GEO_CSV.open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            place_geo[row["geoid"]] = row

    ahpaa_rows, ahpaa_headers = L.read_ahpaa()
    ahpaa = {r["geoid"].strip(): (r["status"] or "").strip()
             for r in ahpaa_rows if (r.get("geoid") or "").strip()}
    ahpaa_enabled = bool(ahpaa)
    ahpaa_as_of = sorted({(r.get("as_of_date") or "").strip()
                          for r in ahpaa_rows if (r.get("as_of_date") or "").strip()})
    print(f"  AHPAA rows       : {len(ahpaa_rows)}  -> filter "
          f"{'ENABLED' if ahpaa_enabled else 'DISABLED (nothing is faked)'}")

    # ---------------- aggregate permits per place per year ----------------
    print("\n[3/6] Aggregating permits onto places")
    print("-" * 78)
    # Keyed by year ON DEMAND. A year with no BPS record for a place is a year
    # the Census has no administrative count for, which is not the same as a year
    # with no permits (SPEC.md §1.3). Those years stay absent here and are
    # written out as null, never as 0.
    series: dict[str, dict[int, dict]] = defaultdict(dict)
    bps_ids_for: dict[str, set[str]] = defaultdict(set)
    months_for: dict[str, dict[int, str]] = defaultdict(dict)
    dropped_units = 0
    for rec in records:
        geoid = geoid_of.get(rec["bps_id"])
        if geoid is None:
            dropped_units += rec["units_total"]
            continue
        slot = series[geoid].setdefault(
            rec["year"], dict.fromkeys(L.STRUCTURE_TYPES, 0))
        for k in L.STRUCTURE_TYPES:
            slot[k] += rec[k]
        bps_ids_for[geoid].add(rec["bps_id"])
        months_for[geoid][rec["year"]] = rec["months_reported"]
    print(f"  Places receiving permits : {len(series):,}")
    print(f"  Units on records with no place geometry (in unmatched.csv): "
          f"{dropped_units:,}")

    # tidy per-place-per-year table, for auditing
    with L.PERMITS_CSV.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["geoid", "year", *L.STRUCTURE_TYPES, "units_total"])
        for geoid in sorted(series):
            for year in sorted(series[geoid]):
                d = series[geoid][year]
                w.writerow([geoid, year, *(d[k] for k in L.STRUCTURE_TYPES),
                            sum(d.values())])
    print(f"  Wrote {L.rel(L.PERMITS_CSV)}")

    # ---------------- reference rates ----------------
    print("\n[4/6] Reference rates (SPEC.md §4)")
    print("-" * 78)
    il_units = us_units = 0
    for year in L.METRIC_YEARS:
        st = L.read_state_year(year)
        if L.STATE_NAME not in st or "United States" not in st:
            raise SystemExit(
                f"The {year} BPS state file is missing an "
                f"'{L.STATE_NAME}' or 'United States' row; "
                "il_pct_growth / us_pct_growth cannot be computed from it."
            )
        il_units += st[L.STATE_NAME]["units_total"]
        us_units += st["United States"]["units_total"]
    il_pct_growth = pct(il_units, il_h1)
    us_pct_growth = pct(us_units, us_h1)
    il_place_sum = sum(r["units_total"] for r in records
                       if r["year"] >= L.METRIC_START)
    print(f"  Illinois BPS units {L.METRIC_START}-{L.YMAX} (published state row): "
          f"{il_units:,}")
    print(f"    same, summed from the place file (cross-check)      : "
          f"{il_place_sum:,}  "
          f"{'identical' if il_place_sum == il_units else 'DIFFERS'}")
    print(f"  Illinois 2010 H1 housing units : {il_h1:,}")
    print(f"  il_pct_growth = {il_units:,} / {il_h1:,} = {il_pct_growth}%")
    print(f"  United States BPS units {L.METRIC_START}-{L.YMAX}: {us_units:,}")
    print(f"  United States 2010 H1           : {us_h1:,}")
    print(f"  us_pct_growth = {us_units:,} / {us_h1:,} = {us_pct_growth}%")

    # ---------------- features and shards ----------------
    print("\n[5/6] Writing features and shards")
    print("-" * 78)
    geom = json.loads(L.SIMPLIFIED_GEOJSON.read_text(encoding="utf-8"))
    tiger = {t["geoid"]: t for t in L.tiger_places()}

    if L.DOCS_SHARDS.exists():
        shutil.rmtree(L.DOCS_SHARDS)
    L.DOCS_SHARDS.mkdir(parents=True, exist_ok=True)

    features = []
    shard_payload: list[tuple[str, str]] = []
    cov_counts: dict[str, int] = defaultdict(int)
    for f in geom["features"]:
        geoid = f["properties"]["GEOID"]
        t = tiger.get(geoid, {})
        pg = place_geo.get(geoid, {})

        years_reported = sorted(series.get(geoid, {}))
        metric_years_reported = [y for y in years_reported if y >= L.METRIC_START]
        # "Reporting" is about the metric window. A place whose permit office last
        # reported in 2003 has no administrative record for 2010-2025, so giving it
        # a 2010-2025 percentage -- which could only ever come out as 0% -- would be
        # exactly the missing-value-as-zero that SPEC.md §1.3 forbids.
        reporting = bool(metric_years_reported)

        if reporting:
            coverage = "reporting"
            county_fips, county_name = bps_county.get(geoid, ("", ""))
            county_source = "bps_record"
            if not county_name:
                county_fips, county_name = pg.get("county_fips", ""), pg.get("county_name", "")
                county_source = "derived_from_geometry"
        else:
            coverage = "no_permit_office"
            county_fips = pg.get("county_fips", "")
            county_name = pg.get("county_name", "")
            county_source = "derived_from_geometry"
        cov_counts[coverage] += 1

        h1_2010 = h1.get(geoid)          # None when the place did not exist in 2010
        pop2020 = pop.get(geoid)

        yr = series.get(geoid, {})
        # The series always covers the full span so that gaps are visible, with a
        # null -- not a zero -- in every year the place has no BPS record.
        ser = [
            ({"year": y, **yr[y], "total": sum(yr[y].values())} if y in yr
             else {"year": y, **{k: None for k in L.STRUCTURE_TYPES}, "total": None})
            for y in L.YEARS
        ] if years_reported else []

        if reporting:
            by_type = {k: sum(yr[y][k] for y in metric_years_reported)
                       for k in L.STRUCTURE_TYPES}
            units_total_2010 = sum(by_type.values())
            mf5p_total = by_type["mf5p"]
            pct_growth = pct(units_total_2010, h1_2010)
            pct_by_type = {k: pct(by_type[k], h1_2010) for k in L.STRUCTURE_TYPES}
            zero_mf = (mf5p_total == 0)
        else:
            # SPEC.md §1.3 / §5: absence of a permit record is not zero housing.
            by_type = {k: None for k in L.STRUCTURE_TYPES}
            units_total_2010 = mf5p_total = None
            pct_growth = None
            pct_by_type = {k: None for k in L.STRUCTURE_TYPES}
            zero_mf = None

        if years_reported:
            by_type_2000 = {k: sum(yr[y][k] for y in years_reported)
                            for k in L.STRUCTURE_TYPES}
            units_total_2000 = sum(by_type_2000.values())
        else:
            by_type_2000 = {k: None for k in L.STRUCTURE_TYPES}
            units_total_2000 = None

        status = ahpaa.get(geoid) if ahpaa_enabled else None

        features.append({
            "type": "Feature",
            "geometry": f["geometry"],
            "properties": {
                "geoid": geoid,
                "name": t.get("name") or f["properties"].get("NAME", ""),
                "namelsad": t.get("namelsad") or f["properties"].get("NAMELSAD", ""),
                "county": county_name,
                "coverage": coverage,
                "pop2020": pop2020,
                "h1_2010": h1_2010,
                "pct_growth": pct_growth,
                "units_total_2010": units_total_2010,
                "units_total_2000": units_total_2000,
                "mf5p_total": mf5p_total,
                "zero_mf": zero_mf,
                "first_metric_year": (metric_years_reported[0]
                                      if metric_years_reported else None),
                "n_metric_years": len(metric_years_reported),
                "ahpaa_status": status,
                "lon": float(pg["lon"]) if pg.get("lon") else None,
                "lat": float(pg["lat"]) if pg.get("lat") else None,
                **{f"u_{k}": by_type[k] for k in L.STRUCTURE_TYPES},
                **{f"p_{k}": pct_by_type[k] for k in L.STRUCTURE_TYPES},
            },
        })

        shard = {
            "geoid": geoid,
            "name": t.get("namelsad") or t.get("name", ""),
            "short_name": t.get("name", ""),
            "county": county_name,
            "county_fips": county_fips,
            "county_source": county_source,
            "coverage": coverage,
            "pop2020": pop2020,
            "h1_2010": h1_2010,
            "pct_growth": pct_growth,
            "units_total_2010": units_total_2010,
            "units_total_2000": units_total_2000,
            "units_by_type": by_type,
            "units_by_type_2000": by_type_2000,
            "pct_growth_by_type": pct_by_type,
            "mf5p_total": mf5p_total,
            "zero_mf": zero_mf,
            "ahpaa_status": status,
            "series": ser,
            "years_reported": years_reported,
            "first_year_reported": years_reported[0] if years_reported else None,
            "last_year_reported": years_reported[-1] if years_reported else None,
            "metric_years_reported": len(metric_years_reported),
            "first_metric_year": (metric_years_reported[0]
                                  if metric_years_reported else None),
            "bps_ids": sorted(bps_ids_for.get(geoid, [])),
            "match_methods": sorted({method_of[b] for b in bps_ids_for.get(geoid, [])}),
            "months_reported": months_for.get(geoid, {}),
            "coverage_note": (
                None if reporting else
                (f"This place last appears in the Building Permits Survey in "
                 f"{years_reported[-1]}, and has no permit record for any year from "
                 f"{L.METRIC_START} on. A percentage since {L.METRIC_START} would "
                 "have nothing behind it, so none is shown. The years it did report "
                 "are in the chart below. This place is excluded from rankings and "
                 "from statewide totals."
                 if years_reported else
                 "No permit office reporting to the Census Building Permits Survey "
                 "for this place. That is not the same as no housing being built "
                 "here: it means the Census has no administrative record to count. "
                 "This place is excluded from rankings and from statewide totals.")
            ),
        }
        shard_payload.append((geoid, json.dumps(shard, separators=(",", ":"))))

    # Writing 1,461 small files is latency-bound, not CPU-bound, so thread it.
    def _write(item):
        geoid, text = item
        (L.DOCS_SHARDS / f"{geoid}.json").write_text(text, encoding="utf-8")

    with ThreadPoolExecutor(max_workers=32) as pool:
        list(pool.map(_write, shard_payload))

    out = {
        "type": "FeatureCollection",
        "name": f"il_places_{L.TIGER_VINTAGE}",
        "features": features,
    }
    L.DOCS_DATA.mkdir(parents=True, exist_ok=True)
    (L.DOCS_DATA / "places.geojson").write_text(
        json.dumps(out, separators=(",", ":")), encoding="utf-8")
    # County outlines: SPEC.md §6.1's keyless-basemap fallback, and the only thing
    # standing in for a basemap, so it ships with the site rather than being fetched.
    shutil.copyfile(CW.COUNTIES_GEOJSON, L.DOCS_DATA / "counties.geojson")
    print(f"  counties.geojson: "
          f"{(L.DOCS_DATA / 'counties.geojson').stat().st_size:,} bytes")

    size = (L.DOCS_DATA / "places.geojson").stat().st_size
    print(f"  places.geojson : {len(features):,} features, {size:,} bytes "
          f"({size / 1024 / 1024:.2f} MB, budget 3.00 MB)")
    print(f"  shards         : {len(features):,} files in {L.rel(L.DOCS_SHARDS)}")
    for k in sorted(cov_counts, key=lambda k: -cov_counts[k]):
        print(f"  coverage {k:<18} {cov_counts[k]:>6,}")
    n_null_pct = sum(1 for ft in features if ft["properties"]["pct_growth"] is None)
    print(f"  Features with a NULL pct_growth: {n_null_pct:,} "
          "(no permit office, or no 2010 count -- never rendered as zero)")
    n_zero_mf = sum(1 for ft in features if ft["properties"]["zero_mf"] is True)
    print(f"  Reporting places with zero 5+ unit permits: {n_zero_mf:,}")
    partial = [ft for ft in features
               if ft["properties"]["coverage"] == "reporting"
               and ft["properties"]["n_metric_years"] < len(L.METRIC_YEARS)]
    print(f"  Reporting places that do not report every {L.METRIC_START}-{L.YMAX} "
          f"year: {len(partial):,}")
    print("     their unreported years are null in the series, never 0, and the "
          "detail panel")
    print("     says which year their record starts from.")

    # ---------------- meta ----------------
    print("\n[6/6] meta.json")
    print("-" * 78)
    with L.UNMATCHED_CSV.open(newline="", encoding="utf-8") as fh:
        unmatched = list(csv.DictReader(fh))
    matched_units = sum(ft["properties"]["units_total_2010"] or 0 for ft in features)
    mun_units = sum(r["units_total"] for r in records
                    if r["year"] >= L.METRIC_START
                    and L.record_class(r) == "municipal")

    sources = [
        "U.S. Census Bureau, Building Permits Survey, place-level annual files, "
        f"Midwest region, {L.YMIN}-{L.YMAX} "
        "(www2.census.gov/econ/bps/Place/Midwest Region/mwYYYYa.txt)",
        "U.S. Census Bureau, Building Permits Survey, state-level annual files, "
        f"{L.METRIC_START}-{L.YMAX}, Illinois and United States totals "
        "(www2.census.gov/econ/bps/State/stYYYYa.txt)",
        "U.S. Census Bureau, 2010 Decennial Census SF1, table H1 (H001001), "
        "total housing units, Illinois places / Illinois / United States",
        "U.S. Census Bureau, 2020 Decennial Census PL, table P1 (P1_001N), "
        "total population, Illinois places",
        f"U.S. Census Bureau, TIGER cartographic boundary files, "
        f"cb_{L.TIGER_VINTAGE}_{L.STATE_FIPS}_place_500k and "
        f"cb_{L.TIGER_VINTAGE}_us_county_500k",
        "Illinois Housing Development Authority AHPAA determination list "
        "(data/manual/ahpaa.csv, hand-maintained; "
        f"{len(ahpaa_rows)} rows loaded)",
    ]
    meta = {
        "build_date": datetime.date.today().isoformat(),
        "ymax": L.YMAX,
        "ymin": L.YMIN,
        "metric_start": L.METRIC_START,
        "tiger_vintage": L.TIGER_VINTAGE,
        "il_pct_growth": il_pct_growth,
        "us_pct_growth": us_pct_growth,
        "il_units_2010_ymax": il_units,
        "us_units_2010_ymax": us_units,
        "il_h1_2010": il_h1,
        "us_h1_2010": us_h1,
        "n_places": len(features),
        "coverage_counts": dict(cov_counts),
        "n_zero_mf": n_zero_mf,
        "n_partial_coverage": len(partial),
        "ahpaa": {
            "enabled": ahpaa_enabled,
            "rows": len(ahpaa_rows),
            "as_of": ahpaa_as_of[-1] if ahpaa_as_of else None,
            "disabled_reason": (
                None if ahpaa_enabled else
                "The AHPAA non-exempt list is a periodic determination by the "
                "Illinois Housing Development Authority with no machine-readable "
                "feed, so it is hand-entered into data/manual/ahpaa.csv. That file "
                "is currently empty, so no municipality has an AHPAA status here. "
                "Showing one would mean inventing it."
            ),
        },
        "join": {
            "municipal_units_2010_ymax": mun_units,
            "units_on_places_with_geometry": matched_units,
            "join_rate_municipal_pct": pct(matched_units, mun_units),
            "join_rate_all_records_pct": pct(matched_units, il_units),
            "unmatched_records": len(unmatched),
        },
        "source_line": "U.S. Census Building Permits Survey; 2010 Decennial Census",
        "sources": sources,
    }
    (L.DOCS_DATA / "meta.json").write_text(
        json.dumps(meta, indent=2), encoding="utf-8")
    print(f"  il_pct_growth {il_pct_growth}%   us_pct_growth {us_pct_growth}%   "
          f"ymax {L.YMAX}   build {meta['build_date']}")
    print(f"  join rate (municipal) {meta['join']['join_rate_municipal_pct']}%")
    print(f"  Wrote {L.rel(L.DOCS_DATA / 'meta.json')}")

    print("\n" + "=" * 78)
    print("Build complete. Next: uv run scripts/check_data.py")
    print("=" * 78)
    return 0


if __name__ == "__main__":
    sys.exit(main())
