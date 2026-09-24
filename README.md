# Illinois Housing Permits Dashboard

A static site showing how much housing each Illinois municipality has permitted
since 2010. Built for [Abundant Housing Illinois](https://abundanthousingillinois.org)
to use in legislative advocacy, and written so that **every number on the site is
reproducible from raw public sources by running one build command.**

---

## What the site shows

For every Illinois municipality with a permit office reporting to the U.S. Census
Bureau, the site shows:

> **`pct_growth`** = housing units authorized by permit, 2010 through 2025,
> divided by the number of housing units counted in that municipality on
> April 1, 2010, expressed as a percent.

The map's default view is that percentage, on a diverging orange-to-blue scale
whose **midpoint is the Illinois statewide figure (5.6%)**, so the colour break
means "keeping up with the state." The U.S. figure over the same period is 15.0%.

Alongside it: absolute unit counts (back to 2000 in the "Total units" view), a
breakdown by structure type (single-family, duplex, 3–4 unit, 5+ unit), a
per-year stacked chart on each municipality, and a **zero multifamily** highlight
for municipalities that have permitted no building of five or more units since
2010.

The site has two pages: the map (`docs/index.html`) and
**[Method, caveats and open items](docs/about.html)** (`docs/about.html`), which
explains what the numbers mean and lists what still needs a person — the empty
AHPAA list first. Its figures are read from the build's own `meta.json`, so it
cannot go stale.

## Caveats that matter, especially in testimony

**Permits are authorizations, not buildings.** The Building Permits Survey counts
housing units *authorized by permit*. It does not count units completed, and it
does not subtract demolitions. So this measures **how much was added**, not net
change in the housing stock. A municipality that permitted 500 units and
demolished 400 shows 500 here.

**The denominator and the numerator do not overlap.** The denominator is the
April 1, 2010 complete count of housing units (2010 Decennial Census, table H1,
variable `H001001`) — an enumeration, not a survey estimate. Permits are summed
from calendar year 2010 forward. A unit counted in the 2010 denominator was
authorized before the count, so it is not also in the numerator.

**A coverage gap is never a zero.** Not every Illinois place has a permit office
reporting to the Census. 932 of the state's 1,461 places have at least one permit
record inside 2010–2025; 529 do not. Those 529 are drawn with a **grey hatch**, never with a low-value colour, are labelled
in the legend as *"No permit office reporting to Census — not a zero"*, are left
out of the table and of every statewide aggregate, and show an explanation
instead of metrics when opened. A municipality with no permit office is not a
municipality that built nothing — it is one the Census has no administrative
record for.

The land between municipalities is filled with a flat, unpatterned state
silhouette, labelled *"Unincorporated — no municipality, not in this data."*
It is territory, not a measurement: county-filed permits for it are out of scope
(BLOCKERS.md #1). The silhouette also sets the map's opening view.

The same rule applies year by year. A place whose permit office first reported in
2013 has **no value**, not a zero, for 2010–2012: its chart shows a shaded gap
over those years and its detail panel says which year its record starts from. The
live counts above are printed by the build and are in `docs/data/meta.json`; they
move when the data is refreshed.

**The AHPAA list is hand-maintained.** The Affordable Housing Planning and
Appeals Act non-exempt list is a periodic determination by the Illinois Housing
Development Authority with no API and no machine-readable feed. It lives in
`data/manual/ahpaa.csv`, which ships with headers and **zero rows**. While it is
empty, every municipality's AHPAA status is null and the site disables the AHPAA
filter with a tooltip saying why. No status is ever generated. When the file is
populated it carries an `as_of_date` per row, which the site displays.

**County-unincorporated permits are not on the map.** About 8% of Illinois
permits 2010–2025 are filed by county permit offices for unincorporated
territory. That territory is not a municipality and has no place boundary, and a
county-balance layer is out of scope for v1. Those units are excluded from every
place metric and are itemised in `data/processed/unmatched.csv`. See
`BLOCKERS.md` #1.

**Known discrepancies are written down, not smoothed over.** `BLOCKERS.md`
records every unresolved item, including a 0.0071% difference between the
place-level and state-level BPS files whose cause could not be established from
the published documentation.

---

## How to rebuild

```sh
uv sync
uv run scripts/fetch_bps.py        # Building Permits Survey flat files
uv run scripts/fetch_census.py     # 2010 SF1 H1, 2020 PL P1  (needs CENSUS_API_KEY)
uv run scripts/fetch_geo.py        # TIGER places, counties, state outline + mapshaper
uv run scripts/build.py            # regenerates everything under docs/data/
uv run scripts/check_data.py       # SPEC.md §7 data checks
uv run scripts/check_site.py       # SPEC.md §7 browser checks (Playwright)
```

The three `fetch_*` scripts are the only ones that touch the network, and they
are idempotent — a file already in `data/raw/` is left alone. Once `data/raw/` is
populated (it is committed, so a fresh clone already has it):

```sh
uv sync && uv run scripts/build.py && uv run scripts/check_data.py
```

is enough, and needs no network access.

`CENSUS_API_KEY` is read from the environment. It is never hardcoded, never
written to disk, and the URLs recorded in `data/SOURCES.md` have no key on them.

### Checks

`scripts/check_data.py` is the definition of done for the data. It exits 0 only
if all ten SPEC.md §7 conditions hold, and prints a readable report either way.
It re-reads the TIGER archive and the raw BPS files itself rather than trusting
anything the build wrote. It also runs three extra checks of its own, each
labelled as an addition (plus **D**, below):

- **A** verifies the BPS column positions against the shipped column headers and
  against the independently published state totals.
- **B** verifies that the months each permit office actually reported are carried
  through, and that no municipality whose office reported nothing is claimed as
  having permitted zero multifamily (BLOCKERS.md #5).
- **C** verifies that the Illinois reference rate and the legend breaks exist per
  structure type and reconcile with the published state rows.
- **D** verifies the state silhouette is a single Illinois polygon, that
  `meta.state_bbox` matches it, and that every place sits inside it.

`scripts/check_site.py` serves `docs/` over HTTP and drives it with headless
Chromium, installing the browser on first run if needed. Site checks 8, 9 and 10
are additions too: 8 asserts the map's colour break follows the structure-type filter,
9 asserts the chart separates the pre-2010 context from the metric window and marks
the years a permit office did not report, and 10 asserts the state silhouette is
drawn beneath the places, the state edge above the county lines, and the opening
view contains the whole state.

The page's one external dependency is the pinned MapLibre CDN build. `check_site.py`
fetches those pinned URLs itself, caches them under `data/raw/vendor/`, and serves
them to the headless browser, so the browser checks do not depend on the test
machine being able to reach a CDN — which it cannot, behind a TLS-inspecting proxy.
The page is unmodified and still references the CDN; a URL that stops resolving
still fails the run.

`scripts/simplify_geo.py` is the offline geometry fallback. `fetch_geo.py`
normally simplifies with mapshaper, which needs the network; if `data/processed/`
is absent, `build.py` rebuilds the simplified boundaries with `topojson` instead,
so a checkout with only `data/raw/` still builds offline.

`scripts/verify_layout_check.py` is not part of the build. It shifts each BPS
unit column by one and confirms check A fails on it, so the tolerance in check A
can be shown to be sufficient rather than merely permissive.

---

## Publishing to GitHub Pages

The site is the `docs/` directory on `main`. To turn it on:

**Settings → Pages → Build and deployment → Source: "Deploy from a branch" →
Branch: `main`, folder: `/docs` → Save.**

Nothing needs building on the server. `docs/` contains the finished HTML, CSS, JS
and JSON; MapLibre GL JS 4.7.1 loads from a pinned CDN and the map makes no
runtime API calls and needs no key. There is no webfont request either — the page
uses Poppins if the reader already has it and a system sans-serif otherwise — so
the pinned MapLibre CDN is the only external request the page makes.

---

## Layout

```
SPEC.md                    the build specification
CLAUDE.md                  working notes, conventions, decisions
BLOCKERS.md                everything unresolved, written as encountered
data/
  raw/                     downloaded sources, cached (committed; all < 50 MB)
  processed/               crosswalk.csv, unmatched.csv, tidy permits, geometry
  manual/ahpaa.csv         hand-maintained, headers only
  SOURCES.md               every URL, date retrieved, file produced
scripts/                   fetch_*, crosswalk, build, check_data, check_site
docs/                      GitHub Pages root (index.html + about.html)
```

## Sources

| What | Source |
|---|---|
| Units authorized | U.S. Census Bureau, Building Permits Survey, place-level annual files, Midwest region, 2000–2025 |
| Statewide and national totals | Building Permits Survey, state-level annual files (`Illinois` and `United States` rows) |
| 2010 housing units | 2010 Decennial Census SF1, table H1, variable `H001001` |
| 2020 population | 2020 Decennial Census PL, table P1, variable `P1_001N` |
| Boundaries | TIGER cartographic boundary files, `cb_2025_17_place_500k`, `cb_2025_us_county_500k` |
| AHPAA status | Illinois Housing Development Authority determination list, hand-entered |

Every URL, with the date it was retrieved and the file it produced, is in
[`data/SOURCES.md`](data/SOURCES.md).
