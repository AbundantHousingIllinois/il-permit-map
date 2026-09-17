# Illinois Housing Permits Dashboard — Build Spec

A static site showing how much housing each Illinois municipality has permitted
since 2010, hosted on GitHub Pages. Built for Abundant Housing Illinois to use in
legislative advocacy.

The audience is state legislators and their staff. The core claim the site must
support is: *many municipalities telling lawmakers "we aren't the problem" have
added almost no housing, and many have added zero multifamily housing at all.*

Because this will be used in testimony, **every number on the site must be
reproducible from raw public sources by running one build command.** Accuracy
beats features. If you cannot source a number, leave it out.

---

## 1. Hard rules

These are not negotiable. Violating any of them makes the deliverable useless.

1. **Never fabricate, estimate, or interpolate data.** If a source is
   unreachable or a field is missing, record it in `BLOCKERS.md` and leave the
   value null. Do not fill gaps with plausible numbers.
2. **Never invent AHPAA compliance statuses.** See §3.4. This is a hand-curated
   file. If it is empty, the AHPAA filter must be disabled in the UI, not faked.
3. **A missing value is never rendered as zero.** A municipality with no permit
   office reporting to the Census is not a municipality that built nothing. See
   §5.
4. **No runtime API calls from the page.** GitHub Pages is static. All data is
   fetched and computed at build time and committed as static files.
5. **No new data sources beyond §3.** If something seems to require one, write
   it in `BLOCKERS.md` and move on.
6. **No features beyond §6.** Scope creep here is expensive.
7. **Record provenance.** Every URL downloaded goes into `data/SOURCES.md` with
   the date retrieved and the file it produced.
8. **Verify URLs, don't assume them.** Source URLs in this spec are starting
   points that may be stale. Navigate from the documented landing pages and
   confirm what actually exists before hardcoding a path.

---

## 2. Stack

- Python managed with **`uv`** (`pyproject.toml`, `uv run`, `uv sync`). Do not
  use bare `pip` or `python`.
- Build scripts in `scripts/`, plain Python. `pandas` and `requests` are fine.
- Frontend: **MapLibre GL JS** + vanilla JS. No build step, no framework, no npm
  for the site itself. Load MapLibre from a pinned CDN version.
- Geometry simplification: `mapshaper` (npx) or Python equivalent.
- Tests/checks: plain Python scripts with exit codes. Playwright for the browser
  check.
- Site is served from the `docs/` directory on the `main` branch.

---

## 3. Data sources

### 3.1 Numerator — Census Building Permits Survey (BPS)

Administrative counts of housing units authorized, collected from permit
offices. **Not** ACS, not a survey estimate.

- Landing page: `https://www.census.gov/construction/bps/`
- We need **place-level annual** data for the **Midwest region**, for years
  **2000 through the most recent final year available** (2024 or 2025).
- Filter to state FIPS **17** (Illinois).
- Fields needed per place per year: place identifier, place name, county,
  and units authorized in each of: 1-unit, 2-unit, 3-4 unit, 5+ unit structures.
- File format and column layout vary by era. Read the official record layout
  documentation (linked from the BPS sample/documentation page) rather than
  guessing at column positions.

**Known risk — the join.** BPS place records do not always carry a clean 6-digit
FIPS place code, and the identifier scheme has changed over time. Joining BPS
places to TIGER place geometries is the single most likely place for this build
to silently go wrong. Requirements:

- Build an explicit crosswalk step, `scripts/crosswalk.py`, that produces
  `data/processed/crosswalk.csv` mapping BPS place records to TIGER place GEOIDs.
- Match on identifier where available; fall back to normalized name + county.
- Write every unmatched BPS record to `data/processed/unmatched.csv` with its
  unit totals, so the size of the problem is visible.
- The check in §7 enforces a minimum join rate. Do not paper over a low join rate
  by loosening the name matching until it passes — log it and report it.

### 3.2 Denominator — 2010 Decennial Census

- Table **H1**, total housing units. API variable `H001001` in the 2010 SF1
  dataset.
- Expected endpoint:
  `https://api.census.gov/data/2010/dec/sf1?get=NAME,H001001&for=place:*&in=state:17`
- A Census API key is available in the environment as `CENSUS_API_KEY`. Read it
  from the environment; never hardcode it, never commit it.
- If the 2010 SF1 API endpoint has been retired, fall back to the 2010 SF1 place
  file on the Census FTP site and document the change in `data/SOURCES.md`.
  **Do not substitute an ACS estimate or the 2020 count** — the 2010 complete
  count is the whole point of the baseline.

### 3.3 Population (for the size filters) — 2020 Decennial Census

- Table **P1**, total population. Variable `P1_001N` in the 2020 PL dataset.
- Used only for the ≥5,000 and ≥25,000 filters.

### 3.4 Geometry — TIGER cartographic boundaries

- Illinois places: `cb_<year>_17_place_500k` from the Census cartographic
  boundary files.
- Simplify aggressively. Target: the full statewide `places.geojson` under 3 MB.
  Start around 5% retention and adjust. Quantize coordinates to ~5 decimal
  places.

### 3.5 AHPAA status — hand-maintained, NOT automated

The Affordable Housing Planning and Appeals Act non-exempt list is a periodic
IHDA determination. There is no API.

- Create `data/manual/ahpaa.csv` with headers:
  `geoid,municipality,status,source_url,as_of_date,notes`
- **Populate it with zero rows.** Steffany will fill it in from the current IHDA
  determination list.
- Add a `README` note in `data/manual/` explaining the file and where the list
  comes from.
- The build must handle an empty file gracefully: `ahpaa_status` is null for all
  places, and the UI disables the AHPAA filter with a tooltip explaining why.
- **Under no circumstances generate this data from model knowledge.**

---

## 4. Metrics

Let `P_total(place, y1, y2)` = total units authorized across all structure types,
summed over years y1..y2. Let `H1_2010(place)` = 2010 total housing units.

Latest available BPS final year is `YMAX`.

| Field | Definition |
|---|---|
| `pct_growth` | `P_total(place, 2010, YMAX) / H1_2010(place) * 100` |
| `units_total_2010` | `P_total(place, 2010, YMAX)` — absolute count |
| `units_total_2000` | `P_total(place, 2000, YMAX)` — absolute, for the "two decades" view |
| `units_by_type` | Same sums split into `sf`, `du`, `mf34`, `mf5p` |
| `pct_growth_by_type` | Each type's 2010–YMAX sum over `H1_2010` |
| `mf5p_total` | 5+ unit units, 2010–YMAX |
| `zero_mf` | Boolean: `mf5p_total == 0` **and** coverage is `reporting` |
| `series` | Per-year units by structure type, 2000–YMAX — for the detail chart |
| `coverage` | See §5 |

### Reference rates (computed once, stored in `meta.json`)

- `il_pct_growth` = total IL units authorized 2010–YMAX ÷ IL total H1 2010
- `us_pct_growth` = same from BPS national totals ÷ US total H1 2010

These are the "keeping up with state/national trends" baselines. `il_pct_growth`
is the **midpoint of the map color scale.**

---

## 5. Coverage — handling gaps honestly

Every place gets a `coverage` field:

- `reporting` — present in BPS with a resolved crosswalk match.
- `no_permit_office` — the place exists in TIGER but has no BPS permit-office
  record. These are typically small or unincorporated-adjacent places.
- `unmatched` — a BPS record exists but could not be joined to geometry, or vice
  versa in an ambiguous way.

Rendering rules:

- `reporting` → normal choropleth fill.
- `no_permit_office` and `unmatched` → a distinct hatch or gray pattern, **never
  a zero-value color.**
- These places are excluded from rankings, from the `zero_mf` flag, and from all
  aggregate totals.
- The legend must label this state in plain language, e.g. "No permit office
  reporting to Census — not a zero."
- The detail panel for such a place explains this instead of showing metrics.

---

## 6. The site

Mobile-first. Many users will open this on a phone from a link in a text or a
committee room.

### 6.1 Map (default view)

- MapLibre choropleth of all Illinois places.
- **Default metric: `pct_growth`.** Diverging orange-to-blue scale, **midpoint at
  `il_pct_growth`**, so the color break means "keeping up with the state."
- Legend states the midpoint value explicitly, e.g. "Illinois average: 7.3%".
- Basemap: a light neutral raster or vector style that does not require an API
  key. If no keyless option is usable, render on a plain background with county
  outlines and document the choice.

### 6.2 Controls

- **Metric toggle:** Percent growth (default) / Total units. In total-units mode
  the year range extends back to 2000 and the color scale becomes sequential, not
  diverging.
- **Structure type toggle:** All (default) / Single-family / Duplex / 3-4 unit /
  5+ unit.
- **"Zero multifamily" highlight:** a switch that dims everything except places
  where `zero_mf` is true. This is a headline view — make it easy to find.
- **Population filter:** ≥5,000 (**on by default**) / ≥25,000 / All.
  Places below the active threshold are dimmed and excluded from the table.
- **AHPAA filter:** show only non-exempt municipalities. Disabled with an
  explanatory tooltip when `ahpaa.csv` is empty.

### 6.3 Table

Below the map. Sortable, filtered by the same controls as the map.
Columns: Municipality, County, % growth since 2010, Total units, 5+ unit units,
Population, AHPAA status (when available). Clicking a row selects it on the map.

### 6.4 Detail panel

Bottom sheet on mobile, side panel on desktop. Opens on map or table click.

- Municipality name, county, 2020 population.
- Headline: percent growth since 2010, with the absolute unit count beside it.
  Never the percent alone.
- One-sentence comparison, generated from the data:
  *"Since 2010, Illinois grew its housing stock by 7.3% and the U.S. by 11.1%.
  Wilmette grew by 2.1% — 142 units."*
- Stacked area chart, units by structure type per year, 2000–YMAX. Match the
  four-series breakdown in the HUD chart the request came from.
- If `zero_mf`, state it plainly: *"No units in buildings of 5+ have been
  permitted here since 2010."*
- If coverage is not `reporting`, show the explanation from §5 instead of metrics.

### 6.5 URL state

The selected place, metric, structure type, and filters are encoded in the URL
hash so a view can be linked in a document or a Slack message. Loading a URL with
a hash restores that exact view.

### 6.6 Visual style

AHIL palette: orange and blue as the diverging pair (the orange-to-blue scale was
specifically requested and is reasonably colorblind-safe — verify the chosen
endpoints against a deuteranopia simulation). Clean, high-contrast, legible at
phone size. Every chart and the map need a title and a source line reading
"U.S. Census Building Permits Survey; 2010 Decennial Census" plus the build date.

---

## 7. Checks — write these BEFORE the build code

`scripts/check_data.py` exits 0 only if all of the following hold. It prints a
readable report either way. This script is the definition of done for the data.

1. Every Illinois place in the TIGER file appears exactly once in
   `docs/data/places.geojson`.
2. Every feature has a non-null `coverage` field with a valid value.
3. BPS join rate: at least **95%** of Illinois BPS-reported units, 2010–YMAX,
   resolve to a place with geometry. Print the actual rate and the top 20
   unmatched records by unit count.
4. Every place with a non-null `pct_growth` has `H1_2010 > 0`.
5. No place has `pct_growth` below 0. Any place above 200% is printed as a
   flagged outlier for human review (not necessarily an error — some genuinely
   grew that fast).
6. Statewide total units recomputed from the per-place shards matches the total
   computed directly from the raw BPS Illinois rows within 0.5%.
7. One JSON shard exists in `docs/data/places/` for every feature in
   `places.geojson`, and each shard parses and contains a `series` array.
8. `docs/data/places.geojson` is under 3 MB.
9. `docs/data/meta.json` contains `il_pct_growth`, `us_pct_growth`, `ymax`,
   `build_date`, and a `sources` list.
10. `data/manual/ahpaa.csv` exists with the correct headers (zero rows is a pass).

`scripts/check_site.py` runs Playwright headless against the built `docs/` and
exits 0 only if:

1. The page loads with zero console errors.
2. The map canvas renders and at least 1,000 polygon features are present in the
   source data the page loaded.
3. The legend displays a numeric Illinois midpoint value.
4. Clicking a place chosen programmatically from the built data (do not hardcode
   a FIPS code) opens the detail panel with a non-empty name, a percent, and an
   absolute unit count.
5. Toggling to "Total units" changes the rendered legend.
6. Toggling "Zero multifamily" reduces the count of highlighted features.
7. A URL hash with a selected place restores that selection on load.

---

## 8. Repository layout

```
.
├── CLAUDE.md                 # working notes, conventions, decisions
├── SPEC.md                   # this file
├── BLOCKERS.md               # anything unresolved, written as encountered
├── README.md                 # what this is, how to rebuild, caveats
├── pyproject.toml
├── data/
│   ├── raw/                  # downloaded sources, cached, gitignored if >50MB
│   ├── processed/            # crosswalk.csv, unmatched.csv, tidy permits
│   ├── manual/
│   │   ├── ahpaa.csv         # headers only, hand-maintained
│   │   └── README.md
│   └── SOURCES.md            # every URL, date retrieved, resulting file
├── scripts/
│   ├── fetch_bps.py
│   ├── fetch_census.py
│   ├── fetch_geo.py
│   ├── crosswalk.py
│   ├── build.py              # produces everything under docs/data/
│   ├── check_data.py
│   └── check_site.py
└── docs/                     # GitHub Pages root
    ├── index.html
    ├── app.js
    ├── style.css
    └── data/
        ├── places.geojson
        ├── meta.json
        └── places/<geoid>.json
```

`uv run scripts/build.py` must regenerate everything under `docs/data/` from
`data/raw/` and `data/manual/` with no network access required.

---

## 9. README requirements

The README is part of the deliverable, not an afterthought. It must state:

- What the site shows and the exact metric definition.
- That BPS counts units **authorized by permit**, not units completed, and does
  not account for demolitions — so this is "how much was added," not net change.
- That the 2010 denominator is the April 1, 2010 complete count, and that permits
  are summed from 2010 forward so the numerator and denominator do not overlap.
- That coverage gaps are not zeros, and how they are rendered.
- That the AHPAA list is hand-maintained with an as-of date.
- How to rebuild: `uv sync && uv run scripts/build.py && uv run scripts/check_data.py`
- How to enable GitHub Pages (Settings → Pages → main branch, `/docs` folder).

---

## 10. Out of scope for v1

Do not build these. They are recorded so they are not re-litigated:

- Housing cost or rent data from ACS
- Peer-group comparison or auto-selected similar municipalities
- Pinning/comparing multiple municipalities side by side
- County-balance layer for unincorporated territory
- PMTiles (only if simplification cannot get under the size budget — and then
  note it in `BLOCKERS.md` before implementing)
- Any per-year rolling average metric