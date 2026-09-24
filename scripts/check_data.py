#!/usr/bin/env python3
"""SPEC.md §7 data checks.  Exit 0 only if every check passes.

Prints a readable report either way.  Every check is wrapped so that a missing
file or a malformed input is reported as a FAIL with its reason, not as a
traceback -- this script has to be runnable against a half-built tree.

This script is the definition of done for the data.  It is deliberately written
to be hard to satisfy by accident: it re-reads the TIGER archive and the raw BPS
files itself rather than trusting anything the build wrote.
"""

from __future__ import annotations

import collections
import csv
import json
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bps_layout as L

JOIN_RATE_MIN = 95.0          # SPEC.md §7.3
GEOJSON_MAX_BYTES = 3 * 1024 * 1024   # SPEC.md §7.8
TOTALS_TOLERANCE = 0.005      # SPEC.md §7.6, 0.5%
OUTLIER_PCT = 200.0           # SPEC.md §7.5
# Tolerances for the extra layout check below. Not SPEC.md thresholds -- this is
# the build's own guard on the BPS column positions.
#
# Per year, 0.5%: the same tolerance SPEC.md §7.6 itself uses for reconciling
# totals, rather than a number fitted to the observed residual. It is chosen to be
# defensible rather than tight, because it does not need to be tight to do its job:
# the smallest Illinois year total in the series is 10,859 units, and reading any
# wrong column -- buildings instead of units, or a neighbouring structure type --
# moves a year total by thousands of units, i.e. by whole percent, not by tenths.
# scripts/verify_layout_check.py demonstrates that this threshold still fails
# loudly on a deliberately shifted column index.
#
# Across the whole metric window, 0.05%: an order of magnitude tighter, because a
# systematic error would accumulate rather than cancel.
LAYOUT_TOLERANCE = 0.005        # 0.5% per year
LAYOUT_TOTAL_TOLERANCE = 0.0005 # 0.05% across 2010-YMAX

W = 78


class Report:
    def __init__(self) -> None:
        self.results: list[tuple[str, str, bool]] = []
        self.lines: list[str] = []

    def out(self, text: str = "") -> None:
        self.lines.append(text)
        print(text, flush=True)

    def head(self, num: str, title: str) -> None:
        self.out("")
        self.out("-" * W)
        self.out(f"Check {num}: {title}")
        self.out("-" * W)

    def record(self, num: str, title: str, ok: bool) -> None:
        self.results.append((num, title, ok))
        self.out(f"  => {'PASS' if ok else 'FAIL'}")


R = Report()


def banner() -> None:
    R.out("=" * W)
    R.out("SPEC.md §7 -- data checks")
    R.out(f"repo: {L.ROOT}")
    R.out(f"scope: Illinois (FIPS {L.STATE_FIPS}), BPS {L.YMIN}-{L.YMAX}, "
          f"metrics {L.METRIC_START}-{L.YMAX}, TIGER cb_{L.TIGER_VINTAGE}")
    R.out("=" * W)


# The checks below run as they are defined, so the banner is printed first here
# rather than from main(), which would put it underneath the whole report.
banner()


def check(num: str, title: str, gating: bool = True):
    """Decorator: run a check, turn any exception into a readable FAIL."""
    def wrap(fn):
        R.head(num + ("" if gating else " (informational)"), title)
        try:
            ok = fn()
        except FileNotFoundError as exc:
            R.out(f"  MISSING INPUT: {exc}")
            ok = False
        except Exception:
            R.out("  ERROR while running this check:")
            for line in traceback.format_exc().splitlines():
                R.out(f"    {line}")
            ok = False
        if gating:
            R.record(num, title, bool(ok))
        else:
            R.out(f"  => {'ok' if ok else 'see above'} (not gating)")
        return fn
    return wrap


# --------------------------------------------------------------------------
# Lazily loaded shared inputs
# --------------------------------------------------------------------------

_cache: dict = {}


def geojson() -> dict:
    if "geojson" not in _cache:
        path = L.DOCS_DATA / "places.geojson"
        if not path.exists():
            raise FileNotFoundError(
                f"{L.rel(path)} does not exist. Run `uv run scripts/build.py` first."
            )
        _cache["geojson"] = json.loads(path.read_text(encoding="utf-8"))
    return _cache["geojson"]


def features() -> list[dict]:
    return geojson().get("features", [])


def props() -> list[dict]:
    return [f.get("properties") or {} for f in features()]


def meta() -> dict:
    if "meta" not in _cache:
        path = L.DOCS_DATA / "meta.json"
        if not path.exists():
            raise FileNotFoundError(
                f"{L.rel(path)} does not exist. Run `uv run scripts/build.py` first."
            )
        _cache["meta"] = json.loads(path.read_text(encoding="utf-8"))
    return _cache["meta"]


def raw_bps() -> list[dict]:
    if "raw_bps" not in _cache:
        _cache["raw_bps"] = L.read_all_place_years()
    return _cache["raw_bps"]


def _read_shard(geoid: str):
    path = L.DOCS_SHARDS / f"{geoid}.json"
    if not path.exists():
        return geoid, "MISSING"
    try:
        return geoid, json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return geoid, f"UNPARSEABLE: {exc}"


def shards() -> dict[str, dict | str]:
    """Read every shard once, in parallel.

    Checks 6 and 7 both need all of them.  Reading is latency-bound rather than
    CPU-bound -- on a cloud-synced working copy a single small file open costs
    hundreds of milliseconds -- so the reads are threaded.  Every shard is still
    opened and parsed individually; nothing is sampled or skipped.
    """
    if "shards" not in _cache:
        geoids = [p.get("geoid") for p in props()]
        with ThreadPoolExecutor(max_workers=32) as pool:
            _cache["shards"] = dict(pool.map(_read_shard, geoids))
    return _cache["shards"]


def state_years() -> dict[int, dict]:
    if "state_years" not in _cache:
        _cache["state_years"] = {y: L.read_state_year(y) for y in L.YEARS}
    return _cache["state_years"]


def unmatched_rows() -> list[dict]:
    if "unmatched" not in _cache:
        if not L.UNMATCHED_CSV.exists():
            raise FileNotFoundError(
                f"{L.rel(L.UNMATCHED_CSV)} does not exist. "
                "Run `uv run scripts/crosswalk.py` (or build.py) first."
            )
        with L.UNMATCHED_CSV.open(newline="", encoding="utf-8") as fh:
            _cache["unmatched"] = list(csv.DictReader(fh))
    return _cache["unmatched"]


def geoids_in_build() -> set[str]:
    return {p.get("geoid") for p in props()}


def fnum(v, dec=2):
    return "null" if v is None else f"{float(v):,.{dec}f}"


# --------------------------------------------------------------------------
# §7.1
# --------------------------------------------------------------------------

@check("1", "Every Illinois place in the TIGER file appears exactly once in places.geojson")
def c1():
    tiger = L.tiger_places()
    tiger_ids = [t["geoid"] for t in tiger]
    tiger_set = set(tiger_ids)
    R.out(f"  TIGER source : {L.rel(L.TIGER_ZIP)}")
    R.out(f"  TIGER places : {len(tiger_ids):,} records, {len(tiger_set):,} distinct GEOIDs")

    built = [p.get("geoid") for p in props()]
    R.out(f"  Built places : {len(built):,} features, {len(set(built)):,} distinct GEOIDs")

    dupes = sorted({g for g in built if built.count(g) > 1})
    missing = sorted(tiger_set - set(built))
    extra = sorted(set(built) - tiger_set)

    if dupes:
        R.out(f"  GEOIDs appearing more than once in places.geojson: {len(dupes)}")
        for g in dupes[:20]:
            R.out(f"    {g} x{built.count(g)}")
    if missing:
        R.out(f"  In TIGER but NOT in places.geojson: {len(missing)}")
        names = {t["geoid"]: t["namelsad"] for t in tiger}
        for g in missing[:20]:
            R.out(f"    {g}  {names.get(g, '')}")
    if extra:
        R.out(f"  In places.geojson but NOT in TIGER: {len(extra)}")
        for g in extra[:20]:
            R.out(f"    {g}")
    if not (dupes or missing or extra):
        R.out("  Exact 1:1 correspondence with the TIGER place list.")
    return not (dupes or missing or extra)


# --------------------------------------------------------------------------
# §7.2
# --------------------------------------------------------------------------

@check("2", "Every feature has a non-null coverage field with a valid value")
def c2():
    counts: dict[str, int] = {}
    bad: list[str] = []
    for p in props():
        cov = p.get("coverage")
        if cov is None or cov not in L.COVERAGE_VALUES:
            bad.append(f"{p.get('geoid')} coverage={cov!r}")
        counts[str(cov)] = counts.get(str(cov), 0) + 1
    R.out(f"  Valid values (SPEC.md §5): {', '.join(L.COVERAGE_VALUES)}")
    for k in sorted(counts, key=lambda k: -counts[k]):
        R.out(f"    {k:<20} {counts[k]:>6,}")
    if bad:
        R.out(f"  Features with a null or invalid coverage: {len(bad)}")
        for b in bad[:20]:
            R.out(f"    {b}")
    return not bad


# --------------------------------------------------------------------------
# §7.3
# --------------------------------------------------------------------------

@check("3", f"BPS join rate >= {JOIN_RATE_MIN:.0f}% of Illinois BPS units {L.METRIC_START}-{L.YMAX}")
def c3():
    built = geoids_in_build()
    reporting = {p["geoid"] for p in props() if p.get("coverage") == "reporting"}

    by_class: dict[str, int] = {}
    matched = 0
    unmatched_units: dict[str, dict] = {}
    for rec in raw_bps():
        if rec["year"] < L.METRIC_START:
            continue
        cls = L.record_class(rec)
        by_class[cls] = by_class.get(cls, 0) + rec["units_total"]
        geoid = None
        if cls == "municipal" and rec["fips_place"]:
            cand = L.STATE_FIPS + rec["fips_place"].zfill(5)
            if cand in built:
                geoid = cand
        if geoid is not None and geoid in reporting:
            matched += rec["units_total"]
        else:
            slot = unmatched_units.setdefault(
                rec["bps_id"],
                {"name": rec["name"], "county": rec["county_fips"], "cls": cls, "units": 0},
            )
            slot["units"] += rec["units_total"]

    total_all = sum(by_class.values())
    municipal = by_class.get("municipal", 0)

    R.out(f"  Illinois BPS units {L.METRIC_START}-{L.YMAX}, by record class:")
    for cls in ("municipal", "county_unincorporated", "mcd_or_unknown"):
        u = by_class.get(cls, 0)
        if u or cls == "municipal":
            share = (u / total_all * 100) if total_all else 0.0
            R.out(f"    {cls:<24} {u:>10,}  {share:6.2f}%")
    R.out(f"    {'TOTAL':<24} {total_all:>10,}  100.00%")
    R.out("")

    rate_all = (matched / total_all * 100) if total_all else 0.0
    rate_mun = (matched / municipal * 100) if municipal else 0.0
    R.out(f"  Units resolved to a place with geometry: {matched:,}")
    R.out(f"    rate against ALL Illinois BPS units      : {rate_all:9.4f}%   (spec-literal)")
    R.out(f"    rate against the MUNICIPAL place universe: {rate_mun:9.4f}%   <-- GATES")
    R.out("")
    R.out("  Why the municipal universe gates (BLOCKERS.md #1): county")
    R.out("  unincorporated-area records carry the FIPS place sentinel 99990 and are")
    R.out("  by definition the territory outside any place, so no TIGER place")
    R.out("  geometry exists for them; SPEC.md §10 puts an unincorporated-territory")
    R.out("  layer out of scope for v1. Both rates are printed so the gap is visible.")
    R.out("")

    ranked = sorted(unmatched_units.items(), key=lambda kv: -kv[1]["units"])
    R.out(f"  Top 20 unmatched BPS records by unit count ({L.METRIC_START}-{L.YMAX}):")
    R.out(f"    {'bps_id':<8} {'cnty':<5} {'units':>8}  {'class':<22} name")
    if not ranked:
        R.out("    (none - every BPS record joined)")
    for bps_id, d in ranked[:20]:
        R.out(f"    {bps_id:<8} {d['county']:<5} {d['units']:>8,}  {d['cls']:<22} {d['name']}")
    if len(ranked) > 20:
        rest = sum(d["units"] for _, d in ranked[20:])
        R.out(f"    ... {len(ranked) - 20} more unmatched records, {rest:,} units")

    ok = rate_mun >= JOIN_RATE_MIN
    if not ok:
        R.out("")
        R.out(f"  Join rate {rate_mun:.2f}% is below the {JOIN_RATE_MIN:.0f}% floor.")
        R.out("  Per SPEC.md §3.1 this must be reported, not fixed by loosening the")
        R.out("  name matching. See BLOCKERS.md.")
    return ok


# --------------------------------------------------------------------------
# §7.4
# --------------------------------------------------------------------------

@check("4", "Every place with a non-null pct_growth has H1_2010 > 0")
def c4():
    bad = []
    n = 0
    for p in props():
        if p.get("pct_growth") is None:
            continue
        n += 1
        h1 = p.get("h1_2010")
        if h1 is None or float(h1) <= 0:
            bad.append(f"{p.get('geoid')} {p.get('name')} h1_2010={h1!r}")
    R.out(f"  Places with a non-null pct_growth: {n:,}")
    if bad:
        R.out(f"  Of those, with a missing or non-positive 2010 denominator: {len(bad)}")
        for b in bad[:20]:
            R.out(f"    {b}")
    else:
        R.out("  All of them have a positive 2010 Decennial H1 housing-unit count.")
    return not bad


# --------------------------------------------------------------------------
# §7.5
# --------------------------------------------------------------------------

@check("5", f"No place has pct_growth below 0; places above {OUTLIER_PCT:.0f}% are flagged")
def c5():
    negative = []
    outliers = []
    for p in props():
        g = p.get("pct_growth")
        if g is None:
            continue
        g = float(g)
        if g < 0:
            negative.append(p)
        if g > OUTLIER_PCT:
            outliers.append(p)
    if negative:
        R.out(f"  Places with pct_growth < 0: {len(negative)}  (this is an error)")
        for p in negative[:20]:
            R.out(f"    {p.get('geoid')} {p.get('name')} {fnum(p.get('pct_growth'))}%")
    else:
        R.out("  No place has a negative pct_growth.")

    R.out("")
    R.out(f"  Flagged outliers above {OUTLIER_PCT:.0f}% -- for human review, NOT errors.")
    R.out("  Small 2010 denominators make large percentages easy; each of these is a")
    R.out("  real BPS count over a real 2010 count and may be genuine fast growth.")
    outliers.sort(key=lambda p: -float(p["pct_growth"]))
    R.out(f"    {'geoid':<9} {'pct':>10} {'units':>8} {'H1_2010':>9}  name")
    if not outliers:
        R.out("    (none)")
    for p in outliers[:40]:
        R.out(
            f"    {p.get('geoid'):<9} {float(p['pct_growth']):>9.1f}% "
            f"{int(p.get('units_total_2010') or 0):>8,} {int(p.get('h1_2010') or 0):>9,}  {p.get('name')}"
        )
    if len(outliers) > 40:
        R.out(f"    ... {len(outliers) - 40} more")
    return not negative


# --------------------------------------------------------------------------
# §7.6
# --------------------------------------------------------------------------

@check("6", "Statewide total from the per-place shards reconciles with the raw BPS rows (0.5%)")
def c6():
    raw_all = 0
    raw_by_class: dict[str, int] = {}
    for rec in raw_bps():
        if rec["year"] < L.METRIC_START:
            continue
        raw_all += rec["units_total"]
        cls = L.record_class(rec)
        raw_by_class[cls] = raw_by_class.get(cls, 0) + rec["units_total"]

    shard_total = 0
    n_shards = 0
    for geoid, shard in shards().items():
        if not isinstance(shard, dict):
            continue
        n_shards += 1
        v = shard.get("units_total_2010")
        if v is not None:
            shard_total += int(v)

    un_units = 0
    for row in unmatched_rows():
        un_units += int(row.get("units_2010_ymax") or 0)

    reconstructed = shard_total + un_units
    R.out(f"  Raw BPS Illinois units {L.METRIC_START}-{L.YMAX}      : {raw_all:>10,}")
    for cls in ("municipal", "county_unincorporated", "mcd_or_unknown"):
        if raw_by_class.get(cls):
            R.out(f"     of which {cls:<22}: {raw_by_class[cls]:>10,}")
    R.out(f"  Summed from the {n_shards:,} per-place shards : {shard_total:>10,}")
    R.out(f"  Summed from unmatched.csv                 : {un_units:>10,}")
    R.out(f"  Shards + unmatched                        : {reconstructed:>10,}")

    if raw_all == 0:
        R.out("  Raw total is zero -- nothing to reconcile against.")
        return False
    drift = abs(reconstructed - raw_all) / raw_all
    share = shard_total / raw_all * 100
    R.out("")
    R.out(f"  Conservation drift |shards+unmatched - raw| / raw = {drift * 100:.4f}%"
          f"   (tolerance {TOTALS_TOLERANCE * 100:.1f}%)")
    R.out(f"  Shards alone are {share:.2f}% of the raw statewide total; the remainder is")
    R.out("  accounted for line by line in unmatched.csv, so no units are lost silently.")
    return drift <= TOTALS_TOLERANCE


# --------------------------------------------------------------------------
# §7.7
# --------------------------------------------------------------------------

@check("7", "One parseable shard with a series array exists for every feature")
def c7():
    missing, unparseable, no_series, bad_series = [], [], [], []
    n = 0
    for geoid, shard in shards().items():
        if shard == "MISSING":
            missing.append(geoid)
            continue
        if isinstance(shard, str):
            unparseable.append(f"{geoid}: {shard}")
            continue
        n += 1
        series = shard.get("series")
        if series is None:
            no_series.append(geoid)
        elif not isinstance(series, list):
            bad_series.append(f"{geoid}: series is {type(series).__name__}, not a list")
    R.out(f"  Features in places.geojson : {len(props()):,}")
    R.out(f"  Shards found and parsed    : {n:,}  in {L.rel(L.DOCS_SHARDS)}")
    for label, items in (
        ("Shards missing", missing),
        ("Shards that do not parse", unparseable),
        ("Shards with no `series` key", no_series),
        ("Shards whose `series` is not a list", bad_series),
    ):
        if items:
            R.out(f"  {label}: {len(items)}")
            for i in items[:20]:
                R.out(f"    {i}")
    ok = not (missing or unparseable or no_series or bad_series)
    if ok:
        R.out("  Every feature has a shard, every shard parses, every shard has a series array.")
    return ok


# --------------------------------------------------------------------------
# §7.8
# --------------------------------------------------------------------------

@check("8", "docs/data/places.geojson is under 3 MB")
def c8():
    path = L.DOCS_DATA / "places.geojson"
    if not path.exists():
        raise FileNotFoundError(
            f"{L.rel(path)} does not exist. Run `uv run scripts/build.py` first."
        )
    size = path.stat().st_size
    R.out(f"  {L.rel(path)}")
    R.out(f"  {size:,} bytes  =  {size / 1024 / 1024:.2f} MB   (budget 3.00 MB)")
    return size < GEOJSON_MAX_BYTES


# --------------------------------------------------------------------------
# §7.9
# --------------------------------------------------------------------------

@check("9", "meta.json contains il_pct_growth, us_pct_growth, ymax, build_date and sources")
def c9():
    m = meta()
    required = ["il_pct_growth", "us_pct_growth", "ymax", "build_date", "sources"]
    ok = True
    for key in required:
        present = key in m
        val = m.get(key)
        if key == "sources":
            good = present and isinstance(val, list) and len(val) > 0
            shown = f"list of {len(val)}" if isinstance(val, list) else repr(val)
        else:
            good = present and val is not None
            shown = repr(val)
        ok = ok and good
        R.out(f"    {key:<16} {'ok  ' if good else 'BAD '} {shown}")
    if isinstance(m.get("sources"), list):
        for s in m["sources"][:12]:
            R.out(f"      - {s}")
    return ok


# --------------------------------------------------------------------------
# §7.10
# --------------------------------------------------------------------------

@check("10", "data/manual/ahpaa.csv exists with the correct headers (zero rows is a pass)")
def c10():
    rows, headers = L.read_ahpaa()
    R.out(f"  {L.rel(L.AHPAA_CSV)}")
    R.out(f"  Expected headers: {L.AHPAA_HEADERS}")
    R.out(f"  Actual headers  : {headers}")
    R.out(f"  Data rows       : {len(rows)}")
    if not rows:
        R.out("  Zero rows -- this is the expected shipped state (SPEC.md §3.5). The")
        R.out("  build leaves ahpaa_status null everywhere and the site disables the")
        R.out("  AHPAA filter. Nothing here is generated.")
    else:
        built = geoids_in_build()
        unknown = [r["geoid"] for r in rows if (r.get("geoid") or "").strip() not in built]
        R.out(f"  Rows whose geoid is not a built place: {len(unknown)}")
        for g in unknown[:20]:
            R.out(f"    {g!r}")
        if unknown:
            R.out("  These rows would be silently dropped; fix the geoid or the build.")
            return False
    return headers == L.AHPAA_HEADERS


# --------------------------------------------------------------------------
# Extra, disclosed: reporting-coverage and per-type scale integrity
# --------------------------------------------------------------------------

@check("B", "Months actually reported to Census are carried, and no unreported "
            "place is claimed as zero-multifamily", gating=True)
def cB():
    R.out("  Not in SPEC.md §7. Added because a BPS place-year can exist with the")
    R.out("  permit office having reported 0 of 12 months: the Census still publishes")
    R.out("  a figure (its own imputation for a non-responder), and the site was")
    R.out("  rendering those as counted zeros. SPEC.md §1.3 forbids exactly that one")
    R.out("  level up, for a year with no record at all.")
    R.out("")
    ps = props()
    sh = shards()
    reporting = [p for p in ps if p["coverage"] == "reporting"]
    ok = True

    missing = [p["geoid"] for p in reporting
               if p.get("months_coverage") is None or p.get("months_flag") is None]
    R.out(f"  B.1  Reporting places carrying months_coverage and months_flag: "
          f"{len(reporting) - len(missing):,} of {len(reporting):,}")
    if missing:
        R.out(f"       {len(missing)} without it: {missing[:10]}")
        ok = False

    out_of_range = [p["geoid"] for p in reporting
                    if p.get("months_coverage") is not None
                    and not (0.0 <= p["months_coverage"] <= 1.0)]
    if out_of_range:
        R.out(f"       months_coverage outside [0,1]: {out_of_range[:10]}")
        ok = False

    counts = collections.Counter(p.get("months_flag") for p in reporting)
    R.out("")
    R.out("  B.2  Distribution across the metric window:")
    for k in ("full", "partial", "low", "none"):
        R.out(f"         {k:<8} {counts.get(k, 0):>6,}")
    R.out("       'none' means the office reported no month in any year of the window,")
    R.out("       so every figure that place has is an estimate.")

    claimed = [p["geoid"] for p in reporting
               if p.get("months_flag") == "none" and p.get("zero_mf") is True]
    R.out("")
    R.out(f"  B.3  Places with no reported month claimed as zero-multifamily: "
          f"{len(claimed)}")
    if claimed:
        R.out(f"       {claimed[:10]}")
        R.out("       These must carry a null zero_mf, not a true one.")
        ok = False
    else:
        R.out("       None. Their zero_mf is null, and they are out of the headline count.")

    m = meta()
    n_zero = sum(1 for p in ps if p.get("zero_mf") is True)
    n_zero3p = sum(1 for p in ps if p.get("zero_mf3p") is True)
    R.out("")
    R.out(f"  B.4  meta n_zero_mf {m.get('n_zero_mf')} vs features {n_zero}   "
          f"n_zero_mf3p {m.get('n_zero_mf3p')} vs features {n_zero3p}")
    if m.get("n_zero_mf") != n_zero or m.get("n_zero_mf3p") != n_zero3p:
        R.out("       meta and the features disagree.")
        ok = False

    # zero_mf3p is the stricter flag, so it can never be true where zero_mf is not.
    contradictory = [p["geoid"] for p in ps
                     if p.get("zero_mf3p") is True and p.get("zero_mf") is not True]
    if contradictory:
        R.out(f"  B.5  'nothing above a duplex' true where 'no 5+ unit' is not: "
              f"{contradictory[:10]}")
        ok = False
    else:
        R.out("  B.5  'Nothing above a duplex' is a strict subset of 'no 5+ unit'.")

    # The shard has to carry the years, so the detail panel can name them.
    bad_shard = []
    for p in reporting[:]:
        d = sh.get(p["geoid"])
        if not isinstance(d, dict):
            continue
        n_imp = len(d.get("months_imputed_years") or [])
        if n_imp != (p.get("n_imputed_years") or 0):
            bad_shard.append(p["geoid"])
    R.out(f"  B.6  Shard imputed-year lists agree with the feature counts: "
          f"{'yes' if not bad_shard else 'NO ' + str(bad_shard[:10])}")
    if bad_shard:
        ok = False

    named = {"1714351": "Cicero", "1729756": "Glen Ellyn"}
    R.out("")
    R.out("  B.7  The two places this check was written for:")
    for geoid, label in named.items():
        d = sh.get(geoid)
        if not isinstance(d, dict):
            R.out(f"         {label}: shard missing")
            ok = False
            continue
        R.out(f"         {label:<11} months_flag={d.get('months_flag'):<8} "
              f"coverage={d.get('months_coverage')}  "
              f"3-4 unit={d.get('mf34_total')}  5+ unit={d.get('mf5p_total')}  "
              f"zero_mf={d.get('zero_mf')}")
    return ok


@check("C", "The Illinois reference rate and the legend breaks follow the "
            "structure-type filter", gating=True)
def cC():
    R.out("  Not in SPEC.md §7. Added because the map's neutral colour break and its")
    R.out("  total-units ramp were both fixed to all-types values, so filtering to a")
    R.out("  single structure type compared every municipality against the wrong")
    R.out("  reference and flattened the ramp.")
    R.out("")
    m = meta()
    ok = True

    by_type = m.get("il_pct_growth_by_type") or {}
    us_by_type = m.get("us_pct_growth_by_type") or {}
    il_units_by_type = m.get("il_units_by_type") or {}
    missing = [k for k in L.STRUCTURE_TYPES
               if k not in by_type or k not in us_by_type]
    R.out(f"  C.1  meta carries a per-type Illinois and U.S. rate for all "
          f"{len(L.STRUCTURE_TYPES)} types: {'yes' if not missing else 'NO ' + str(missing)}")
    if missing:
        return False

    # Recomputed straight from the published state rows, the same source
    # il_pct_growth uses (BLOCKERS.md #2).
    want = dict.fromkeys(L.STRUCTURE_TYPES, 0)
    for year in L.METRIC_YEARS:
        st = L.read_state_year(year)
        for k in L.STRUCTURE_TYPES:
            want[k] += st[L.STATE_NAME][k]
    R.out("")
    R.out("  C.2  Per-type Illinois units recomputed from st<YYYY>a.txt:")
    for k in L.STRUCTURE_TYPES:
        same = want[k] == il_units_by_type.get(k)
        R.out(f"         {k:<5} meta {il_units_by_type.get(k):>9,}  "
              f"recomputed {want[k]:>9,}  {'exact' if same else 'DIFFERS'}")
        if not same:
            ok = False
    total_by_type = sum(want.values())
    R.out(f"       sum over types {total_by_type:,} vs il_units_2010_ymax "
          f"{m.get('il_units_2010_ymax'):,}  "
          f"{'exact' if total_by_type == m.get('il_units_2010_ymax') else 'DIFFERS'}")
    if total_by_type != m.get("il_units_2010_ymax"):
        ok = False

    # Each rate is that type's units over the same 2010 denominator.
    R.out("")
    R.out("  C.3  Each rate is units / il_h1_2010, to 4 dp:")
    for k in L.STRUCTURE_TYPES:
        expect = round(want[k] / m["il_h1_2010"] * 100, 4)
        same = abs(expect - by_type[k]) < 1e-6
        R.out(f"         {k:<5} {by_type[k]:>8}%  expected {expect:>8}%  "
              f"{'ok' if same else 'DIFFERS'}")
        if not same:
            ok = False

    stops = m.get("units_stops") or {}
    R.out("")
    R.out("  C.4  Total-units legend breaks, one ladder per type, 7 strictly "
          "increasing stops:")
    for k in ["all", *L.STRUCTURE_TYPES]:
        v = stops.get(k)
        good = (isinstance(v, list) and len(v) == 7
                and all(isinstance(x, int) for x in v)
                and all(v[i] < v[i + 1] for i in range(len(v) - 1)))
        R.out(f"         {k:<5} {v}  {'ok' if good else 'BAD'}")
        if not good:
            ok = False
    if len({tuple(stops.get(k, ())) for k in L.STRUCTURE_TYPES}) == 1:
        R.out("       Every type got the identical ladder, which is the bug this "
              "check exists to catch.")
        ok = False
    return ok


# --------------------------------------------------------------------------
# Extra, disclosed: layout verification against the published state totals
# --------------------------------------------------------------------------

@check("A", "BPS column positions reproduce the published Illinois state totals", gating=True)
def cA():
    R.out("  Not in SPEC.md §7. Added because every number on the site depends on the")
    R.out("  BPS column positions being right, and the 2000-2006 layout is not covered")
    R.out("  by the current record-layout PDF (see bps_layout.py). Summing Illinois")
    R.out("  place records must reproduce the independently published `Illinois` row of")
    R.out(f"  st<YYYY>a.txt to within {LAYOUT_TOLERANCE * 100:.2f}% in any single "
          f"{L.METRIC_START}-{L.YMAX} year and")
    R.out(f"  {LAYOUT_TOTAL_TOLERANCE * 100:.2f}% across the window as a whole")
    R.out("  (a mis-read column would be wrong by orders of magnitude, not by units).")
    R.out("  Residual single-family-only deltas are a revision-vintage difference")
    R.out("  between the place and state files; see BLOCKERS.md #2.")
    R.out("")
    R.out("  A.1  Column labels, read from the two-row header shipped inside every")
    R.out("       annual file. This is the decisive check on the indices: it catches a")
    R.out("       one-column slip that a totals comparison cannot see, because 1-unit")
    R.out("       Buildings and 1-unit Units are nearly equal.")
    label_problems = []
    for year in L.YEARS:
        label_problems.extend(L.verify_header_labels(year))
    if label_problems:
        R.out(f"       {len(label_problems)} mislabelled column(s):")
        for pr in label_problems[:20]:
            R.out(f"         {pr}")
        ok_labels = False
    else:
        R.out(f"       All {len(L.YEARS)} years: every unit column sits under its own")
        R.out("       structure-type label and under the word 'Units'.")
        for key, want in L.EXPECTED_UNIT_LABELS.items():
            R.out(f"         {key:<5} modern col {L.MODERN_PLACE_LAYOUT[key]:>2}, "
                  f"legacy col {L.LEGACY_PLACE_LAYOUT[key]:>2}  -> {want!r} / 'Units'")
        ok_labels = True

    R.out("")
    R.out("  A.2  Totals reconciliation against the published state file.")
    R.out(f"    {'year':<6}{'era':<8}{'place-file':>12}{'state-file':>12}{'delta':>9}  status")
    ok = True
    tot_place = tot_state = 0
    for year in L.YEARS:
        recs = L.read_place_year(year)
        place_sum = sum(r["units_total"] for r in recs)
        era = recs[0]["era"] if recs else "?"
        st = state_years()[year]
        if L.STATE_NAME not in st:
            R.out(f"    {year:<6}{era:<8}{place_sum:>12,}{'missing':>12}{'':>9}  no state row")
            ok = False
            continue
        state_sum = st[L.STATE_NAME]["units_total"]
        delta = place_sum - state_sum
        gating = year >= L.METRIC_START
        drift = abs(delta) / state_sum if state_sum else 1.0
        if delta == 0:
            status = "exact"
        elif not gating:
            status = f"{drift * 100:.3f}% (BLOCKERS.md #2)"
        elif drift <= LAYOUT_TOLERANCE:
            status = f"{drift * 100:.3f}% within tolerance"
        else:
            status = f"{drift * 100:.3f}% OVER TOLERANCE"
            ok = False
        R.out(f"    {year:<6}{era:<8}{place_sum:>12,}{state_sum:>12,}{delta:>+9,}  {status}")
        if gating:
            tot_place += place_sum
            tot_state += state_sum
    R.out("")
    tot_drift = abs(tot_place - tot_state) / tot_state if tot_state else 1.0
    R.out(f"    {L.METRIC_START}-{L.YMAX} totals: place {tot_place:,} vs state "
          f"{tot_state:,}, delta {tot_place - tot_state:+,} = {tot_drift * 100:.4f}%")
    R.out(f"    per-year tolerance {LAYOUT_TOLERANCE * 100:.2f}%, "
          f"window tolerance {LAYOUT_TOTAL_TOLERANCE * 100:.2f}%")
    if tot_drift > LAYOUT_TOTAL_TOLERANCE:
        ok = False
    return ok and ok_labels


# --------------------------------------------------------------------------

def main() -> int:
    for name in ("c1", "c2", "c3", "c4", "c5", "c6", "c7", "c8", "c9", "c10",
                 "cB", "cC", "cA"):
        globals()[name]  # checks run at decoration time; this keeps ordering explicit

    R.out("")
    R.out("=" * W)
    R.out("SUMMARY")
    R.out("=" * W)
    failed = [r for r in R.results if not r[2]]
    for num, title, ok in R.results:
        R.out(f"  [{'PASS' if ok else 'FAIL'}] {num:<3} {title}")
    R.out("")
    if failed:
        R.out(f"{len(failed)} of {len(R.results)} checks FAILED: "
              f"{', '.join(n for n, _, _ in failed)}")
        R.out("Nothing was adjusted to make a check pass. Fix the build or record the")
        R.out("reason in BLOCKERS.md.")
        return 1
    R.out(f"All {len(R.results)} checks PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
