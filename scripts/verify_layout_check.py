#!/usr/bin/env python3
"""Prove that check_data.py's layout check (check A) actually has teeth.

Not part of the build or of SPEC.md.  It exists so that the tolerance on check A
can be shown to be loose-but-sufficient rather than merely loose: it shifts each
BPS unit-column index by one and confirms the check fails, loudly, every time.

Run: uv run scripts/verify_layout_check.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bps_layout as L

TOL = 0.005   # check_data.LAYOUT_TOLERANCE


def worst_drift() -> float:
    worst = 0.0
    for year in L.METRIC_YEARS:
        recs = L.read_place_year(year)
        state = L.read_state_year(year)[L.STATE_NAME]["units_total"]
        place = sum(r["units_total"] for r in recs)
        if state:
            worst = max(worst, abs(place - state) / state)
    return worst


def main() -> int:
    print("=" * 78)
    print("Does check A catch a wrong BPS column? (shifting each unit column by one)")
    print("=" * 78)
    baseline = worst_drift()
    print(f"\nBaseline, correct columns: worst single-year drift "
          f"{baseline * 100:.4f}%  -> tolerance {TOL * 100:.2f}%: "
          f"{'PASSES' if baseline <= TOL else 'FAILS'}\n")

    print(f"  {'perturbation':<34}{'worst drift':>14}  check A")
    failures = 0
    for key in L.STRUCTURE_TYPES:
        for shift in (-1, +1):
            orig = L.MODERN_PLACE_LAYOUT[key]
            L.MODERN_PLACE_LAYOUT[key] = orig + shift
            try:
                drift = worst_drift()
                caught = drift > TOL
            except Exception as exc:
                drift, caught = float("inf"), True
                print(f"  {key} {shift:+d} raised {type(exc).__name__}", end="  ")
            finally:
                L.MODERN_PLACE_LAYOUT[key] = orig
            failures += bool(caught)
            label = f"modern '{key}' column {shift:+d}"
            shown = "n/a" if drift == float("inf") else f"{drift * 100:.2f}%"
            print(f"  {label:<34}{shown:>14}  "
                  f"{'FAILS (good)' if caught else 'passes (BAD)'}")

    n = len(L.STRUCTURE_TYPES) * 2
    print(f"\n{failures} of {n} single-column perturbations are caught.")
    ok = (failures == n) and baseline <= TOL
    print("\nConclusion: the tolerance is loose enough to absorb the "
          f"{baseline * 100:.4f}% revision-vintage")
    print("noise documented in BLOCKERS.md #2, and still tight enough that no "
          "wrong column")
    print("position survives it." if ok else "position survives it -- EXCEPT AS SHOWN ABOVE.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
