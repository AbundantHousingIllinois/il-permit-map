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
    if not L.STATE_GEOJSON.exists():
        print(f"  {L.rel(L.STATE_GEOJSON)} absent -- simplifying offline")
        eps = SGM.simplify_to(L.STATE_ZIP, L.STATE_GEOJSON, 60_000,
                              state_fips=L.STATE_FIPS)
        print(f"  state outline simplified at toposimplify {eps}")


def geometry_bbox(fc: dict) -> list[list[float]]:
    """[[west, south], [east, north]] of every coordinate in ``fc``, rounded
    outward to 3 decimals so the initial view never clips the state's edge."""
    import math

    xs: list[float] = []
    ys: list[float] = []

    def walk(c):
        if c and isinstance(c[0], (int, float)):
            xs.append(c[0])
            ys.append(c[1])
        else:
            for sub in c:
                walk(sub)

    for f in fc["features"]:
        walk(f["geometry"]["coordinates"])
    down = lambda v: math.floor(v * 1000) / 1000
    up = lambda v: math.ceil(v * 1000) / 1000
    return [[down(min(xs)), down(min(ys))], [up(max(xs)), up(max(ys))]]


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


# Six mantissas per decade, not the usual three. A 1/2/5 ladder only offers
# five distinct values between 10 and 200, and the sparse structure types need
# six breaks inside about that range.
NICE_MANTISSAS = (1, 1.5, 2, 3, 5, 7)


def nice_ceiling(v: float) -> int:
    """Smallest readable round number at or above ``v``."""
    if v <= 0:
        return 0
    import math
    exp = math.floor(math.log10(v))
    for m in NICE_MANTISSAS + (10,):
        step = m * 10 ** exp
        if step >= v - 1e-9:
            return int(round(step))
    return int(10 ** (exp + 1))


def stop_ladder(values, n: int = 7) -> list[int]:
    """Seven sequential legend breaks fitted to the data actually present.

    The total-units scale used one hard-coded 0-25,000 ladder for every
    structure type, so filtering to 5+ unit put almost every municipality in the
    lightest bin and the map went blank.  The breaks are derived per type
    instead, from the distribution of measured places, and rounded to readable
    numbers.  Places with no measurement contribute nothing -- a null is not a
    zero here either.
    """
    xs = sorted(v for v in values if v is not None and v > 0)
    if not xs:
        return list(range(n))
    # Geometric between the median and the 99th percentile of the places that
    # have any of this type. Geometric rather than quantile-spaced because a
    # quantile ladder collides at the top on the sparse types (most towns permit
    # no 3-4 unit buildings at all) and because permit counts are long-tailed:
    # Chicago is three orders of magnitude above the median, and a linear ladder
    # puts every other municipality in the first bin.
    hi = nice_ceiling(xs[min(len(xs) - 1, int(0.99 * len(xs)))])
    lo = max(1, nice_ceiling(xs[len(xs) // 2]))
    if lo >= hi:
        lo = max(1, hi // 100)
    ratio = (hi / lo) ** (1 / (n - 2))
    stops = [0] + [nice_ceiling(lo * ratio ** i) for i in range(n - 1)]
    # Strictly increasing: MapLibre's `interpolate` rejects a repeated input.
    out = [stops[0]]
    for v in stops[1:]:
        out.append(max(int(v), out[-1] + 1))
    return out


def year_ranges(years) -> str:
    """[2010,2011,2012,2014] -> '2010-2012 and 2014'.  For prose, not for data."""
    years = sorted(years)
    if not years:
        return ""
    runs, run = [], [years[0], years[0]]
    for y in years[1:]:
        if y == run[1] + 1:
            run[1] = y
        else:
            runs.append(run)
            run = [y, y]
    runs.append(run)
    parts = [str(a) if a == b else f"{a}\u2013{b}" for a, b in runs]
    if len(parts) == 1:
        return parts[0]
    return ", ".join(parts[:-1]) + " and " + parts[-1]


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
    need(L.STATE_ZIP, "fetch_geo.py")
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
    # The same reference rate, per structure type. The map's neutral midpoint is
    # "the Illinois rate", and when the reader filters to 5+ unit the Illinois
    # rate they should be compared against is Illinois's 5+ unit rate -- not the
    # all-types one. Taken from the published state rows for the same reason
    # il_pct_growth is (BLOCKERS.md #2): the state file is the Census figure.
    il_by_type = dict.fromkeys(L.STRUCTURE_TYPES, 0)
    us_by_type = dict.fromkeys(L.STRUCTURE_TYPES, 0)
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
        for k in L.STRUCTURE_TYPES:
            il_by_type[k] += st[L.STATE_NAME][k]
            us_by_type[k] += st["United States"][k]
    il_pct_growth = pct(il_units, il_h1)
    us_pct_growth = pct(us_units, us_h1)
    il_pct_by_type = {k: pct(il_by_type[k], il_h1) for k in L.STRUCTURE_TYPES}
    us_pct_by_type = {k: pct(us_by_type[k], us_h1) for k in L.STRUCTURE_TYPES}
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
    print("  Illinois reference rate by structure type (the map's midpoint when "
          "the type filter is on):")
    for k in L.STRUCTURE_TYPES:
        print(f"    {k:<5} {il_by_type[k]:>9,} units  il {il_pct_by_type[k]:>7}%"
              f"   us {us_pct_by_type[k]:>7}%")

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

        # How many of the 12 months each year the permit office actually reported.
        # Census publishes a figure either way -- the "Reported and Imputed" block
        # this build reads carries Census's own imputation for a non-responding
        # office -- so a 0-month year is a real published number, but an estimate
        # rather than a count. Illinois 0-month place-years carry 14,709 units in
        # the metric window, so blanking them would throw away real data; showing
        # them without saying what they are is the error the site was making.
        mrep = months_for.get(geoid, {})
        months_by_year = {}
        for y in L.METRIC_YEARS:
            raw = str(mrep.get(y, "")).strip()
            months_by_year[y] = int(raw) if raw.isdigit() else 0
        months_sum = sum(months_by_year.values())
        months_expected = 12 * len(L.METRIC_YEARS)
        imputed_years = [y for y in L.METRIC_YEARS if months_by_year[y] == 0]
        full_years = [y for y in L.METRIC_YEARS if months_by_year[y] == 12]
        if reporting:
            months_coverage = round(months_sum / months_expected, 4)
            months_flag = (
                "none" if months_sum == 0 else
                "full" if len(full_years) == len(L.METRIC_YEARS) else
                "partial" if months_coverage >= 0.75 else
                "low"
            )
        else:
            months_coverage = None
            months_flag = None
            imputed_years = []
            full_years = []

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
            mf34_total = by_type["mf34"]
            # A place whose permit office reported no month at all across the whole
            # metric window has no observed multifamily record -- every figure it
            # has is imputed. "This town permitted zero apartments" is an advocacy
            # claim, and it is not one this data can support for those places, so
            # the flag is null rather than true. Same rule as coverage one level up.
            if months_flag == "none":
                zero_mf = None
                zero_mf3p = None
            else:
                zero_mf = (mf5p_total == 0)
                zero_mf3p = (mf34_total + mf5p_total == 0)
        else:
            # SPEC.md §1.3 / §5: absence of a permit record is not zero housing.
            by_type = {k: None for k in L.STRUCTURE_TYPES}
            units_total_2010 = mf5p_total = mf34_total = None
            pct_growth = None
            pct_by_type = {k: None for k in L.STRUCTURE_TYPES}
            zero_mf = None
            zero_mf3p = None

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
                "mf34_total": mf34_total,
                "zero_mf": zero_mf,
                "zero_mf3p": zero_mf3p,
                "months_coverage": months_coverage,
                "months_flag": months_flag,
                "n_imputed_years": len(imputed_years) if reporting else None,
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
            "mf34_total": mf34_total,
            "zero_mf": zero_mf,
            "zero_mf3p": zero_mf3p,
            "months_coverage": months_coverage,
            "months_flag": months_flag,
            "months_imputed_years": imputed_years,
            "n_full_months_years": len(full_years) if reporting else None,
            "months_note": (
                None if not reporting or not imputed_years else
                (f"This municipality's permit office reported no months to the "
                 f"Census in {year_ranges(imputed_years)}. The Census still "
                 "publishes a figure for those years \u2014 its own estimate for a "
                 "non-reporting office \u2014 so the numbers below are what the Census "
                 "published, not what the municipality counted. Treat them as a "
                 "floor."
                 + ("" if months_flag != "none" else
                    " Every year in the window is on this footing, so this place "
                    "is not counted in the zero-multifamily total."))
            ),
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
    # The state silhouette: drawn beneath the places so unincorporated land reads
    # as territory, not as a hole, and its bbox is the map's opening view.
    state_gj = json.loads(L.STATE_GEOJSON.read_text(encoding="utf-8"))
    if len(state_gj["features"]) != 1:
        raise SystemExit(f"{L.rel(L.STATE_GEOJSON)} should hold exactly one "
                         f"Illinois feature, not {len(state_gj['features'])}")
    shutil.copyfile(L.STATE_GEOJSON, L.DOCS_DATA / "state.geojson")
    state_bbox = geometry_bbox(state_gj)
    print(f"  state.geojson   : "
          f"{(L.DOCS_DATA / 'state.geojson').stat().st_size:,} bytes, "
          f"bbox {state_bbox}")

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
    n_zero_mf3p = sum(1 for ft in features if ft["properties"]["zero_mf3p"] is True)
    print(f"  Reporting places with zero 5+ unit permits: {n_zero_mf:,}")
    print(f"  Reporting places with nothing above a duplex: {n_zero_mf3p:,}")

    months_counts: dict[str, int] = defaultdict(int)
    for ft in features:
        if ft["properties"]["months_flag"]:
            months_counts[ft["properties"]["months_flag"]] += 1
    print("  Months actually reported to Census inside the metric window, across "
          "the reporting places:")
    for k in ("full", "partial", "low", "none"):
        print(f"    {k:<8} {months_counts.get(k, 0):>6,}")
    print("     A 0-month year still carries a published Census figure; it is an "
          "estimate for a")
    print("     non-reporting office, not a count, and the detail panel now says "
          "which years those are.")
    print(f"     The {months_counts.get('none', 0)} places with no reported month "
          "at all get a null zero-multifamily")
    print("     flag rather than a true one.")

    # Legend breaks fitted per structure type (see stop_ladder).
    units_stops = {
        "all": stop_ladder([ft["properties"]["units_total_2010"] for ft in features])
    }
    for k in L.STRUCTURE_TYPES:
        units_stops[k] = stop_ladder(
            [ft["properties"][f"u_{k}"] for ft in features])
    print("  Total-units legend breaks, fitted per type:")
    for k, v in units_stops.items():
        print(f"    {k:<5} {v}")
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
        f"cb_{L.TIGER_VINTAGE}_{L.STATE_FIPS}_place_500k, "
        f"cb_{L.TIGER_VINTAGE}_us_county_500k and "
        f"cb_{L.TIGER_VINTAGE}_us_state_500k",
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
        "state_bbox": state_bbox,
        "il_pct_growth": il_pct_growth,
        "us_pct_growth": us_pct_growth,
        "il_pct_growth_by_type": il_pct_by_type,
        "us_pct_growth_by_type": us_pct_by_type,
        "il_units_by_type": il_by_type,
        "units_stops": units_stops,
        "il_units_2010_ymax": il_units,
        "us_units_2010_ymax": us_units,
        "il_h1_2010": il_h1,
        "us_h1_2010": us_h1,
        "n_places": len(features),
        "coverage_counts": dict(cov_counts),
        "n_zero_mf": n_zero_mf,
        "n_zero_mf3p": n_zero_mf3p,
        "n_partial_coverage": len(partial),
        "months_counts": dict(months_counts),
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
