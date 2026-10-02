# Roadmap

Ideas that have been asked for and not built. `BLOCKERS.md` is what is unresolved
about the site as it stands; this file is what the site does not do yet. It sits
at the repo root, outside `docs/`, so it is not published with the site.

Each item says where it came from and what has to be decided before it starts.

---

## 1. Economic context for towns that permit little

*From the Slack feedback plan (formerly `docs/PHASES.md`, Phase 5), 2026-09.*

A town that has permitted almost nothing is either one where nobody wants to
build or one that is blocking it. Context helps tell those apart.

**Measures**, from ACS 5-year at place level (same Census API and key as the
rest of the build; variables were verified to exist):

- Employment and income: `B23025_003E`, `B23025_005E` (unemployment rate),
  `B19013_001E` (median household income).
- Market signal: `B25004_001E` (vacancy), `B25077_001E` (median home value).

**How.** A new `scripts/fetch_acs.py`. Carry each `_M` margin-of-error variable
beside its estimate and null any estimate whose margin makes it meaningless: ACS
5-year is noisy for small villages, and an unknown value is `null`, never a
substituted number. Show it as a context block in the detail panel and as
optional table columns. **Not** in the map colours: that would be a new headline
metric, and Steffany and Austin should decide that explicitly. Say so in
`about.html`.

**Decide first.** SPEC.md §10 excludes "housing cost or rent data from ACS", and
median home value is exactly that. Unemployment, income and vacancy do not run
into it.

**Opportunity Zones: deferred.** They need the CDFI Fund's designated-tract XLSX
(reachable, no key), TIGER tract geometry and a tract-to-place areal overlap, and
the designations are frozen on 2010-vintage tracts, a weaker fit for "is demand
working here now" than current ACS.

## 2. County and metro view

*Steffany, 2026-10-01.*

The same measure for each county, and for each metro area (Census CBSA), so a
reader can compare the Chicago suburbs with Peoria or the Metro East rather than
town by town.

Notes for whoever starts it:

- **County totals can include unincorporated permits.** About 8% of Illinois
  permits 2010–2025 are filed by county offices for unincorporated land and are
  left off the municipal map (BLOCKERS.md #1). A county is the first level where
  they have a home. The denominator then has to be the whole county's 2010
  housing count, not the sum of its towns, or the two will not match.
- The published state rows already reconcile with the place files (check A), so a
  county sum can be checked the same way.
- **Decide first:** a third page, or a toggle on the main map? The legislator view
  became its own page for the reasons in CLAUDE.md (*The legislator view is its
  own page*); the same argument probably applies. Which metro definition (CBSA
  2023 delineation) and whether Chicago's metro includes its Indiana and Wisconsin
  counties (the data here is Illinois only).

## 3. Downloadable shapefiles

*Steffany, 2026-10-01.*

Let a reader download the map's data as GIS files, so an analyst or a reporter
can open it in QGIS or ArcGIS rather than scraping the page.

- What to offer: municipalities with their figures (pct_growth, units by type,
  coverage, AHPAA status and share), and the Senate and House districts with their
  estimates. Plus a CSV of the same table for people without GIS.
- Format: GeoJSON is already built and is enough for most tools; a zipped
  Shapefile is what many government users expect. Shapefile truncates field names
  to 10 characters, so it needs a documented field list. GeoPackage avoids that.
- Ship the **unsimplified** TIGER geometry, not the simplified display geometry,
  and say so. Every download carries the build date and a sources file.
- **Decide first:** which formats, and whether the files are built by `build.py`
  (offline, so via `pyogrio`/`fiona` or a pure-Python writer) or only on release.

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
   printed URL of the live district page** (`districts.html#senate-28`) (Austin).
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

**Decide first:**

- **The weighting.** For example `score = land_share × population_inside`.
  That is close to "the people of this district who live in this town" and favours
  big towns. A version that adds the two parts with weights (`a × land_share +
  b × population_share`) lets a small town that sits wholly inside compete.
  Try both on a few districts (one in Chicago, one suburban, one downstate) and
  pick the one whose answers look right to Austin.
- **Population inside the district.** Land share × town population is the cheap
  version and is what the district estimate already assumes. Census blocks would
  be exact but are a new data source.
- **Members with no usable office address.** Four have none, and two list a
  Springfield capitol room. Either their sheets say "no district office listed",
  or the addresses are corrected by hand from ilga.gov.

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
