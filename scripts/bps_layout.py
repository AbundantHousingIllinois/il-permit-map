"""Shared constants, BPS record layout, and small readers.

Imported by crosswalk.py, build.py and check_data.py so that the definition of
the BPS record layout and of the "place universe" exists in exactly one place.

Record layout source of truth
-----------------------------
https://www2.census.gov/econ/bps/Documentation/placeasc.pdf  (Attachment B,
"Year-to-Date and Annual", 41 fields).  That document describes the layout used
by the annual place files from 2007 onward.

The 2000-2006 annual files carry a *different*, 38-field layout.  It is not
described by the current documentation PDF, so the column positions below were
taken from the two-row header that the Census ships inside each of those files
and then verified numerically: summing Illinois place records with these
positions reproduces the ``Illinois`` row of the independently published state
file ``st<YYYY>a.txt`` exactly for 2000, 2007-2025 (see BLOCKERS.md #2 for the
two legacy years that differ slightly).  ``verify_layout_against_state_totals``
re-runs that check as part of ``check_data.py``.
"""

from __future__ import annotations

import csv
import io
import json
import os
import re
import struct
import sys
import zipfile
from pathlib import Path

# --------------------------------------------------------------------------
# Scope constants
# --------------------------------------------------------------------------

STATE_FIPS = "17"          # Illinois
STATE_NAME = "Illinois"
BPS_REGION = "mw"          # Illinois is in the Census Midwest region
YMIN = 2000                # SPEC.md §3.1: "years 2000 through ..."
YMAX = 2025                # "... the most recent final year available".
#                            census.gov/construction/bps/annual.html, retrieved
#                            2026-09-16: "Annual data for 2025 was released on
#                            May 14, 2026."
METRIC_START = 2010        # SPEC.md §4: pct_growth numerator starts 2010
TIGER_VINTAGE = 2025       # cb_2025_17_place_500k

YEARS = list(range(YMIN, YMAX + 1))
METRIC_YEARS = list(range(METRIC_START, YMAX + 1))
# Permits-vs-built compares permits against the change in housing units between
# two decennial counts. Both counts are as of April 1 (2010 and 2020), so the
# permit decade that sits between them is calendar 2010-2019.
BUILT_YEARS = list(range(2010, 2020))
# A permits-vs-count gap larger than this share of the 2010 stock is called out
# in the detail panel. At 5%, 1.3% of fully reported places have a count that
# rose that far beyond their permits and 16% one that fell that far short.
BUILT_FLAG_PCT = 5.0

# BPS field 6 sentinels meaning "this record is not a place".
#   99990  the county's unincorporated area
#   00000  no FIPS place code assigned to this permit office
# Verified for Illinois: every modern-era record carrying either sentinel is a
# county unincorporated area, a whole-county permit office or a township, and not
# one of them is a municipality.  See BLOCKERS.md #1.
NON_PLACE_FIPS = {"", "00000", "99990"}
UNINCORPORATED_SENTINEL = "99990"

STRUCTURE_TYPES = ("sf", "du", "mf34", "mf5p")
STRUCTURE_LABELS = {
    "sf": "Single-family",
    "du": "Duplex (2 units)",
    "mf34": "3-4 units",
    "mf5p": "5+ units",
}

COVERAGE_VALUES = ("reporting", "no_permit_office", "unmatched")

# --------------------------------------------------------------------------
# Paths
# --------------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent.parent
RAW = ROOT / "data" / "raw"
RAW_BPS_PLACE = RAW / "bps" / "place"
RAW_BPS_STATE = RAW / "bps" / "state"
RAW_BPS_DOC = RAW / "bps" / "doc"
RAW_CENSUS = RAW / "census"
RAW_GEO = RAW / "geo"
PROCESSED = ROOT / "data" / "processed"
MANUAL = ROOT / "data" / "manual"
DOCS = ROOT / "docs"
DOCS_DATA = DOCS / "data"
DOCS_SHARDS = DOCS_DATA / "places"
SOURCES_MD = ROOT / "data" / "SOURCES.md"
AHPAA_CSV = MANUAL / "ahpaa.csv"
AHPAA_HEADERS = ["geoid", "municipality", "status", "source_url", "as_of_date", "notes"]

TIGER_ZIP = RAW_GEO / f"cb_{TIGER_VINTAGE}_{STATE_FIPS}_place_500k.zip"
SIMPLIFIED_GEOJSON = PROCESSED / "places_simplified.geojson"
# The state outline, like the county layer, is published only as a national
# archive at this vintage; it is filtered to Illinois when simplified.
STATE_ZIP = RAW_GEO / f"cb_{TIGER_VINTAGE}_us_state_500k.zip"
STATE_GEOJSON = PROCESSED / "state_simplified.geojson"

# State legislative districts. TIGER cb_2025 carries LSY 2024: the map adopted in
# 2021 and in force from the 2022 election, which is the map the current General
# Assembly was elected on. Legislators are the Open States "current" bulk file.
CHAMBERS = {
    "senate": {"zip_key": "sldu", "field": "SLDUST", "n": 59, "openstates": "upper",
               "label": "State Senate", "title": "Sen."},
    "house": {"zip_key": "sldl", "field": "SLDLST", "n": 118, "openstates": "lower",
              "label": "State House", "title": "Rep."},
}
def district_zip(chamber: str) -> Path:
    return RAW_GEO / f"cb_{TIGER_VINTAGE}_{STATE_FIPS}_{CHAMBERS[chamber]['zip_key']}_500k.zip"
def district_geojson(chamber: str) -> Path:
    return PROCESSED / f"{CHAMBERS[chamber]['zip_key']}_simplified.geojson"
LEGISLATORS_CSV = RAW / "legislators" / "il.csv"
# data/SOURCES.md keeps the FIRST retrieval of a URL, but "current legislators"
# is only as current as the latest fetch, so that date is kept beside the file.
LEGISLATORS_RETRIEVED = RAW / "legislators" / "retrieved.txt"
LEGISLATOR_OVERRIDES_CSV = MANUAL / "legislator_overrides.csv"
LEGISLATOR_OVERRIDE_HEADERS = ["chamber", "district", "field", "value", "source_url",
                               "as_of_date", "notes"]
# A place is listed under a district when at least this share of its land area
# falls inside it. Below 1% the overlaps are boundary slivers; the share itself is
# carried so a partial member is never presented as a whole one.
DISTRICT_SHARE_MIN = 0.01
CROSSWALK_CSV = PROCESSED / "crosswalk.csv"
UNMATCHED_CSV = PROCESSED / "unmatched.csv"
PERMITS_CSV = PROCESSED / "permits_tidy.csv"


def place_file(year: int) -> Path:
    return RAW_BPS_PLACE / f"{BPS_REGION}{year}a.txt"


def state_file(year: int) -> Path:
    return RAW_BPS_STATE / f"st{year}a.txt"


# --------------------------------------------------------------------------
# BPS annual place-file layouts (0-based column indices)
# --------------------------------------------------------------------------

# 2007-2025.  placeasc.pdf Attachment B.
MODERN_PLACE_LAYOUT = {
    "n_fields": 41,
    "survey_date": 0,
    "state_fips": 1,
    "bps_id": 2,          # 6-digit Building Permits Survey ID
    "county_fips": 3,     # 3-digit FIPS county
    "census_place": 4,    # 4-digit Census place code
    "fips_place": 5,      # 5-digit FIPS place code
    "fips_mcd": 6,        # 5-digit FIPS MCD code
    "pop": 7,
    "csa": 8,
    "cbsa": 9,
    "footnote": 10,
    "central_city": 11,
    "zip": 12,
    "region": 13,
    "division": 14,
    "months_reported": 15,
    "name": 16,
    # "Reported and Imputed Data" (fields 18-29 in the PDF's 1-based numbering).
    # These are Census's published figures and include Census's own imputation
    # for nonresponding permit offices; they are what the state totals are built
    # from.  Using them is not us estimating anything.
    "sf": 18,             # 101-Units
    "du": 21,             # 103-Units
    "mf34": 24,           # 104-Units
    "mf5p": 27,           # 105-Units
}

# 2000-2006.  Derived from the two-row header shipped inside the files:
#   row 1: Survey,State,6-Digit,County,Place,MSA/,PMSA,Central,Zip,Region,
#          Division,Number of,,Place,,1-unit,...
#   row 2: Date,Code,ID,Code,Code,CMSA,Code,City,Code,Code,Code,Months Rep,,
#          Name,Bldgs,Units,Value,...
# No FIPS place code and no FIPS MCD code in this era; MSA/CMSA and PMSA sit
# where CSA/CBSA later do, and there is an unnamed empty column before the place
# name.  Verified numerically against the published state totals.
LEGACY_PLACE_LAYOUT = {
    "n_fields": 38,
    "survey_date": 0,
    "state_fips": 1,
    "bps_id": 2,
    "county_fips": 3,
    "census_place": 4,
    "fips_place": None,   # absent in this era
    "fips_mcd": None,
    "msa_cmsa": 5,
    "pmsa": 6,
    "central_city": 7,
    "zip": 8,
    "region": 9,
    "division": 10,
    "months_reported": 11,
    "name": 13,
    "sf": 15,
    "du": 18,
    "mf34": 21,
    "mf5p": 24,
}

# State-level annual file st<YYYY>a.txt.  placeasc.pdf's sibling stateasc.pdf;
# header shipped in the file:
#   Survey Date, FIPS State, Region, Division, State Name,
#   then 1-unit(Bldgs,Units,Value), 2-units, 3-4 units, 5+ units (imputed),
#   then the same four groups "reported only".
STATE_LAYOUT = {
    "n_fields": 29,
    "survey_date": 0,
    "state_fips": 1,
    "region": 2,
    "division": 3,
    "name": 4,
    "sf": 6,
    "du": 9,
    "mf34": 12,
    "mf5p": 15,
}


def layout_for(n_fields: int) -> dict:
    if n_fields == MODERN_PLACE_LAYOUT["n_fields"]:
        return MODERN_PLACE_LAYOUT
    if n_fields == LEGACY_PLACE_LAYOUT["n_fields"]:
        return LEGACY_PLACE_LAYOUT
    raise ValueError(
        f"Unrecognised BPS annual place record width: {n_fields} fields. "
        f"Known layouts are {LEGACY_PLACE_LAYOUT['n_fields']} (2000-2006) and "
        f"{MODERN_PLACE_LAYOUT['n_fields']} (2007-). Re-read "
        "https://www2.census.gov/econ/bps/Documentation/placeasc.pdf before "
        "assuming column positions."
    )


# --------------------------------------------------------------------------
# Reading BPS files
# --------------------------------------------------------------------------

def _int_or_zero(raw: str) -> int:
    """Parse a BPS unit count.

    BPS unit fields are administrative counts and are always populated in the
    annual files; an empty field means the structure type had no permits, which
    genuinely is zero *for a reporting office*.  A place with no BPS record at
    all is a different thing entirely and is handled as coverage, never as zero
    (SPEC.md §1.3, §5).
    """
    raw = (raw or "").strip()
    if raw == "":
        return 0
    return int(raw.replace(",", ""))


# Historic place names carry appended footnote markers (placeasc.pdf §D).
_NAME_FOOTNOTES = re.compile(r"\s*(\(N\)|#|@\d)+\s*$")


_PLACE_CACHE: dict[tuple[int, str], list[dict]] = {}
_HEADER_CACHE: dict[int, tuple[list[str], list[str], int]] = {}


def read_place_year(year: int, state_fips: str = STATE_FIPS) -> list[dict]:
    """Return one dict per BPS place record for ``state_fips`` in ``year``.

    Cached per (year, state): the checks read the same years several times and
    these are ~1 MB text files.  Callers treat the records as read-only.
    """
    ck = (year, state_fips)
    if ck in _PLACE_CACHE:
        return _PLACE_CACHE[ck]
    path = place_file(year)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing. Run `uv run scripts/fetch_bps.py` first."
        )
    out: list[dict] = []
    with path.open(newline="", encoding="latin-1") as fh:
        for row in csv.reader(fh):
            # Skip the two header rows and the blank separator row.
            if len(row) < 20 or not row[0].strip().isdigit():
                continue
            lay = layout_for(len(row))
            if row[lay["state_fips"]].strip() != state_fips:
                continue
            fips_place = (
                row[lay["fips_place"]].strip() if lay["fips_place"] is not None else ""
            )
            raw_name = row[lay["name"]].strip()
            rec = {
                "year": year,
                "state_fips": state_fips,
                "bps_id": row[lay["bps_id"]].strip(),
                "county_fips": row[lay["county_fips"]].strip().zfill(3),
                "census_place": row[lay["census_place"]].strip(),
                "fips_place": fips_place,
                "fips_mcd": (
                    row[lay["fips_mcd"]].strip() if lay["fips_mcd"] is not None else ""
                ),
                "name_raw": raw_name,
                "name": _NAME_FOOTNOTES.sub("", raw_name),
                "months_reported": row[lay["months_reported"]].strip(),
                "era": "modern" if lay is MODERN_PLACE_LAYOUT else "legacy",
            }
            for key in STRUCTURE_TYPES:
                rec[key] = _int_or_zero(row[lay[key]])
            rec["units_total"] = sum(rec[k] for k in STRUCTURE_TYPES)
            out.append(rec)
    _PLACE_CACHE[ck] = out
    return out


def read_all_place_years(years=None, state_fips: str = STATE_FIPS) -> list[dict]:
    recs: list[dict] = []
    for year in (years if years is not None else YEARS):
        recs.extend(read_place_year(year, state_fips=state_fips))
    return recs


def read_state_year(year: int) -> dict[str, dict]:
    """Return ``{state-or-aggregate name: {sf, du, mf34, mf5p, units_total}}``.

    The state file carries real states plus published aggregate rows including
    ``United States``, which SPEC.md §4 needs for ``us_pct_growth``.
    """
    path = state_file(year)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing. Run `uv run scripts/fetch_bps.py` first."
        )
    lay = STATE_LAYOUT
    out: dict[str, dict] = {}
    with path.open(newline="", encoding="latin-1") as fh:
        for row in csv.reader(fh):
            if len(row) < lay["n_fields"]:
                continue
            date = row[lay["survey_date"]].strip()
            if not (len(date) == 6 and date.isdigit()):
                continue
            rec = {k: _int_or_zero(row[lay[k]]) for k in STRUCTURE_TYPES}
            rec["units_total"] = sum(rec[k] for k in STRUCTURE_TYPES)
            out[row[lay["name"]].strip()] = rec
    return out


# --------------------------------------------------------------------------
# The place universe (SPEC.md §3.1 / §10, BLOCKERS.md #1)
# --------------------------------------------------------------------------

MUNICIPAL_BPS_IDS: set[str] = set()   # filled by ``learn_municipal_ids``


def learn_municipal_ids(records) -> set[str]:
    """Collect the BPS ids that the modern-era files prove are municipalities.

    The 2000-2006 files carry no FIPS place code at all, so the only way to know
    whether a legacy record is a municipality or a township is the 6-digit BPS id,
    which is stable across the whole series (verified: not one Illinois BPS id
    changes its FIPS place code between 2007 and 2025).
    """
    MUNICIPAL_BPS_IDS.clear()
    for rec in records:
        if rec.get("era") == "modern":
            fips = (rec.get("fips_place") or "").strip().zfill(5)
            if fips not in NON_PLACE_FIPS:
                MUNICIPAL_BPS_IDS.add(rec["bps_id"])
    return MUNICIPAL_BPS_IDS


_UNINC_NAME = re.compile(r"unincorporated\s+area\s*$", re.I)
_COUNTYWIDE_NAME = re.compile(r"\bcounty(\s+part)?\s*$", re.I)
_TOWNSHIP_NAME = re.compile(r"\b(township|twp)\.?\s*$", re.I)


def is_unincorporated_name(name: str) -> bool:
    return bool(_UNINC_NAME.search((name or "").strip()))


def record_class(rec: dict) -> str:
    """Classify a BPS record by whether a TIGER *place* geometry can exist for it.

    ``municipal``
        Carries a real 5-digit FIPS place code, or is a legacy-era record whose
        BPS id the modern-era files prove is a municipality.  Eligible to join,
        and the only class counted in the join-rate denominator.
    ``county_unincorporated``
        The county permit office reporting for territory outside any
        municipality.  No place geometry exists for it by definition, and
        SPEC.md §10 puts an unincorporated-territory layer out of scope for v1.
    ``county_wide``
        A whole-county permit office, e.g. ``Perry County``.
    ``township``
        A township (MCD) permit office, e.g. ``Murdock township``.
    ``other_non_place``
        No place code and none of the above patterns.  Reported, never guessed at.

    Nothing outside ``municipal`` is folded into a place metric or a statewide
    aggregate; every such record is written to unmatched.csv with its class as
    the reason, so the size of each bucket stays visible.
    """
    fips_place = (rec.get("fips_place") or "").strip()
    fips_norm = fips_place.zfill(5) if fips_place else ""
    name = (rec.get("name") or "").strip()

    if fips_norm and fips_norm not in NON_PLACE_FIPS:
        return "municipal"
    if fips_place == "" and rec.get("era") == "legacy":
        # Legacy era has no FIPS place column at all; fall back to the BPS id.
        if rec["bps_id"] in MUNICIPAL_BPS_IDS:
            return "municipal"
    if fips_norm == UNINCORPORATED_SENTINEL or is_unincorporated_name(name):
        return "county_unincorporated"
    if _COUNTYWIDE_NAME.search(name):
        return "county_wide"
    if _TOWNSHIP_NAME.search(name):
        return "township"
    return "other_non_place"


NON_PLACE_CLASSES = (
    "county_unincorporated", "county_wide", "township", "other_non_place",
)


# --------------------------------------------------------------------------
# Name normalisation for the fallback match (SPEC.md §3.1)
# --------------------------------------------------------------------------

# Legal-status suffixes that TIGER and BPS spell differently for the same place.
_SUFFIXES = (
    "village",
    "city",
    "town",
    "township",
    "borough",
    "cdp",
    "municipality",
)

_PUNCT = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")


def normalize_name(name: str) -> str:
    """Normalise a place name for matching.

    Lowercases, strips BPS footnote markers and punctuation, expands the common
    abbreviations that differ between BPS and TIGER, and drops one trailing
    legal-status word.  Deliberately conservative: it does not do fuzzy or
    edit-distance matching, because a wrong name match silently attributes one
    municipality's permits to another (SPEC.md §3.1).
    """
    s = _NAME_FOOTNOTES.sub("", (name or "").strip()).lower()
    s = s.replace("&", " and ")
    s = _PUNCT.sub(" ", s)
    s = _WS.sub(" ", s).strip()
    # Directional and generic abbreviations.
    words = []
    repl = {
        "st": "saint", "ste": "saint", "mt": "mount", "ft": "fort",
        "n": "north", "s": "south", "e": "east", "w": "west",
        "hts": "heights", "pk": "park", "vlg": "village", "spgs": "springs",
    }
    for w in s.split():
        words.append(repl.get(w, w))
    s = " ".join(words)
    parts = s.split()
    if len(parts) > 1 and parts[-1] in _SUFFIXES:
        parts = parts[:-1]
    return " ".join(parts)


# --------------------------------------------------------------------------
# Minimal DBF reader, so TIGER can be read without a GIS dependency
# --------------------------------------------------------------------------

def read_dbf_from_zip(zip_path: Path, member_suffix: str = ".dbf") -> list[dict]:
    """Read the attribute table of a shapefile inside a zip.

    Used so that ``check_data.py`` can read the authoritative TIGER GEOID list
    straight from the downloaded archive rather than from anything the build
    produced.
    """
    if not zip_path.exists():
        raise FileNotFoundError(
            f"{zip_path} is missing. Run `uv run scripts/fetch_geo.py` first."
        )
    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in zf.namelist() if n.lower().endswith(member_suffix)]
        if not names:
            raise ValueError(f"No {member_suffix} member inside {zip_path}")
        data = zf.read(names[0])
    return _parse_dbf(data)


def _parse_dbf(data: bytes) -> list[dict]:
    n_records, header_len, record_len = struct.unpack("<IHH", data[4:12])
    fields = []
    pos = 32
    while data[pos] != 0x0D:
        raw = data[pos:pos + 32]
        name = raw[:11].split(b"\x00")[0].decode("latin-1")
        ftype = chr(raw[11])
        flen = raw[16]
        fields.append((name, ftype, flen))
        pos += 32
    out = []
    base = header_len
    for i in range(n_records):
        start = base + i * record_len
        rec = data[start:start + record_len]
        if not rec or rec[:1] == b"*":       # deleted record
            continue
        off = 1
        row = {}
        for name, ftype, flen in fields:
            val = rec[off:off + flen].decode("latin-1").strip()
            off += flen
            row[name] = val
        out.append(row)
    return out


def tiger_places(zip_path: Path | None = None) -> list[dict]:
    """Authoritative Illinois place list from the TIGER cartographic archive."""
    rows = read_dbf_from_zip(zip_path or TIGER_ZIP)
    out = []
    for r in rows:
        geoid = r.get("GEOID") or r.get("GEOIDFQ", "")[-7:]
        out.append({
            "geoid": geoid,
            "name": r.get("NAME", ""),
            "namelsad": r.get("NAMELSAD", ""),
            "stusps": r.get("STUSPS", ""),
            "lsad": r.get("LSAD", ""),
            "aland": r.get("ALAND", ""),
        })
    return out


# --------------------------------------------------------------------------
# AHPAA (SPEC.md §3.5)
# --------------------------------------------------------------------------

def read_ahpaa() -> tuple[list[dict], list[str]]:
    """Return ``(rows, headers)``.  Zero rows is a normal, passing state."""
    if not AHPAA_CSV.exists():
        raise FileNotFoundError(f"{AHPAA_CSV} is missing; it is part of the repo.")
    with AHPAA_CSV.open(newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        headers = reader.fieldnames or []
        rows = [r for r in reader if any((v or "").strip() for v in r.values())]
    return rows, headers


# --------------------------------------------------------------------------
# Provenance (SPEC.md §1.7)
# --------------------------------------------------------------------------

def record_source(url: str, produced: str, note: str = "") -> None:
    """Append one row to data/SOURCES.md, de-duplicated on (url, produced)."""
    import datetime

    today = datetime.date.today().isoformat()
    SOURCES_MD.parent.mkdir(parents=True, exist_ok=True)
    header = (
        "# Sources\n\n"
        "Every URL this build downloads, the date it was retrieved, and the file\n"
        "it produced (SPEC.md §1.7). Appended automatically by the fetch scripts.\n\n"
        "| URL | Retrieved | File produced | Note |\n"
        "|---|---|---|---|\n"
    )
    if not SOURCES_MD.exists():
        SOURCES_MD.write_text(header, encoding="utf-8")
    text = SOURCES_MD.read_text(encoding="utf-8")
    key = f"| {url} |"
    for line in text.splitlines():
        if line.startswith(key) and f"| {produced} |" in line:
            return
    with SOURCES_MD.open("a", encoding="utf-8") as fh:
        fh.write(f"| {url} | {today} | {produced} | {note} |\n")


def rel(path: Path) -> str:
    try:
        return str(Path(path).resolve().relative_to(ROOT))
    except ValueError:
        return str(path)


# --------------------------------------------------------------------------
# Downloading
# --------------------------------------------------------------------------

def download(url: str, dest: Path, note: str = "", force: bool = False) -> bool:
    """Fetch ``url`` to ``dest`` unless already cached.  Records provenance.

    Returns True if a network request was made.  Fetch scripts are idempotent so
    that re-running them is cheap and so that ``build.py`` never needs the
    network (SPEC.md §8).
    """
    import requests

    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0 and not force:
        print(f"  cached   {rel(dest)}")
        record_source(url, rel(dest), note)
        return False
    print(f"  fetching {url}")
    resp = requests.get(url, timeout=180, headers={
        "User-Agent": "il-map-permits build (Abundant Housing Illinois)"
    })
    resp.raise_for_status()
    tmp = dest.with_suffix(dest.suffix + ".part")
    tmp.write_bytes(resp.content)
    tmp.replace(dest)
    print(f"  wrote    {rel(dest)}  ({len(resp.content):,} bytes)")
    record_source(url, rel(dest), note)
    return True


def census_api(url_no_key: str, dest: Path, note: str, force: bool = False) -> bool:
    """Fetch a Census API URL, adding the key from the environment.

    SPEC.md §3.2: read ``CENSUS_API_KEY`` from the environment, never hardcode
    it, never commit it.  The URL recorded in SOURCES.md is the key-free one.
    """
    import requests

    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists() and dest.stat().st_size > 0 and not force:
        print(f"  cached   {rel(dest)}")
        record_source(url_no_key, rel(dest), note)
        return False
    key = os.environ.get("CENSUS_API_KEY", "").strip()
    url = url_no_key + (f"&key={key}" if key else "")
    if not key:
        print("  note: CENSUS_API_KEY is not set; querying unauthenticated "
              "(rate limited).")
    print(f"  fetching {url_no_key}")
    resp = requests.get(url, timeout=180)
    resp.raise_for_status()
    try:
        payload = resp.json()
    except Exception as exc:
        raise RuntimeError(
            f"Census API did not return JSON for {url_no_key}: "
            f"{resp.text[:400]!r}"
        ) from exc
    dest.write_text(json.dumps(payload), encoding="utf-8")
    print(f"  wrote    {rel(dest)}  ({len(payload) - 1:,} data rows)")
    record_source(url_no_key, rel(dest), note)
    return True


def read_census_json(path: Path) -> list[dict]:
    """Census API JSON -> list of dicts keyed by the header row."""
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing. Run `uv run scripts/fetch_census.py` first."
        )
    rows = json.loads(path.read_text(encoding="utf-8"))
    header, *body = rows
    return [dict(zip(header, r)) for r in body]


# --------------------------------------------------------------------------
# Header-label verification
# --------------------------------------------------------------------------

# In both eras the two-row header shipped inside the annual place file happens to
# put the structure-type label of row 1 and the word "Units" of row 2 at exactly
# the column the units figure occupies.  That makes the header a direct check on
# the column indices, independent of any totals comparison -- which matters
# because 1-unit Buildings and 1-unit Units are nearly equal, so a one-column slip
# there is invisible to a totals check.
EXPECTED_UNIT_LABELS = {
    "sf": "1-unit",
    "du": "2-units",
    "mf34": "3-4 units",
    "mf5p": "5+ units",
}


def read_header_rows(year: int) -> tuple[list[str], list[str]]:
    if year in _HEADER_CACHE:
        r1, r2, _ = _HEADER_CACHE[year]
        return r1, r2
    path = place_file(year)
    if not path.exists():
        raise FileNotFoundError(
            f"{path} is missing. Run `uv run scripts/fetch_bps.py` first."
        )
    rows = []
    with path.open(newline="", encoding="latin-1") as fh:
        for row in csv.reader(fh):
            rows.append(row)
            if len(rows) == 2:
                break
    if len(rows) < 2:
        raise ValueError(f"{path} has fewer than two header rows.")
    _HEADER_CACHE[year] = (rows[0], rows[1], 0)
    return rows[0], rows[1]


def verify_header_labels(year: int) -> list[str]:
    """Return a list of problems; empty means every unit column is correctly named."""
    row1, row2 = read_header_rows(year)
    # Determine the era from a data row, not from the year, so a changed file is caught.
    recs = read_place_year(year)
    if not recs:
        return [f"{year}: no Illinois data rows found"]
    lay = (MODERN_PLACE_LAYOUT if recs[0]["era"] == "modern"
           else LEGACY_PLACE_LAYOUT)

    problems = []
    for key, want in EXPECTED_UNIT_LABELS.items():
        idx = lay[key]
        got1 = (row1[idx].strip() if idx < len(row1) else "<past end>")
        got2 = (row2[idx].strip() if idx < len(row2) else "<past end>")
        if got1.lower() != want.lower():
            problems.append(
                f"{year}: column {idx} for '{key}' is headed {got1!r} in the "
                f"structure-type row, expected {want!r}"
            )
        if got2.lower() != "units":
            problems.append(
                f"{year}: column {idx} for '{key}' is headed {got2!r} in the "
                f"measure row, expected 'Units' (reading Buildings or Valuation "
                "by mistake)"
            )
    return problems
