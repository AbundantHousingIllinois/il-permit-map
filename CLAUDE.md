# Working notes

Conventions and decisions for this repository. `SPEC.md` is the requirement;
this file is how it was met and why. `BLOCKERS.md` is what is still unresolved.

## Conventions

- Python is managed with `uv`. Never `pip`, never bare `python`.
- `scripts/bps_layout.py` is the single source of truth for the BPS record
  layout, the year range, the paths, the place universe and the name
  normalisation. Every other script imports it. If a column index needs to
  change, it changes there and nowhere else.
- Only `scripts/fetch_*.py` touch the network. `build.py` must run offline.
- Fetches are idempotent and record provenance through `L.download` /
  `L.census_api`, which append to `data/SOURCES.md`.
- A value that is not known is `None` in Python and `null` in JSON, all the way
  through to the page. Nothing substitutes a zero for a missing measurement.

## Decisions

**YMAX = 2025.** census.gov/construction/bps/annual.html, retrieved 2026-09-16:
"Annual data for 2025 was released on May 14, 2026." `mw2026a.txt` does not
exist.

**Two BPS record layouts, one of them undocumented.** The annual place files are
41 fields from 2007 and 38 fields for 2000–2006. The current record-layout PDF
(`Documentation/placeasc.pdf`, Attachment B) describes only the 41-field layout.
The 38-field layout was taken from the two-row header Census ships inside those
files, and both layouts are verified two ways by `check_data.py` check A:

1. *Column labels.* In both eras the structure-type label in header row 1 and the
   word `Units` in header row 2 sit at exactly the column the units figure
   occupies, so the header itself confirms the indices.
2. *Totals.* Summing Illinois place records reproduces the independently
   published `Illinois` row of `st<YYYY>a.txt`.

Check 1 is the decisive one. A totals comparison cannot tell 1-unit *Buildings*
from 1-unit *Units*, because for single-family construction they are nearly
equal — `scripts/verify_layout_check.py` demonstrates exactly that blind spot,
which is why the label check exists.

**Units come from the "Reported and Imputed Data" block (fields 18–29), not the
"Reported Only" block (30–41).** These are Census's published figures and include
Census's own imputation for non-responding permit offices; they are what the
published state and national totals are built from. Using the reported-only block
would under-count and would not reconcile.

**The crosswalk is an identifier join, not a name join.** No Illinois BPS id
changes its FIPS place code anywhere in 2007–2025, so `GEOID = "17" + FIPS place`
resolves 933 of 948 places, and the legacy era inherits through the stable
6-digit BPS id. Name matching is the documented fallback and picks up 15
legacy-only municipalities that left the permit universe around 2004; it requires
an exact match after normalisation and refuses any match that would make two BPS
records report the same place in the same year. Join rate on the municipal
universe: **99.999%** of units 2010–2025.

**`il_pct_growth` uses the published state row, not the place-file sum.** They
differ by 21 units over 2010–2025 (BLOCKERS.md #2); the headline reference rate
should be the Census figure.

**Simplification normally runs in `fetch_geo.py`, not `build.py`,** because `npx
mapshaper` needs the network. mapshaper's `-simplify` is topology-aware, so
shared borders stay shared and no slivers open between neighbours. `-clean` is
deliberately not used: it deleted one whole place (1730835), which would break
the 1:1 correspondence with TIGER that check 1 requires. Retention was raised
from the spec's suggested 5% to 12% because 5% produced only 0.51 MB against a
3 MB budget; the committed `places.geojson` is 1.09 MB.

**`scripts/simplify_geo.py` is the offline fallback.** SPEC.md §8 requires
`build.py` to run with no network, and a checkout that has `data/raw/` but not
`data/processed/` has no simplified geometry. In that case `build.py` rebuilds it
with `topojson`, which is the "Python equivalent" SPEC.md §2 allows and is
likewise topology-preserving. It steps through a ladder of thresholds and refuses
any that would drop a feature. The committed artefact is mapshaper's; the
fallback produces a slightly larger file (1.21 MB of geometry at
`toposimplify 0.002`) and is exercised by deleting `data/processed/` and
rebuilding.

**A year with no BPS record for a place is null in `series`, never 0.** 108 of the
reporting places do not appear in all 26 annual files, and 61 of those had almost
no presence inside the metric window. Writing 0 for those years is the same
mistake as colouring a `no_permit_office` place as zero, one level down, and it
inflated the zero-multifamily headline. Now: `series` carries nulls for
unreported years and the chart leaves a visibly shaded hole rather than a flat
zero band; `coverage` is `reporting` only if the place has at least one record
inside 2010-YMAX, so a place whose office last reported in 2003 gets null metrics
and an explanation; and the detail panel says "since {first_metric_year}" rather
than "since 2010" when they differ. This moved `reporting` from 948 to 932 and
`zero_mf` from 713 to 697.

**A 0-month year is a published estimate, not a count, and not a blank.** Every BPS
place record carries `months_reported`. A year where an office filed 0 of 12 months
still has a published figure, because the "Reported and Imputed" block this build
reads carries Census's own imputation for a non-responder — Illinois 0-month
place-years hold 14,709 units in the metric window, so blanking them would discard
real data. They are shown, ticked on the chart, and named in prose in the detail
panel. The five places that reported no month in *any* metric year get a null
`zero_mf` rather than `true`, which moved the zero-5+ headline from 697 to 692.
Raised by Austin Busch asking about Cicero; see BLOCKERS.md #5.

**"Zero multifamily" is two claims, so it is two switches.** The old highlight keyed
on `mf5p_total == 0` and was labelled "Zero multifamily", which reads as "nothing
but houses". Glen Ellyn has permitted 4 units in 3–4 unit buildings and 0 in 5+, and
was in it. The switch is now **"No 5+ unit buildings"** (692 municipalities), with a
second, stricter **"Nothing above a duplex"** on `mf34 + mf5p == 0` (635). Every
detail panel prints all four buckets, and a place in the first switch with 3–4 unit
permits says so in the flag itself.

**The colour break follows the structure-type filter.** `midpoint()` returned
`il_pct_growth` — the all-types 5.612% — whatever the filter said, so filtering to
5+ unit compared every municipality against a rate four times its own reference and
the map went uniformly orange. `meta.json` now carries `il_pct_growth_by_type` and
`us_pct_growth_by_type`, taken from the published state rows for the same reason
`il_pct_growth` is (BLOCKERS.md #2). The sequential ladder had the same problem from
a different direction: one hard-coded `SEQ_STOPS` of 0–25,000 for every type put
almost everything in the lightest bin. `meta.units_stops` now carries a ladder per
type, fitted geometrically between the median and the 99th percentile of the places
that have any of that type, rounded to readable numbers.

**The chart keeps 2000–2009 but marks it as context.** Steffany suggested starting
the chart at 2010; Austin asked to keep the earlier years and mark them, to show the
pre-2008 baseline. Austin's is the later message and the better one, so: pre-2010
bands render at 0.4 opacity with a dashed top outline, a dashed rule sits at 2010
labelled "context" / "counted in the metric", and the tooltip says so on any year
before 2010. Only 2010–YMAX feeds a figure, which the chart caption now states.

**`check_site.py` serves the pinned MapLibre bundle from a local cache.** The page's
one external dependency is the pinned CDN build. On a machine behind a
TLS-inspecting proxy the headless browser cannot validate that request even when
everything else on the machine can, and the whole run died on a 45-second timeout
that said nothing. The pinned URLs are now fetched through Python — which does read
the machine's CA configuration — cached under `data/raw/vendor/`, and served to the
browser from the cache. The page is unmodified and still references the CDN; what
was removed is the *checks'* dependence on the test machine reaching it. A URL that
404s still fails the run, and which path was taken is printed.

**No webfont.** AHIL's Poppins is requested from the reader's own system and a
system sans-serif is the fallback. A Google Fonts `<link>` would be a second
runtime external request that SPEC.md never authorised, and a reader on a
committee-room connection that cannot reach fonts.googleapis.com would take a
failed request for it. The only external request the page makes is the pinned
MapLibre CDN.

**No basemap tiles.** SPEC.md §6.1 allows a plain background with county outlines
when no keyless basemap is usable. That is the choice here: it keeps the page
free of any runtime third-party request, which matters both for §1.4 and for
loading on a phone signal in a committee room.

**The state silhouette refines "no basemap tiles"; it does not reverse it.** Austin
said the map looked "wonky-shaped". The cause was not the place geometry: the only
polygons on screen were incorporated places, so unincorporated Illinois was a hole
and the state had no outline. `fetch_geo.py` now also takes
`cb_2025_us_state_500k` (national-only, like counties), filters to Illinois and
simplifies it at 10%. The page fills it beneath the places in `--map-land` and
draws its edge above the county lines. It is static geometry shipped with the site,
so the page still makes no tile request and no new runtime request of any kind.
`meta.state_bbox` is computed from it and replaces the hand-typed map bounds. The
fill colour was checked, not assumed: OKLab ΔE×100 from `#c9c6bb` is 10.0 to the
diverging midpoint, 14.0 to the lightest sequential step and 8.0 to the hatch base;
in dark mode `#20201e` is 5.0 from the hatch base, whose stripes carry the rest of
the difference. The legend names it "Unincorporated — no municipality, not in this
data," so land that is not measured cannot read as a measurement.

**Permits vs. built is a difference in units, in the panel and table only.**
Steffany asked to compare a decade of permits against the 2010→2020 change in the
housing count. The plan proposed a ratio, permits ÷ net change. Checked against the
data, the count *fell* in 780 of the 1,360 places with both counts, and in 148 of
the 373 that qualify, so that ratio is negative or undefined for much of the state.
Steffany chose the signed difference `(h1_2020 − h1_2010) − permits_2010_2019`, and
chose to keep it out of the map colours: only 373 of 932 reporting places qualify,
and an unexplained large negative (Aurora, −5,876) would read as a verdict. It is
null unless the office reported 12 of 12 months in every year 2010–2019 and both
counts exist; the shard carries the reason. `H1_001N` comes from 2020 DHC; the 2020
PL file returns identical figures at every level, checked 2026-09-23. Illinois:
+129,714 in the count against 183,247 permitted, −53,533. The permit decade is
calendar 2010–2019 because both counts are as of April 1 (`L.BUILT_YEARS`).

**Permits are a floor; the census count is the measure of what exists.** Asked to
double-check Cicero and Berwyn, the build proved correct and the Census record
proved incomplete: Berwyn's record never shows the 52-unit reVerb Century Station
(finished around 2010), and the permit survey never counts conversions
(BLOCKERS.md #6). Steffany asked for the caveat at the top of the map, not only in
about.html, so `#top-caveat` sits in the header. The detail panel always shows the
two census counts when both exist, even where the permit subtraction is withheld,
and flags a gap beyond 5% of the 2010 stock in either direction
(`L.BUILT_FLAG_PCT`). The headline stays permit-based on purpose: it measures what a
town *allowed* as new construction, which is the question the map exists for, and
the census count cannot answer that annually or after 2020.

**The legislator view is its own page, not a filter on the map.** Austin asked to
"filter, don't display" by legislator. A 177-entry control on a page that already
carries metric, type, population, two highlights and a caveat would crowd it, and
would silently change what every legend and total means. Steffany chose a separate
`districts.html`: one map with a Senate / House toggle, municipalities coloured as
on the main map, district outlines over them, and a district panel with the
member, the towns and an estimate. The main map gains only a header link and one
line in each detail panel. District numbers are drawn as **icons, not text**:
MapLibre text labels need a glyph server, which would be a second runtime request,
but an icon needs nothing, so each number is painted to a canvas in the page's own
font and placed at the district's pole of inaccessibility (`shapely polylabel`,
computed in the build). MapLibre's collision detection hides numbers that would
overlap, largest district first; the selected one is orange and always wins.

Membership is an areal overlap on the **unsimplified** TIGER files (`scripts/
districts.py`), listed when at least 1% of a place's land is inside
(`L.DISTRICT_SHARE_MIN`), and the share travels with it. The plan's named case
"Arlington Heights appears in more than one district list" is true only for the
House (54 and 53, 70/30); in the Senate it is 99.9% in District 27. Check F asserts
what is true. The district total is **area-weighted** — each reporting place's
units × its share inside — which Steffany chose over a plain sum (Chicago is in 19
Senate districts) and over a sum of towns mostly inside. It is printed rounded
with an "estimate" tag and its assumption stated beside it. Beside it, at
Steffany's request, sits the change in the census housing count 2010→2020,
weighted the same way (every place with both counts contributes, permit office or
not), and each town's headline shows its own 2010 and 2020 counts. Both are
labelled a change in homes standing, not homes built. The headline's comparison
sentence said a town "grew its housing stock" by its permit share; with the real
count now beside it, that became "has permitted".

The Open States file is a download, so it lives in `data/raw/legislators/`, not
`data/manual/` (whose README says nothing there is script-written). Corrections go
in `data/manual/legislator_overrides.csv`. `SOURCES.md` keeps a URL's first
retrieval date only, and "current legislators" is only as current as the latest
fetch, so `fetch_districts.py` writes `retrieved.txt` beside the snapshot and the
page prints it. The palette, the diverging ladder, the number formats, the hatch
and `esc()` moved from `app.js` to `docs/shared.js` so both maps colour a town
identically from one definition.

**Colour.** AHIL orange `#E87722` and blue `#004B87` as the diverging pair, with
a neutral grey midpoint pinned to `il_pct_growth`. The pair was checked against a
colour-vision-deficiency simulation rather than assumed, as §6.6 requires: OKLab
ΔE×100 is 29.2 under protanopia and 37.9 under tritanopia between endpoints, and
18.9 under protanopia between the wing extremes. The four chart series were run
through the palette validator and pass every check, with separate steps selected
for the dark surface rather than flipped automatically.

**`window.map` is assigned as soon as the map is constructed.** An element with
`id="map"` already puts a DOM node on `window.map`, so the assignment has to
overwrite it before anything reads it.

**Map layers that use `fill-pattern` are added in the `load` handler,** after the
hatch image exists. Declaring them in the initial style makes MapLibre log a
missing-image error, and site check 1 requires an empty console.

**Shard I/O is threaded** in both `build.py` and `check_data.py`. The working
copy sits under a cloud file provider where a single small-file open costs about
280 ms; 1,461 sequential reads take roughly seven minutes and about fifteen
seconds threaded. Every shard is still opened and parsed individually.

## Things deliberately not done

Everything in SPEC.md §10, plus: no municipal-succession mapping for the three
municipalities whose FIPS place codes no longer exist in TIGER (BLOCKERS.md #3),
and no AHPAA data of any kind.

---

## Every deviation from SPEC.md, with its reason

1. **§7 check 3 gates on the municipal universe, not all BPS units.** Read
   literally the check is unsatisfiable: 8.26% of Illinois BPS units 2010–2025
   are county-unincorporated, whole-county or township records that cannot have
   a TIGER *place* geometry, capping the all-units rate at 91.74%. §10 puts an
   unincorporated layer out of scope. Both rates print; the gap is not hidden.
   (BLOCKERS.md #1.)
2. **§7 check 6 is implemented as a conservation identity**
   `shards + unmatched == raw IL total` (drift 0.0000%), rather than
   `shards == raw IL total`, which the same 8.26% makes impossible. Strictly
   harder to satisfy than the loose reading, and it catches dropped years and
   double counting. The shards-only share is printed too.
3. **One check added beyond §7: check A**, gating and labelled as an addition.
   It verifies the BPS column positions against the shipped column headers and
   against the published state totals. Added because the 2000–2006 layout is not
   covered by the current record-layout PDF. Its per-year tolerance is 0.5% —
   the same figure §7.6 uses — and 0.05% across the metric window.
   `scripts/verify_layout_check.py` shows the tolerance still fails on any
   one-column slip.
4. **Geometry retention 12%, not the suggested "around 5%."** §3.4 says to start
   at 5% and adjust; 5% produced 0.51 MB against a 3 MB budget and visibly
   faceted small villages. Built `places.geojson` is 1.09 MB.
5. **`scripts/simplify_geo.py` added** as the offline Python simplifier so
   `build.py` satisfies §8 ("no network access required") from a checkout that
   has `data/raw/` but no `data/processed/`. §2 explicitly allows a Python
   equivalent to mapshaper.
6. **County names come from the 2010 SF1 API** (`for=county:*`), and places with
   no BPS record get their county from a point-in-polygon test against the TIGER
   county layer. §3 lists no place-to-county lookup. Both are existing §3.2/§3.4
   sources, and every shard records which method produced its label.
   (BLOCKERS.md #4.)
7. **The county boundary file is the national `cb_2025_us_county_500k`**, filtered
   to Illinois. §3.4 names only the place file; counties are not published
   per-state at this vintage. Used for §6.1's county outlines.
8. **No basemap tiles.** §6.1 permits this explicitly when no keyless option is
   usable; recorded here because it is a choice, made to keep the page free of
   any runtime third-party request.
9. **No webfont.** AHIL's Poppins is used if the reader has it, with a system
   fallback. A Google Fonts request would be a second external dependency SPEC.md
   never authorised, and an unreachable one would trip site check 1.
10. **`unmatched.csv` has one row per BPS record id**, aggregated across years
    with its unit totals, not one row per record-year. §3.1 asks for "every
    unmatched BPS record ... with its unit totals"; per-id is the readable form.
11. **The table lists only `reporting` places.** §6.3 does not say so, but §5
    requires coverage gaps to be excluded from rankings, and a table that can be
    sorted by percent growth is a ranking.
12. **`coverage` is judged on the metric window.** A place whose permit office
    last reported before 2010 is `no_permit_office` rather than `reporting` with
    a 0% growth rate, and years with no BPS record are null in `series`, never 0
    (§1.3). This moved `reporting` from 948 to 932 and `zero_mf` from 713 to 697.
13. **`sort` is encoded in the URL hash** alongside the four things §6.5 lists,
    so a linked view restores the table ordering the sender was looking at.
14. **Playwright is a main dependency, not optional**, so that
    `uv run scripts/check_site.py` works after a plain `uv sync`; the script
    installs the Chromium build on first run and retries a transient driver
    failure.
15. **`check_site.py` waits for the detail panel to finish rendering** rather than
    sleeping a fixed interval. The panel fetches its shard over HTTP; a panel
    that never renders still fails, so this bounds the wait rather than relaxing
    the check.
16. **Shard reads and writes are threaded** in `build.py` and `check_data.py`.
    Every shard is still opened and parsed individually; only the concurrency
    changed, because this working copy sits on a cloud file provider where one
    small-file open costs about 280 ms.

17. **`months_reported` gates the zero-5+ flag and is surfaced on the page.** §7
    has no check for it and §6 does not mention it, but §1.3's rule — a missing
    measurement is never a zero — applies as much to a year an office skipped as to
    a year with no record. Five places lose their `zero_mf` flag to this; the rest
    keep their figures and gain a caveat. `check_data.py` check B gates it.
    (BLOCKERS.md #5.)
18. **The "Zero multifamily" highlight became two switches with different names.**
    §6.2 names one highlight. The old label described the 5+ bucket as though it
    covered all multifamily, which is wrong for any municipality with a 3–4 unit
    permit. Splitting it is a clarity fix, not a new feature: both switches read the
    same already-built per-type totals.
19. **The diverging midpoint and the sequential ladder are per structure type.**
    §6.2 says the midpoint is "the Illinois average" and gives one figure. Read with
    §6.2's own type filter, one figure cannot be right for five different views, so
    `meta.json` carries five. `check_data.py` check C and site check 8 gate it.
20. **The chart marks 2000–2009 as context rather than dropping it.** §6.4 asks for
    the full 2000–YMAX series; this keeps it and adds the boundary the metric
    definition implies.
21. **Site checks 8 and 9 added**, both labelled as additions, for the two items
    above. Site check 3's midpoint is now read from its own element rather than
    scraped as the first number in the sentence — which stopped working the moment
    the sentence could say "3–4 unit".
22. **`check_site.py` may cache the pinned MapLibre bundle under
    `data/raw/vendor/`** and serve it to the headless browser. §8 requires the
    *build* to run offline and says nothing about the checks; this moves the checks
    the same way. The page itself is untouched.
23. **Fixed while in the file:** the legend title in Total units mode said
    "Total units permitted 2000–YMAX" while the map coloured by the
    METRIC_START–YMAX total. The label was wrong, not the data.
24. *Recorded below, under "Added after the spec: `docs/about.html`".*
25. **A state silhouette, a third legend swatch, and computed bounds.** §6.1
    describes a plain background with county outlines. The state outline is a
    fourth TIGER file (`cb_2025_us_state_500k`, same host and vintage as §3.4's),
    rendered beneath the places so unincorporated land reads as territory.
    Decision 8 still holds: no tiles, no runtime third-party request. Data check
    **D** and site check **10** gate it, both labelled as additions. The offline
    fallback in `build.py` rebuilds it with `topojson` like the other layers.
26. **Fixed while in the file:** `fetch_geo.py` recorded `-clean` in the
    provenance line it appends to `data/SOURCES.md`, though `-clean` is
    deliberately not used. Earlier rows in that append-only log still say it; new
    rows do not.
27. **Permits vs. what the 2020 Census counted.** §1.5 and §3 name no 2020 housing
    count, and §3.2 forbids substituting one for the 2010 denominator. It is used
    here for one separate, requested comparison and nothing else: `pct_growth` is
    untouched, which data check 4 still gates. It adds a table column and a
    detail-panel section, not a map metric. Data check **E** and site check **11**
    gate it, both labelled as additions. The global source line stays as §6.6
    words it, because every figure on the map is still 2010-based; the 2020 table
    is in `meta.sources` and the about page.
28. **Blank values sort last in the table in both directions.** A null used to
    sort as −∞, which put every blank at the top of an ascending sort — a missing
    value presented as the lowest rank. With the new column blank for 559 reporting
    places, that stopped being a corner case.
29. **Check 7 is stricter than §7.7, and `build.py` sweeps the shard directory.**
    The cloud file provider left 1,163 byte-identical conflict copies
    (`1700113 3.json`) beside the shards during a rebuild. They never reached git,
    but `git add docs/data` would have shipped them. The cause was `build.py`
    deleting the shard directory and recreating it; the provider kept producing
    copies after the build had finished. Shards are now overwritten in place,
    anything that is not a current shard is deleted after the write, and check 7
    fails if one is there.
30. **A caveat at the top of the map page**, which §6 does not describe, at
    Steffany's request: permits are a floor, and the census count is the better
    measure of what exists. It pushed the map below a phone-height viewport, so
    site check 4 now scrolls the map into view before its click, as a reader
    would. The click must still land on the target place.
31. **The legislator view** (`docs/districts.html`) and its two new sources:
    TIGER `cb_2025_17_sldu_500k` / `_sldl_500k` and the Open States current
    legislators file. §3 names neither and §6 describes no such page; both were
    requested (Austin's legislator filter, Steffany's choice of a separate page).
    It adds an estimated, area-weighted district total — a new figure, labelled
    as an estimate everywhere it appears. Its source line adds "Open States" to
    §6.6's fixed wording, because the member details are from there. Data check
    **F** (including F.4: the estimates sum to 99–100% of municipal permits in each
    chamber; 99.8% Senate, 99.5% House) and site check **12** gate it, both
    labelled as additions.

---

## Added after the spec: `docs/about.html`

SPEC.md §1.6 says "no features beyond §6," and §6 describes only the map page.
Steffany asked for an explainer page after the build was finished, so this is a
deliberate, requested departure rather than scope creep, recorded here as
**deviation 24**.

The page restates the metric definition, the three things the numbers are *not*
(authorised rather than built, no demolitions netted out, not a cost measure),
the coverage rule, and every item from `BLOCKERS.md` written for a reader rather
than for a maintainer — with the empty AHPAA list first and marked "needs you",
including the exact column spec for `data/manual/ahpaa.csv`.

Every figure on it is fetched from the same `docs/data/meta.json` the map reads,
so it cannot drift out of date with the build; there is no second copy of any
number in the HTML. `check_site.py` gained a disclosed extra check **B** that
loads the page, requires zero console errors, and asserts the rendered Illinois
and U.S. figures match `meta.json` — so a build that changes the numbers without
updating the page fails the check rather than shipping a stale caveat. It now also
asserts the two multifamily counts and the two months-reported counts, which are the
figures a reader is most likely to quote.

The page gained a section 4, *What "no 5+ unit" means, and what it does not*, using
Glen Ellyn as the worked example, and a third callout in section 3 for the
months-reported rule.
