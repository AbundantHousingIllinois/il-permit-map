# Blockers and unresolved items

Written as encountered, per SPEC.md §1.1. Nothing here was filled in with a
plausible number.

---

## 1. `county_unincorporated` BPS records have no place geometry — join-rate denominator

**Status:** resolved by explicit scope decision; the numbers are reported both ways.

The Illinois BPS place files contain two kinds of record:

1. **Permit-issuing municipalities** — a real 5-digit FIPS place code in field 6.
2. **Records that are not places.** Field 6 carries a sentinel (`99990`, or
   `00000` meaning no place code assigned), and the name says what the record
   actually is: `Winnebago County Unincorporated Area`, `Perry County`,
   `Murdock township`. Every one of the 78 Illinois records carrying a sentinel
   was inspected: all are county unincorporated areas, whole-county permit
   offices or townships. Not one is a municipality.

Over 2010–2025, Illinois BPS-reported units split:

| Class | Units | Share |
|---|---|---|
| `municipal` | 272,726 | 91.74% |
| `county_unincorporated` | 23,913 | 8.04% |
| `county_wide` | 623 | 0.21% |
| `township` | 11 | 0.00% |
| **Total** | **297,273** | 100% |

Nothing outside `municipal` **can** join to a TIGER *place* geometry: an
unincorporated area is by definition the territory that is not in a place, a
county permit office covers a county, and a township is a different geography
entirely. SPEC.md §10 puts a "county-balance layer for unincorporated territory"
explicitly out of scope for v1, so there is no geometry in this build for any of
those units to land on.

Read literally, SPEC.md §7 check 3 ("at least 95% of Illinois BPS-reported
units, 2010–YMAX, resolve to a place with geometry") is unsatisfiable: the
ceiling is 91.74%, and no amount of correct joining reaches 95%.

**Decision.** The join rate is gated on the *municipal* universe — the set of
BPS records that can in principle have a place geometry. `check_data.py` prints
**both** rates side by side and labels which one gates, so the 8.26% that cannot
join is visible rather than buried. Every non-municipal record is written to
`data/processed/unmatched.csv` with its class as the `reason`
(`county_unincorporated`, `county_wide`, `township`), kept distinct from genuine
join failures (`no_tiger_match`, `ambiguous_name`), so §3.1's "write every
unmatched BPS record" holds literally. Their units are excluded from every place
metric and every statewide aggregate used for the map.

On the municipal universe the join rate is **99.999%** using the 6-digit BPS id
and the FIPS place code alone, before any name matching — so the 95% floor is met
with room to spare and the name fallback is doing no heavy lifting.

This is deviation #1 in the final deviation list. It is a scope consequence of
§10, not a loosened check.

---

## 2. 2000–2006 place-file sums do not exactly equal the published state totals

**Status:** logged, not chased. No effect on any 2010–YMAX metric.

Summing Illinois place records and comparing to the `Illinois` row of the
published state file `st<YYYY>a.txt`:

| Year | Place-file sum (1-unit) | State file (1-unit) | Delta |
|---|---|---|---|
| 2000 | 37,817 | 37,817 | 0 |
| 2003 | 45,823 | 45,379 | +444 |
| 2006 | 37,907 | 37,903 | +4 |
| 2007 | 24,511 | 24,511 | 0 |
| 2010 | 7,624 | 7,624 | 0 |
| 2024 | 10,326 | 10,326 | 0 |
| 2025 | 10,821 | 10,821 | 0 |

Five years inside the metric window also differ, all in single-family only and
all with the place file slightly **higher**:

| Year | Delta (units) | Share of that year |
|---|---|---|
| 2013 | +10 | 0.064% |
| 2014 | +6 | 0.029% |
| 2017 | +1 | 0.004% |
| 2018 | +3 | 0.014% |
| 2022 | +1 | 0.005% |
| **2010–2025 total** | **+21** | **0.0071%** |

Eleven of the sixteen metric years reconcile exactly. The cause was investigated
and **not** determined from the published documentation:

- It is not a column-position error. A mis-read column would be wrong by orders
  of magnitude, and could not be exact in eleven years and off by one unit in
  others.
- It is not the `(N)` universe footnote from `Documentation/placeasc.pdf` §D. No
  Illinois place record in any year carries an `(N)`, `#` or `@` marker, and 2014
  and 2018 introduce no new BPS ids at all yet still differ.
- The most likely remaining explanation is a revision-vintage difference: BPS
  revises annual data as late reports and corrections arrive, and the place-level
  and state-level annual files are not guaranteed to be republished in lockstep.
  Nothing in the documentation states this, so it is recorded as a hypothesis,
  not as a finding.

Consequences and how it is kept visible:

- `il_pct_growth` uses the **published** `Illinois` state row (297,252 units
  2010–2025), not the place-file sum, so the headline reference rate is the
  Census figure.
- The per-place numbers, and therefore the map, come from the place file. The
  0.0071% gap is 70x inside the 0.5% that SPEC.md §7.6 allows.
- `check_data.py` check A prints the reconciliation for every year 2000–2025 and
  fails if any metric year drifts past 0.05%, so a real parsing regression would
  be caught long before this noise floor.
- `units_total_2000` (the "two decades" figure) may run very slightly above the
  Census-published state total for 2003 and 2006 as well.

---

## 3. Three Illinois municipalities in BPS have no TIGER 2025 place geometry

**Status:** unresolved by design. 3 units affected out of 272,726. Reported, not patched.

These BPS records carry a real 5-digit FIPS place code, but that code no longer
exists in `cb_2025_17_place_500k`:

| BPS id | FIPS place | Name | County | Units 2010–2025 | Units 2000–2025 |
|---|---|---|---|---|---|
| 135700 | 12203 | Centreville | St. Clair | 3 | 124 |
| 113200 | 10370 | Cahokia village | St. Clair | 0 | 25 |
| 326500 | 31992 | Gulf Port village | Henderson | 0 | 0 |

Cahokia and Centreville stopped existing as separate municipalities; their
territory is now inside a different place with a different FIPS code. Attributing
their permits to a successor municipality would require a municipal-succession
record, which is not one of the sources in SPEC.md §3, and inventing the link
would credit one municipality with another's permits.

So these three records stay in `data/processed/unmatched.csv` with
`reason=no_tiger_match` and their units are excluded. The successor place shows
only the permits filed under its own BPS record, which is what the source data
actually says.

## 4. County label for places with no BPS record is derived from geometry

**Status:** resolved; the method is recorded on every shard.

BPS records carry a FIPS county code, so a `reporting` place gets its county from
the permit record itself. A place with no BPS record has no such field, and none
of the sources in SPEC.md §3 is a place-to-county lookup. The county label for
those places is therefore derived: the place's interior point is tested against
the TIGER county layer already downloaded for the county outlines.

This is a geometric computation on published boundaries, not an estimate, but it
is not an authoritative assignment either — a place straddling a county line is
labelled with the county containing its interior point. Every shard carries
`county_source` (`bps_record` or `derived_from_geometry`) so a reader can tell
which is which.
