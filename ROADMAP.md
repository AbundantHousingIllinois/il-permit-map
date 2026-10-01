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

## 4. A printable one-page sheet per municipality

*Steffany, 2026-10-01, inspired by Electrify Chicago's "Print Flyer"
(https://electrifychicago.net/building/willis-tower-rivion-llc/) and a post by
@chiwho.bike (https://bsky.app/profile/chiwho.bike/post/3mvopkkqlf22s).*

A "Print" button in each municipality's panel that produces one clean US Letter
page to hand to a council member or leave behind in a legislator meeting.
Electrify Chicago's building page does this: the grade, the key numbers, the
charts and the year-by-year table, with the site's navigation gone.

A sheet would hold:

- Name, county, 2020 population, the AHIL logo and the build date.
- The headline: percent of the 2010 stock permitted since 2010, against Illinois
  and the U.S., in words as well as numbers.
- Units by structure type, and the multifamily flag.
- The per-year chart (drawn for print: no hover, years labelled).
- The census counts 2010 → 2020, AHPAA status and affordable share, and the
  town's Senate and House members.
- The caveat that permits are a floor, and a short sources line with a link
  (or QR code) back to the live page for that town.

Notes:

- The district view already prints cleanly for a meeting (README), so the
  pattern is a `@media print` stylesheet plus a print layout, not a PDF library.
  The browser's "Save as PDF" then gives the PDF.
- Every figure must come from the same shard and `meta.json` the panel reads, so
  the sheet can never disagree with the map.
- A QR code needs a small library or an inline generator; it must not add a
  runtime request beyond the pinned MapLibre bundle (CLAUDE.md, *No webfont*).
- Use the `ahil-brand` guidelines for the layout.
- **Decide first:** one page per town only, or also one per legislative district
  (the district panel already has most of what that sheet would need)?
