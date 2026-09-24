# Slack feedback from Austin & Steffany — implementation plan

## Status

**Phase 1 is done, committed, and passing every check** — commit `7b5ebdf` on
`claude/funny-planck-7n8c8j`, working tree clean.

- 13/13 data checks pass (10 original + new checks B and C).
- 10/10 site checks pass (8 original + new checks 8 and 9).
- Verified on both geometry paths: the committed mapshaper artefacts, and a
  from-scratch offline rebuild with `data/processed/` deleted.

**Phase 1 is pushed.** It was pushed by hand from a local clone, because the
cloud session's own `git push` returns 403 — the Claude GitHub App is not
authorized to write to `AbundantHousingIllinois/il-permit-map`. That is a
property of the cloud container's credentials, not of the repo or the commit,
and it does not apply to a local Claude Code session, which uses your own git
credentials.

**Phase 2 is done** (local session, 2026-09-23). The state silhouette is drawn
beneath the places in a checked neutral, the state edge above the county lines,
the legend has a third swatch ("Unincorporated — no municipality, not in this
data"), and the hand-typed map bounds are replaced by `meta.state_bbox`. Gated by
new data check D and site check 10; CLAUDE.md deviations 25–26. 14/14 data checks
and 11/11 site checks pass, on both the mapshaper artefacts and an offline rebuild
with `data/processed/` deleted.

**Phase 3 is done** (local session, 2026-09-23), with a changed formula. The data
showed the housing count fell 2010→2020 in 780 of 1,360 places, so the planned
ratio (permits ÷ net change) was negative or undefined for much of the state.
Steffany chose the signed difference `net change − permits` in units, in the detail
panel and table only, not on the map. 373 municipalities qualify; Illinois is
−53,533. Gated by data check E and site check 11; CLAUDE.md deviations 27–30. After a Cicero/Berwyn double-check, a top-of-page caveat says permits are a floor and the census count is the better measure of what exists (BLOCKERS.md #6).
15/15 data checks and 12/12 site checks pass.

**Phase 4 is done** (local session, 2026-09-23) as a separate page rather than a
filter: `docs/districts.html`, one map with a Senate / House toggle, a district
panel with the member's contact details, every overlapping municipality with its
share, and an area-weighted district estimate (Steffany's choice). The plan's
Arlington Heights case holds only in the House (Senate 27 alone; House 54 and 53).
Open States is snapshotted into `data/raw/legislators/`, with hand corrections in
`data/manual/legislator_overrides.csv`. Data check F and site check 12; CLAUDE.md
deviation 31.

**Phase 5 below is not started.** Median home value collides with SPEC.md §10
("Housing cost or rent data from ACS"); decide before starting.

### Picking this up on your own machine

```sh
npm install -g @anthropic-ai/claude-code      # or: brew install --cask claude-code
cd /path/to/il-permit-map
git checkout main && git pull
claude
```

`CLAUDE.md` is committed, so a local session reads the conventions, the decision
log and every deviation without being told. Keep this plan file alongside it —
`docs/PHASES.md` or similar — so the next session can read it directly.

Two things a local session needs that the cloud one had:

- `export CENSUS_API_KEY=...` before any `fetch_*.py`. Phases 3 and 5 need it;
  Phases 2 and 4 do not.
- `npx mapshaper` is fetched on demand by `fetch_geo.py`, so Node must be
  present for Phase 2's state-outline simplification. The `topojson` fallback in
  `scripts/simplify_geo.py` covers it offline if not.

Suggested order and opening prompts, one phase per session so each stays
reviewable:

| Phase | Opening prompt | Needs |
|---|---|---|
| 2 — map shape | "Read the plan in docs/PHASES.md and do Phase 2, the state silhouette and unincorporated fill. Keep zero basemap tiles." | Node |
| 4 — legislators | "Do Phase 4, the legislative district filter. Re-fetch the Open States CSV rather than trusting the figures in the plan." | nothing |
| 3 — permits vs built | "Do Phase 3, the 2010→2020 comparison." | `CENSUS_API_KEY` |
| 5 — ACS context | "Do Phase 5, the ACS employment and market context." | `CENSUS_API_KEY` |

Phase 4 is the one Austin can use in a meeting the day it lands, so it is worth
doing before 3 and 5 if his calendar is the constraint. Phase 2 is first here
only because it is the smallest and re-establishes the workflow.

After each phase: `uv run scripts/build.py && uv run scripts/check_data.py &&
uv run scripts/check_site.py`. Every check passes on `main` after each phase
(27 after Phase 3), so any failure is from that phase's change.

---

## Context

Austin Busch and Steffany Bahamon reviewed the permits map and left seven asks
across two Slack messages. They fall into three groups:

1. **A data question that turns out to have a real finding behind it.** Austin
   saw multifamily in Cicero and Glen Ellyn that the "zero multifamily" filter
   claims does not exist, and Steffany asked for a double-check. I ran it.
   Glen Ellyn is a labelling problem, Cicero is a genuine coverage hole the site
   currently renders as zeros.
2. **Presentation fixes** that need no new data: the pre-2010 part of the chart,
   the Illinois colour break not following the structure-type filter, and the
   "zero multifamily" wording.
3. **Three new datasets**: decennial housing units to compare permits against
   what got built, state legislative districts so Austin can filter by
   legislator, and an economic-context measure so low-demand places are not
   read as simply obstructive.

### Do we need an additional API?

**No new API, and no new key.** Everything lands on sources the build already
uses or on keyless static files. Verified live on 2026-09-18:

| Need | Source | New key? |
|---|---|---|
| 2020 housing units | `api.census.gov/data/2020/dec/dhc` `H1_001N` | No — existing `CENSUS_API_KEY` |
| Employment / income / vacancy / home value | `api.census.gov/data/2023/acs/acs5` | No — same key |
| State Senate / House district shapes | `www2.census.gov/geo/tiger/GENZ2025/shp/cb_2025_17_sldu_500k.zip`, `…_sldl_500k.zip` | No key (same host as the place file) |
| Illinois state outline | `…/cb_2025_us_state_500k.zip` | No key |
| Legislator names & contact | `data.openstates.org/people/current/il.csv` | **No key** — bulk static CSV, no account |

One caveat worth stating up front: `CENSUS_API_KEY` is **not set in this
environment**, so the Census calls return `missing_key`. Whoever runs the fetch
step needs it in the environment, as today.

### On building the legislator CSV myself

Yes — but not from memory. `data.openstates.org/people/current/il.csv` returns
177 rows (59 Senate, 118 House, districts contiguous 1–59 and 1–118), with name,
party, district, email, district phone and links back to ilga.gov. Spot-checked
against Austin's own example: **Laura Murphy, Senate 28, Democratic** is row-
present with a district office number. I will fetch that file and snapshot it
into `data/manual/legislators.csv` as a committed, human-editable file with
provenance — so the build stays offline, a wrong row can be corrected by hand,
and nothing is generated from a model's recollection. That is the rule
`data/manual/README.md` already sets for AHPAA, and it matters more here: a
municipality attributed to the wrong senator in a legislator meeting is exactly
the kind of error that rule exists to prevent.

`ilga.gov` itself is not reachable through this environment's proxy (TLS
failure), which is a second reason to snapshot rather than scrape at build time.

---

## The data double-check (do this first, it changes what we say publicly)

**Glen Ellyn — not a bug.** 12 of 12 months reported in every metric year.
382 units 2010–2025: 378 single-family, **4 units in 3–4 unit buildings (2023)**,
0 units in 5+ unit buildings. `zero_mf` keys on `mf5p_total == 0`
(`scripts/build.py`, surfaced as the `zero_mf` property). Austin's guess is
right: it means *no 5+ unit*. The number is correct; the label is misleading.

**Cicero — a real hole.** 8 units total 2010–2025, all single-family or duplex.
But `months_reported` reads **0 of 12 in 2010, 2011, 2012, 2013, 2014 and 2023,
and 1 of 12 in 2015 and 2016**. Half the metric window is not actually covered,
and the chart draws those years as a flat zero band. The shard already carries
`months_reported`; nothing on the site reads it.

**How widespread.** Across the 932 `reporting` places, counting metric years
(2010–2025) with zero months reported:

- **5 places** report 0 months in **all 16** metric years yet are labelled
  `reporting` (Sidell, McLeansboro, Grandview, Elkville, Andalusia — all five
  currently count toward the `zero_mf` headline).
- **249 of the 697 `zero_mf` places** have 4 or more metric years at 0 months.
- Only 396 of 932 have full month coverage across the window.

This is the same class of error CLAUDE.md already calls out one level up ("a
year with no BPS record for a place is null, never 0") — it just was not applied
to a year where the record exists but the office reported nothing.

---

## Phase 1 — coverage integrity and labelling (no new data)

### 1.1 Surface months reported

- `scripts/build.py`: derive per place, over 2010–YMAX, `months_full_years`,
  `months_zero_years`, and `months_coverage` (reported months ÷ expected
  months). Add a `months_flag` of `full` / `partial` / `none`. Write all of it
  into the shard; promote `months_coverage` and `months_flag` onto
  `docs/data/places.geojson` properties (short keys, consistent with `u_sf`,
  `p_sf`).
- Do **not** repurpose `coverage` — `check_data.py` and CLAUDE.md deviation 12
  gate on its counts. `months_flag` is a second, orthogonal axis.
- Exclude `months_flag == "none"` places from the `n_zero_mf` headline in
  `meta.json` and report the count both ways, the same way the join rate is
  reported both ways today.

### 1.2 Chart and detail panel honour it

- `stackedAreaChart()` (`docs/app.js:402`) already shades unreported years via
  the `reported` array and `runs`. Extend `reported[i]` to be false when the
  year has 0 months reported, so those years get the existing grey hole instead
  of a zero band. Years with partial months keep their bar but gain a marker.
- Detail panel: a sentence naming the years — "Cicero's permit office reported
  no months to Census in 2010–2014 and 2023. Those years are blank here, not
  zero."
- Legend gains the wording; `docs/about.html` gains the rule.

### 1.3 "Zero multifamily" → say what it means

- `docs/index.html`: rename the toggle to **"No 5+ unit buildings permitted"**
  with a one-line hint that 3–4 unit buildings are counted separately.
- Add a second toggle **"Nothing above a duplex"** keyed on a new
  `zero_mf3p` property (`mf34_total + mf5p_total == 0`), computed in
  `build.py`.
- Detail panel always prints the four-way split, so Glen Ellyn's 4 units in
  3–4 unit buildings are visible rather than inferred.
- `passesFilters()` (`docs/app.js:95`) gains the second test; `writeHash`/
  `readHash` (`:648`, `:661`) gain the new key.

### 1.4 Colour break follows the structure-type filter

`midpoint()` (`docs/app.js:109`) returns `META.il_pct_growth` unconditionally —
the all-types 5.612% — even when the map is filtered to 5+ unit. That is
Austin's third point and it is a straightforward bug.

- `scripts/build.py`: compute `il_pct_growth_by_type` and
  `us_pct_growth_by_type` from the **published state files** already in
  `data/raw/bps/st<YYYY>a.txt` over the same denominator `il_h1_2010`, matching
  the existing decision that `il_pct_growth` uses the state row rather than the
  place-file sum. Write both into `meta.json`.
- `midpoint()` selects by `state.type`.
- `sequentialStops()` (`:124`) has the same problem in total-units mode:
  `SEQ_STOPS` is a fixed 0–25,000 ladder, so filtering to 5+ unit collapses
  almost every place into the lightest bin. Give it per-type stop ladders,
  derived in `build.py` from the actual per-type distribution and written into
  `meta.json` so the page has no hard-coded breaks.
- `renderLegend()` (`:259`) and the legend title text state which Illinois rate
  is the midpoint.

### 1.5 Pre-2010 on the chart

Steffany suggested starting the chart at 2010; Austin's later message asks to
keep 2000–2010 but mark it, "to explain the baseline before 2008." Going with
Austin's — it is the later message and it keeps the crash context.

In `stackedAreaChart()`: render bands for years < 2010 at reduced fill opacity
with a dashed top edge, add a vertical rule at 2010, and label the two spans
("Context" / "Metric window"). Add the note to the chart legend and to
`docs/about.html`.

---

## Phase 2 — map shape, no basemap tiles

Austin's "wonky-shaped… different shapefile with a better raster." Keeping the
zero-third-party-request decision (CLAUDE.md decision 8 / deviation 8); the
shape reads wrong because the only polygons on screen are incorporated places,
so unincorporated Illinois is a hole and the state has no silhouette.

- `scripts/fetch_geo.py`: also download `cb_2025_us_state_500k.zip` (verified
  reachable), filter to Illinois, run it through the same mapshaper ladder →
  `data/processed/state_simplified.geojson`. `scripts/simplify_geo.py` gains the
  matching offline path so `build.py` still runs with no network (SPEC.md §8).
- `scripts/build.py`: emit `docs/data/state.geojson` and put the state bbox in
  `meta.json`.
- `buildMap()` (`docs/app.js:163`): add a `state` source; fill the state
  silhouette in a neutral tone **beneath** the place fills so unincorporated
  land reads as territory rather than absence, draw a crisp state outline above
  the county lines, and replace the hand-typed `bounds:
  [[-91.6,36.9],[-87.0,42.6]]` with the computed bbox.
- Legend gains a third swatch: "Unincorporated — no municipality, not in this
  data," distinct from the existing hatch.

This is a refinement of decision 8, not a reversal — record it that way in
CLAUDE.md.

---

## Phase 3 — permits vs. what got built

Steffany: "add up all the permits in the decade and compare it to the 2020-2010
difference." Right shape, with caveats that have to ship alongside the number.

- `scripts/fetch_census.py`: add three queries to `QUERIES` — `2020/dec/dhc`
  `H1_001N` for `place:*` in state 17, for the state, and for the US. Same
  `L.census_api()` helper, same key, same provenance trail into
  `data/SOURCES.md`. Variable existence verified.
- `scripts/build.py`: per place, `permits_2010_2019` (sum of `series` 2010–2019
  — the decennial reference dates are April 1 2010 and April 1 2020, so that is
  the matching decade) against `h1_2020 - h1_2010`, plus a ratio. **Null it
  whenever any of the ten years is not fully month-covered** — otherwise this
  metric inherits exactly the Cicero problem.
- Ship as a fourth entry in the Metric control, not the default, and state
  plainly on the page and in `about.html` why the two do not have to match:
  demolitions are netted out of the housing stock but not out of permits, not
  every permit is built, annexation moves boundaries, and the ratio can exceed 1
  or go negative. It is a diagnostic, not a scorecard.

---

## Phase 4 — legislative district filter

Austin: filter, don't display. Places appear in multiple lists.

- **New `scripts/fetch_districts.py`** — downloads `cb_2025_17_sldu_500k.zip`
  and `cb_2025_17_sldl_500k.zip` via `L.download()`, simplifies through the same
  mapshaper ladder `fetch_geo.py` uses, and fetches
  `data.openstates.org/people/current/il.csv` into
  `data/manual/legislators.csv` (committed snapshot, provenance appended to
  `data/SOURCES.md`). `data/manual/README.md` gains a section explaining the
  columns and that a row may be corrected by hand.
- **`scripts/build.py`**: intersect each place against each district using
  **unsimplified** TIGER geometry for accuracy, with `shapely.STRtree` for the
  index (`shapely>=2.0` is already a dependency). 1,461 × 177 with an index is
  cheap. Record the overlap share and apply a small threshold so boundary-noise
  slivers do not create phantom memberships; keep the share so a borderline case
  is inspectable.
- Emit `docs/data/districts.json`: per district `{chamber, number, name, party,
  email, phone, url, geoids[]}`; per place, `sldu[]` and `sldl[]` on the shard
  and a compact form on `places.geojson` for client-side filtering.
- **`docs/index.html`**: a Legislator control — a `<select>` grouped by chamber
  with a type-ahead, since 177 entries is too many for the existing segmented
  buttons.
- **`docs/app.js`**: `state.district`; `passesFilters()` gains the test;
  `writeHash`/`readHash` gain `dist=`; the table meta line reads "24
  municipalities overlapping Senate District 28 — Laura Murphy (D)"; the detail
  panel lists a place's districts and legislators.
- Contact info is carried in the data so Austin's "at least provide contact
  info" is satisfied now; the action-item generator he flagged as "another
  undertaking" stays out of scope.

---

## Phase 5 — economic context for low-demand places

Both requested measures, from ACS 5-year at place level (same API, same key;
variables verified to exist):

- Employment / income: `B23025_003E`, `B23025_005E` (unemployment rate),
  `B19013_001E` (median household income).
- Market signal: `B25004_001E` (vacancy), `B25077_001E` (median home value).

**New `scripts/fetch_acs.py`.** Carry the `_M` margin-of-error variables
alongside every estimate and null any estimate whose MOE makes it meaningless —
ACS 5-year at place level is noisy for small villages, and this build's standing
rule is that an unknown value is `null`, never a substituted number.

Surface it as a context block in the detail panel and as optional table columns.
**Not** wired into the colour scale in v1: that would be a new headline metric
definition, and it should be a decision Steffany and Austin make explicitly
rather than one that arrives inside a rendering change. Note that in
`about.html`.

**Opportunity Zones: deferred, with a written reason.** It needs the CDFI Fund's
designated-tract XLSX (reachable, keyless), TIGER tract geometry, and a
tract-to-place areal overlap, and the designations are frozen on 2010-vintage
tracts — a weaker fit for "is demand working here in 2026" than current ACS.
Logged in `BLOCKERS.md` rather than silently dropped.

---

## Documentation to update

- `BLOCKERS.md`: new item — months-reported coverage inside `reporting` places,
  with the Cicero table and the 5 / 249 counts. New item — Opportunity Zones
  deferred.
- `CLAUDE.md`: new decision entries and deviations 18+ (per-type colour break,
  months-reported treatment, pre-2010 chart treatment, state silhouette as a
  refinement of decision 8, districts, permits-vs-built, ACS context).
- `docs/about.html`: every one of the above in reader language, with all figures
  read from `meta.json` so check B keeps them honest.
- `README.md`: the new fetch commands.

---

## Verification

```bash
uv sync
export CENSUS_API_KEY=...            # required; not set in this environment
uv run scripts/fetch_census.py       # + 2020 DHC H1
uv run scripts/fetch_geo.py          # + state outline
uv run scripts/fetch_districts.py    # new: SLDU/SLDL + legislators.csv
uv run scripts/fetch_acs.py          # new: ACS 5-year context
uv run scripts/build.py              # must still succeed with the network off
uv run scripts/check_data.py
uv run scripts/check_site.py
```

New checks in `scripts/check_data.py`:

- Every place resolves to ≥1 Senate and ≥1 House district; every district 1–59
  and 1–118 has ≥1 place or a recorded reason; every district has a legislator
  row (fail loudly, never blank).
- Named case: **Arlington Heights appears in more than one district list** —
  Austin's own test.
- Named case: **Senate 28 / Laura Murphy includes Park Ridge, Des Plaines and
  Schaumburg** — Austin's other test.
- `months_coverage` is present and in [0,1] for every reporting place; no place
  with `months_flag == "none"` counts toward `n_zero_mf`.
- `il_pct_growth_by_type` reconciles against the published state rows within the
  existing 0.05% metric-window tolerance.

New checks in `scripts/check_site.py`: selecting a legislator filters the map
and table; the chart renders a visible pre-2010 treatment; the legend midpoint
label changes when the structure-type filter changes; console stays empty.

Offline guarantee re-tested the existing way: delete `data/processed/`, rerun
`uv run scripts/build.py` with no network, confirm the fallback simplifier
handles the new state and district layers too.

Manual spot checks: Glen Ellyn (4 units in 3–4 unit buildings visible, no longer
labelled as if it had none of anything), Cicero (2010–2014 and 2023 render as
holes, not zeros), and the 5 all-zero-months places dropped from the zero-5+
headline.
