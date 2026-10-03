# Roadmap

Ideas that have been asked for and not built. `BLOCKERS.md` is what is unresolved
about the site as it stands; this file is what the site does not do yet. It sits
at the repo root, outside `docs/`, so it is not published with the site.

Each item says where it came from and what has to be decided before it starts.

**Austin's feedback, 2026-10-02.** He answered items 1, 2 and 4, shelved 3, and
added link previews as the top priority; those were built on 2026-10-03 (CLAUDE.md
deviation 38). Steven asked for a language pass, now item 5. Suggested order:
5, 4, 2, 1. Item 3 is shelved.

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

**Decide first.** SPEC.md §10 excludes "housing cost or rent data from ACS", and
median home value is exactly that. Austin asked for it, so this is a deliberate
deviation to record in CLAUDE.md, not an oversight. Steffany should confirm.

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
- **Decide first:** whether "average permit rate" means the area's own rate
  (all its permits ÷ all its 2010 housing, as `il_pct_growth` is for the state) or
  the mean of its towns' rates. The first is consistent with the rest of the site
  and is not swayed by tiny villages; the second is what "average" literally says.
  Ask Austin.

## 3. Downloadable shapefiles — shelved

*Steffany, 2026-10-01. Shelved by Austin, 2026-10-02.*

Austin: "very low priority." What he had asked for under "shapefiles" was map
boundaries that look like the real ones, which the unsimplified district outlines
already did (CLAUDE.md deviation 32), not a downloadable dataset. Kept here only
so the idea is not re-proposed from scratch: if it comes back, ship the
unsimplified TIGER geometry with the figures, as GeoJSON plus GeoPackage (no
10-character field limit) and a CSV, with the build date and sources.

## 4. A printable one-page sheet per legislative district

*Steffany, 2026-10-01. The idea of a print button comes from Electrify Chicago's
"Print Flyer" (https://electrifychicago.net/building/willis-tower-rivion-llc/)
and a post by @chiwho.bike (https://bsky.app/profile/chiwho.bike/post/3mvopkkqlf22s),
but the sheet is our own, not a copy of theirs.*

A "Print" button on each Senate and House district in the legislator view that
produces one clean US Letter page to leave behind in a meeting with that member.

**What the sheet holds:**

1. **The district's headline stats:** the member, the estimated units permitted
   since 2010, the change in the census housing count 2010→2020, and how many of
   its towns have permitted no 5+ unit building or are AHPAA non-exempt. All of
   these are already in the district panel. **Beside them, a QR code and the
   printed URL of the district's own page** (`…/il-permit-map/senate/28/`,
   which previews properly when shared) (Austin).
2. **The municipality the member's district office is in.** It is the place the
   member knows best and where the meeting usually happens. Open States carries a
   `district_address` for 173 of 177 members (snapshot of 2026-09), and the city
   in it can be matched exactly to a town in the district for 170 of them
   (checked 2026-10-02). The three that do not match:
   - House 69 (Sosnowski): the office is in Machesney Park, which is not in the
     district. Show it and say the office is outside the district.
   - House 55 (Cochran) and House 42 (DeLaRosa): the "district" address is a
     Stratton Office Building room in Springfield, i.e. a capitol office. Treat
     as no district office, or correct by hand in
     `data/manual/legislator_overrides.csv` from ilga.gov.

   An exact name match is enough; no geocoding is needed. A city that matches no
   Census place fails the build loudly rather than being guessed.
3. **The next best town: the "best option" for the district**, after the office
   town (Austin: "the district office town, then the next best town"). Chosen by a
   weighted combination of
   - how much of the town's land is inside the district (the share the build
     already computes, `L.DISTRICT_SHARE_MIN` and up), and
   - how many people live there: the town's 2020 population, or the people of
     the town who live inside the district.

   A town that is mostly inside the district and holds a lot of its people is the
   one the member is most clearly answerable for. It is the highest-scoring town
   other than the office town, so the sheet always shows two different towns
   when the district has two.

   **Chicago is shown as a whole city** (Austin), never split into wards or
   community areas. In a district that includes Chicago, the sheet shows Chicago
   and then the best suburb, if the district has one. The 2026-10-01 note that a
   suburban office might carry a "Chicago" mailing address was a guess; the data
   does not show it.

For each of those towns, a short block: percent of the 2010 stock permitted
against Illinois, units by structure type, the multifamily flag, and AHPAA status
and share.

**Austin's answers, 2026-10-02:**

- **Scoring the next best town:** first, towns with **more than 50% of their land
  in the district and more than 5,000 people**; then everything else by
  **population × land share**. Within the first group, order by population × land
  share too (assumed; he did not say).
- **Population inside the district:** land share × the town's 2020 population, as
  the district estimate already assumes. No block data.
- **Members without a viable office:** Cochran (House 55) and DeLaRosa (House 42),
  whose "district office" is a Springfield capitol room, get **the next two best
  towns** instead.

**Still to decide:** Sosnowski (House 69) lists a real office in Machesney Park,
which is outside his district. Is that "viable"? Either show it and say it is
outside the district, or treat him like the two above. Ask Austin.

**How it would work:**

- A `@media print` stylesheet and a print layout, not a PDF library. The
  browser's "Save as PDF" gives the PDF. The district view already prints
  cleanly (README), so this extends that.
- Every figure comes from `districts.json`, the shards and `meta.json`, which the
  page already reads, so the sheet can never disagree with the map. The office
  town and the featured town are computed in the build and carried in
  `districts.json`, with the score, so the choice can be checked.
- A small district map on the sheet, with the two towns marked. The QR code
  beside the headline must come from an inline generator (or be drawn in the
  build), adding no runtime request beyond the pinned MapLibre bundle.
- The caveat that permits are a floor, the build date and a sources line.
- Use the `ahil-brand` guidelines for the layout.

## 5. A plain-language pass over the site's wording

*Steven, relayed by Austin, 2026-10-02.*

"It has British spellings ('Colour'). And it says Claude things like 'permits are a
floor', which isn't a thing normal people say."

- **American spelling** everywhere a reader sees it: `index.html`,
  `districts.html`, `about.html`, the panel text in `detail.js` and `districts.js`,
  and the text `build.py` writes into shards (`months_note`, `coverage_note`).
  Code comments and these notes can stay as they are.
- **Rewrite the stock phrases** in plain English, starting with the header
  caveat's "Permits are a floor, not a count of homes." Say what it means: permits
  count new buildings a town approved; they miss conversions and basement
  apartments, so the real number of homes added can be higher.
- Site checks read some of this text (the caveat, check B's figures), so run both
  check scripts after the pass.
