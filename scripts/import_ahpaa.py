#!/usr/bin/env python3
"""Transcribe IHDA's 2023 AHPAA report into data/manual/ahpaa.csv.

The Affordable Housing Planning and Appeal Act list has no API (SPEC.md §3.5).
IHDA publishes it as PDFs; Impact for Equity supplied the same report as a
spreadsheet, ``data/manual/2023-AHPAA-Local-Government-Data.xlsx``. Its second
tab, "Statewide Affordability Listing", has every local government IHDA scored:
1,298 rows, 44 of them non-exempt. On 2026-10-01 every row was compared with
IHDA's published Statewide Affordability List PDF (population, year-round units,
affordable units, share) and the 44 with IHDA's non-exempt list; all matched.

This is a deterministic transcription of that supplied file, not a fetch and not
model knowledge. ``ahpaa.csv`` stays the artefact the build reads.

Places are joined by **exact** name to the Census place file, not by
``normalize_name``: that drops a trailing "City", which would put Mason City's
figures on Mason. IHDA also writes the legal type where two places share a name
("Wilmington City", "Windsor Village"), so "name type" is tried too. Two rows
need a hand mapping, and five cannot attach at all; both lists are asserted, so
a different file fails here rather than loading quietly.

    uv run scripts/import_ahpaa.py
"""

from __future__ import annotations

import csv
import json
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bps_layout as L

SHEET = "Statewide Affordability Listing"
NONEXEMPT_SHEET = "Non-Exempt Local Governments"

# IHDA name -> Census GEOID, where the names differ.
HAND_MAP = {
    "Alvin": ("1701242", "Census name: Alvan (Alvin) village."),
    "Sandoval*": ("1767444", "IHDA footnote: median real estate taxes were not "
                  "available for Sandoval in the ACS 2017-2021 estimates, so they "
                  "could not be factored into its calculation."),
}
# IHDA rows with no 2025 Census place. Alorton, Cahokia and Centreville merged
# into Cahokia Heights in 2021 (after IHDA's ACS 2017-2021 window); Clear Lake and
# Time are not in the 2025 place file. All five are exempt.
UNATTACHED = {"Alorton", "Cahokia", "Centreville", "Clear Lake", "Time"}
# Notes on a row IHDA published as it stands. The status is never changed here.
NOTES = {
    "Timberlane": ("IHDA's population is the ACS 2017-2021 estimate, 1,323. The "
                   f"2020 Census counts 906, under the {L.AHPAA_EXEMPT_POP:,} residents "
                   "below which IHDA treats a municipality as exempt."),
}


def read_sheet(ws) -> list[dict]:
    """Data rows of one tab: Place, County, Population, Total Year-Round Units,
    Total Affordable Units, Affordable Housing Share, Non-Exempt Status."""
    out = []
    for i, r in enumerate(ws.iter_rows(values_only=True)):
        if i < 6:
            continue                       # title block and the header row
        r = r[1:8]
        if r[0] and r[1] and isinstance(r[2], (int, float)):
            out.append({"place": r[0], "county": r[1], "population": r[2],
                        "year_round_units": r[3], "affordable_units": r[4],
                        "share": r[5], "status": r[6]})
    return out


def read_xlsx() -> tuple[list[dict], list[dict]]:
    """``(statewide rows, non-exempt rows)`` from the supplied workbook."""
    import openpyxl

    wb = openpyxl.load_workbook(L.AHPAA_XLSX, read_only=True, data_only=True)
    return read_sheet(wb[SHEET]), read_sheet(wb[NONEXEMPT_SHEET])


def census_municipalities() -> dict[str, list[dict]]:
    """Lower-cased "name" and "name type" -> Census municipalities (no CDPs)."""
    gj = json.loads(L.SIMPLIFIED_GEOJSON.read_text(encoding="utf-8"))
    by = defaultdict(list)
    for f in gj["features"]:
        p = f["properties"]
        name, lsad = p["NAME"], p["NAMELSAD"]
        if lsad.endswith(" CDP"):
            continue
        by[name.lower()].append(p)
        by[lsad.lower()].append(p)
    return by


def match(rows: list[dict]) -> tuple[list[tuple[dict, str, str]], list[str]]:
    by = census_municipalities()
    matched, unattached = [], []
    for r in rows:
        if r["place"] in HAND_MAP:
            g, note = HAND_MAP[r["place"]]
            matched.append((r, g, note))
            continue
        hits = {p["GEOID"] for p in by.get(r["place"].lower(), [])}
        if len(hits) == 1:
            matched.append((r, hits.pop(), NOTES.get(r["place"], "")))
        elif not hits:
            unattached.append(r["place"])
        else:
            raise SystemExit(f"{r['place']!r} ({r['county']}) matches {sorted(hits)}")
    return matched, unattached


def main() -> int:
    rows, nonexempt = read_xlsx()
    print(f"{L.rel(L.AHPAA_XLSX)}: {len(rows):,} rows in {SHEET!r}, "
          f"{len(nonexempt)} in {NONEXEMPT_SHEET!r}")
    ne_a = {(r["place"], r["county"]) for r in rows if r["status"] == "Non-Exempt"}
    ne_b = {(r["place"], r["county"]) for r in nonexempt}
    assert ne_a == ne_b, f"the two tabs disagree on non-exempt: {ne_a ^ ne_b}"
    assert {r["status"] for r in rows} == {"Exempt", "Non-Exempt"}
    for r in rows:
        assert 0 <= r["share"] <= 1, r
        if r["year_round_units"]:
            assert abs(r["affordable_units"] / r["year_round_units"] - r["share"]) < 1e-9, r
        if r["status"] == "Non-Exempt":
            assert r["share"] < L.AHPAA_SHARE_THRESHOLD, r

    matched, unattached = match(rows)
    assert set(unattached) == UNATTACHED, f"unexpected unattached rows: {unattached}"
    assert set(NOTES) <= {r["place"] for r, _, _ in matched}, "a note names no row"
    geoids = [g for _, g, _ in matched]
    assert len(geoids) == len(set(geoids)), "two IHDA rows on one place"

    with L.AHPAA_CSV.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=L.AHPAA_HEADERS)
        w.writeheader()
        for r, g, note in sorted(matched, key=lambda t: t[1]):
            w.writerow({
                "geoid": g,
                "municipality": r["place"].rstrip("*"),
                "status": r["status"],
                "source_url": L.AHPAA_SOURCE_URL,
                "as_of_date": L.AHPAA_AS_OF,
                "notes": note,
                "affordable_share": f"{r['share']:.6f}",
                "affordable_units": f"{r['affordable_units']:.2f}",
                "year_round_units": r["year_round_units"],
            })
    n_ne = sum(1 for r, _, _ in matched if r["status"] == "Non-Exempt")
    print(f"  wrote {len(matched):,} rows to {L.rel(L.AHPAA_CSV)} ({n_ne} non-exempt)")
    print(f"  not attached (no 2025 Census place): {', '.join(sorted(unattached))}")
    L.record_source(
        L.AHPAA_SOURCE_URL, L.rel(L.AHPAA_CSV),
        "Transcribed by scripts/import_ahpaa.py from data/manual/"
        "2023-AHPAA-Local-Government-Data.xlsx (supplied by Impact for Equity); "
        "every row checked against this PDF on 2026-10-01.",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
