# Roadmap

Ideas that have been asked for and not built. `BLOCKERS.md` is what is unresolved
about the site as it stands; this file is what the site does not do yet. It sits
at the repo root, outside `docs/`, so it is not published with the site.

Each item says where it came from and what has to be decided before it starts.

**Austin's feedback, 2026-10-02.** He answered items 1, 2 and 4, shelved 3, and
added link previews as the top priority. Built since: link previews (2026-10-03,
CLAUDE.md deviation 38), the plain-language pass Steven asked for (deviation 39),
and the printable district sheet, formerly item 4 (deviation 40). Left: 2, then 1.
Item 3 is shelved.

**Austin's feedback, 2026-10-03.** Metros (item 2) is unblocked: the area's own
rate. Economic context (item 1) waits on outside advice. The three problems he
hit with what was live were fixed the same day (CLAUDE.md deviation 41).

**Austin's feedback, 2026-10-05.** The district view, previews and printing work
for him, and the site stays AHIL-branded only (no YIMBY Illinois link). Built
the next day (CLAUDE.md deviation 42): "municipality" for "town" in what a reader
sees, up to four municipalities on the printed sheet in a 2 x 2 grid, and number
labels only, without "District office" or "Top-ranked town".

---

## 1. Economic context for towns that permit little

*From the Slack feedback plan (formerly `docs/PHASES.md`, Phase 5), 2026-09.
Narrowed by Austin, 2026-10-02.*

A town that has permitted almost nothing is either one where nobody wants to
build or one that is blocking it. Context helps tell those apart.

**Measures (Austin):** the *change* in median household income
(`B19013_001E`) and the *change* in median home value (`B25077_001E`), from ACS
5-year at place level, each with its `_M` margin of error. Unemployment and
vacancy are dropped. He asked whether each should be compared with the change
for Illinois as a whole; that is cheap (the state row comes from the same query)
and is the natural reading, so plan on it.

**The period cannot be 2010–2025.** An ACS 5-year estimate covers five years, and
the latest published is 2020–2024 (2021–2025 is due around December 2026). The
honest pair is **2006–2010 against 2020–2024**: it starts at the permit window's
start and does not overlap, which matters because the Census advises against
comparing overlapping 5-year periods. Swap to 2021–2025 when it is out.

**Dollars must be adjusted.** A change in a dollar figure across fourteen years is
mostly inflation unless both ends are in the same dollars. Use the Census-advised
CPI-U-RS factor and say so beside the figure.

**How.** A new `scripts/fetch_acs.py`. Null any estimate whose margin makes it
meaningless; small villages are noisy, and an unknown value is `null`, never a
substituted number. Show it in the detail panel and as optional table columns,
**not** in the map colours. Say so in `about.html`.

**On hold for outside advice (2026-10-03).** Steffany: "design here is key", and
someone with an economics background, or who has thought about low-demand places,
should weigh in before it is built. Austin agreed: ask Emily at MPC, and probably
the WNN folks too. Questions worth putting to them:

- Do a town's change in median income and median home value actually separate
  "nobody wants to build here" from "the town is blocking it"? What would they
  use instead, or as well (population or job change, vacancy, sales prices,
  rents, distance to jobs)?
- Is 2006–2010 against 2020–2024 a fair window, given the 2008 crash sits inside
  the first period?
- How should a small village's wide margin of error be shown: blanked, flagged,
  or grouped?
- Should the comparison be to Illinois, to the town's own metro (item 2), or to
  similar towns?

**Then decide.** SPEC.md §10 excludes "housing cost or rent data from ACS", and
median home value is exactly that. Austin asked for it, so if it survives the
advice above, it is a deliberate deviation to record in CLAUDE.md, not an
oversight, and Steffany signs it off.

**Opportunity Zones: deferred.** They need the CDFI Fund's designated-tract XLSX,
TIGER tract geometry and a tract-to-place overlap, and the designations are frozen
on 2010-vintage tracts.

## 2. Metros view

*Steffany, 2026-10-01. Shaped by Austin, 2026-10-02.*

The same measure for each metro and micro area, so a reader can compare the
Chicago suburbs with Peoria or the Metro East rather than town by town.

**Austin's answers:**

- **Its own page**, with a tab called **"Metros"** beside "By municipality" and
  "By legislative district".
- **Core-based statistical areas** (CBSAs): metropolitan *and* micropolitan, on
  OMB's current delineation (July 2023).
- **Every CBSA that includes an Illinois county, counting only its Illinois
  counties.** So the Chicago metro here is its Illinois counties only, and the
  same for St. Louis (the Metro East) and the Quad Cities. Take the county lists
  from OMB's delineation file, not from memory.
- **An average permit rate for each area**, and each municipality evaluated
  against its own area's rate, **in this tab's summary only**. The main map keeps
  Illinois as its reference.
- **The area rate is the area's own rate** (Austin, 2026-10-03): all its permits
  ÷ all its 2010 housing, as `il_pct_growth` is for the state, "because we're
  trying to show where growth is happening within a metro area." Not the mean of
  its towns' rates.

**The data is already fetched and committed (2026-10-03)**, so this can be built
without a Census API key or any download:

- `data/raw/cbsa/list1_2023.xlsx`: OMB Bulletin 23-01 (July 2023), every CBSA and
  its counties (`scripts/fetch_cbsa.py`). 62 of Illinois's 102 counties are in
  one of 33 CBSAs with an Illinois county: 12 metropolitan, 21 micropolitan. The
  other 40 counties are in none, so a rural county has no area to compare to.
- `data/raw/census/h1_2010_county_17.json`, `h1_2020_county_17.json`,
  `p1_2020_county_17.json`: 2010 and 2020 housing counts and 2020 population for
  every Illinois county (`scripts/fetch_census.py`). The county counts sum
  exactly to the state's: 5,296,715 in 2010, 5,426,429 in 2020.
- `data/raw/bps/county/co<YYYY>a.txt`, 2000–2025, and `data/raw/bps/doc/
  cntyasc.pdf` (their record layout): Census's county-level permit files, every
  U.S. county (`scripts/fetch_bps.py`). Units are fields 7, 10, 13 and 16
  (1-unit, 2-unit, 3–4, 5+ units, reported and imputed). Their Illinois rows
  sum exactly to the published state row in every year 2010–2025; in 2002 they
  are 100 units over it (61,071 against 60,971), outside the metric window.
  They are a cross-check on county sums built from the place files, or the
  source of the county totals outright, since they include what counties file
  for unincorporated land.

Notes for whoever starts it:

- **The area rate can include unincorporated permits.** About 8% of Illinois
  permits 2010–2025 are filed by county offices for unincorporated land and are
  left off the municipal map (BLOCKERS.md #1). A county is the first level where
  they have a home. The denominator is then the counties' whole 2010 housing
  count, not the sum of their towns, or the two will not match.
- The published state rows already reconcile with the place files (check A), so a
  county sum can be checked the same way.
- A municipality that straddles two counties in different CBSAs (rare) needs a
  rule; by largest land share is the obvious one.
- Nothing is left to decide before starting. Steffany plans to build it in its
  own session.

## 3. Downloadable shapefiles — shelved

*Steffany, 2026-10-01. Shelved by Austin, 2026-10-02.*

Austin: "very low priority." What he had asked for under "shapefiles" was map
boundaries that look like the real ones, which the unsimplified district outlines
already did (CLAUDE.md deviation 32), not a downloadable dataset. Kept here only
so the idea is not re-proposed from scratch: if it comes back, ship the
unsimplified TIGER geometry with the figures, as GeoJSON plus GeoPackage (no
10-character field limit) and a CSV, with the build date and sources.
