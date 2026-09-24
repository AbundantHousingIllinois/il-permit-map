/* Illinois Housing Permits Dashboard -- Abundant Housing Illinois
 *
 * No runtime API calls (SPEC.md §1.4): everything is read from static files
 * under docs/data/ that scripts/build.py produced.
 *
 * A missing value is never drawn as zero (SPEC.md §1.3, §5). Places whose
 * coverage is not `reporting` get a hatch pattern and are excluded from the
 * table, from rankings and from the zero-multifamily highlight.
 */
'use strict';

/* ---------------------------------------------------------------- palette */

/* Diverging scale, percent-growth mode. Two hues plus a NEUTRAL GRAY midpoint,
 * with the midpoint pinned to il_pct_growth so the colour break reads as
 * "keeping up with the state" (SPEC.md §6.1).
 *
 * Endpoints are AHIL orange #E87722 and AHIL blue #004B87, verified against a
 * colour-vision-deficiency simulation as SPEC.md §6.6 requires rather than
 * assumed: OKLab dE x100 between the two endpoints is 29.2 (protanopia),
 * 37.9 (tritanopia) and 39.5 (normal vision); between the two wing extremes
 * #7E3505 and #004B87 it is 18.9 (protanopia). Anything above 8 is well
 * separated, so the pair survives deuteranopia and protanopia.
 */
const DIVERGING = {
  low: ['#7E3505', '#E87722', '#F7C79F'],
  mid: '#E9E6E1',
  high: ['#A8C6DC', '#3C7CA8', '#004B87']
};

/* Sequential scale, total-units mode: one hue, light to dark (SPEC.md §6.2). */
const SEQUENTIAL = ['#EEF4F9', '#CFE0EE', '#A3C4DE', '#6F9FC6', '#3C79A8', '#17578A', '#00365F'];
/* Only a fallback now. The real breaks are fitted per structure type in the
 * build and arrive in meta.units_stops: one hard-coded ladder put almost every
 * municipality in the lightest bin as soon as the type filter was on. */
const SEQ_STOPS = [0, 25, 100, 400, 1500, 6000, 25000];

/* Four categorical series for the stacked area chart, assigned in fixed order
 * (single-family, duplex, 3-4, 5+) and never cycled. Both sets were run through
 * the palette validator and pass every check for their own surface: lightness
 * band, chroma floor, CVD separation, the normal-vision floor and contrast.
 */
const SERIES_LIGHT = { sf: '#2E6E9E', du: '#E87722', mf34: '#1F8A70', mf5p: '#9B59B6' };
const SERIES_DARK  = { sf: '#3E80B2', du: '#DC7420', mf34: '#26977C', mf5p: '#A468BE' };

const TYPE_ORDER = ['sf', 'du', 'mf34', 'mf5p'];
const TYPE_LABEL = { sf: 'Single-family', du: 'Duplex (2 units)', mf34: '3–4 units', mf5p: '5+ units' };
const TYPE_LABEL_SHORT = { all: 'All types', sf: 'Single-family', du: 'Duplex', mf34: '3–4 unit', mf5p: '5+ unit' };

function darkMode() {
  return window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches
    && document.documentElement.dataset.theme !== 'light';
}
function seriesColors() { return darkMode() ? SERIES_DARK : SERIES_LIGHT; }
function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

/* ---------------------------------------------------------------- state */

const state = {
  metric: 'pct_growth',
  type: 'all',
  popMin: 5000,
  zeroMf: false,
  zeroMf3p: false,
  ahpaa: false,
  selected: null,
  sort: { key: 'pct_growth', dir: 'asc' }
};

let META = null;
let FEATURES = [];          // properties only, in file order
let BY_GEOID = new Map();
let SHARDS = new Map();     // geoid -> shard JSON, fetched on demand
let map = null;
let activeGeoids = [];      // single source of truth for "highlighted"
let layersReady = false;    // the fill layers are added in the map's load handler

/* ---------------------------------------------------------------- helpers */

const fmtInt = n => (n === null || n === undefined) ? '—' : n.toLocaleString('en-US');
const fmtPct = (n, d) => (n === null || n === undefined)
  ? '—' : n.toLocaleString('en-US', { minimumFractionDigits: d === undefined ? 1 : d, maximumFractionDigits: d === undefined ? 1 : d }) + '%';

/* The value the map is currently colouring by. Returns null -- never 0 -- when
 * the place has no measurement. */
function metricValue(p) {
  if (p.coverage !== 'reporting') return null;
  const key = state.metric === 'pct_growth'
    ? (state.type === 'all' ? 'pct_growth' : 'p_' + state.type)
    : (state.type === 'all' ? 'units_total_2010' : 'u_' + state.type);
  const v = p[key];
  return (v === null || v === undefined) ? null : v;
}

function isMeasured(p) { return metricValue(p) !== null; }

function passesFilters(p) {
  if ((p.pop2020 || 0) < state.popMin) return false;
  if (state.zeroMf && p.zero_mf !== true) return false;
  if (state.zeroMf3p && p.zero_mf3p !== true) return false;
  if (state.ahpaa) {
    const s = (p.ahpaa_status || '').toLowerCase();
    if (!s.includes('non-exempt')) return false;
  }
  return true;
}

function isHighlighted(p) { return isMeasured(p) && passesFilters(p); }

/* ---------------------------------------------------------------- scales */

/* The neutral break on the diverging scale: the Illinois rate the reader is
 * being asked to compare against. With the structure-type filter on, that has
 * to be Illinois's rate FOR THAT TYPE -- pinning it to the all-types 5.6% made
 * every town look far below average the moment you filtered to 5+ unit. */
function midpoint() {
  if (state.type === 'all') return META.il_pct_growth;
  const byType = META.il_pct_growth_by_type || {};
  const v = byType[state.type];
  return (v === null || v === undefined) ? META.il_pct_growth : v;
}

function usMidpoint() {
  if (state.type === 'all') return META.us_pct_growth;
  const byType = META.us_pct_growth_by_type || {};
  const v = byType[state.type];
  return (v === null || v === undefined) ? META.us_pct_growth : v;
}

function divergingStops() {
  const m = midpoint();
  return [
    [0, DIVERGING.low[0]],
    [m * 0.35, DIVERGING.low[1]],
    [m * 0.7, DIVERGING.low[2]],
    [m, DIVERGING.mid],
    [m * 1.6, DIVERGING.high[0]],
    [m * 2.6, DIVERGING.high[1]],
    [m * 4, DIVERGING.high[2]]
  ];
}

function sequentialStops() {
  const fitted = (META.units_stops || {})[state.type];
  const stops = (fitted && fitted.length === SEQUENTIAL.length) ? fitted : SEQ_STOPS;
  return stops.map((v, i) => [v, SEQUENTIAL[i]]);
}

function currentStops() {
  return state.metric === 'pct_growth' ? divergingStops() : sequentialStops();
}

function colorExpression() {
  const key = state.metric === 'pct_growth'
    ? (state.type === 'all' ? 'pct_growth' : 'p_' + state.type)
    : (state.type === 'all' ? 'units_total_2010' : 'u_' + state.type);
  const expr = ['interpolate', ['linear'], ['to-number', ['get', key], 0]];
  for (const [v, c] of currentStops()) expr.push(v, c);
  return expr;
}

/* ---------------------------------------------------------------- hatch */

/* A 45-degree hatch, drawn in a canvas so no image file and no external
 * request is needed. This is the fill for coverage gaps -- deliberately not a
 * colour from either scale, so it can never be mistaken for a low value. */
function hatchImage() {
  const s = 8, c = document.createElement('canvas');
  c.width = c.height = s;
  const g = c.getContext('2d');
  g.fillStyle = darkMode() ? '#2b2b29' : '#dfe0dc';
  g.fillRect(0, 0, s, s);
  g.strokeStyle = darkMode() ? '#6a6a64' : '#a6a7a2';
  g.lineWidth = 1.6;
  g.beginPath();
  g.moveTo(-2, s + 2); g.lineTo(s + 2, -2);
  g.moveTo(s / 2 - 2, s + s / 2 + 2); g.lineTo(s + s / 2 + 2, s / 2 - 2);
  g.stroke();
  return g.getImageData(0, 0, s, s);
}

/* ---------------------------------------------------------------- map */

function buildMap(placesGeojson, countiesGeojson, stateGeojson) {
  map = new maplibregl.Map({
    container: 'map',
    /* SPEC.md §6.1: no keyless basemap is used. The map renders on a plain
     * background with county outlines, which keeps the page free of any runtime
     * third-party tile request and of any API key. Documented in the README.
     *
     * The state silhouette is filled beneath the places, so the land between
     * municipalities reads as unincorporated territory rather than as a hole
     * cut out of the map. It uses no image, so it can be declared here.
     *
     * Only the background layer is declared up front. The fill layers are added
     * in the load handler, after the hatch image exists -- a layer that names a
     * `fill-pattern` image the style does not have yet makes MapLibre log an
     * error, and site check 1 requires a console with nothing in it. */
    style: {
      version: 8,
      sources: {
        places: { type: 'geojson', data: placesGeojson, promoteId: 'geoid' },
        counties: { type: 'geojson', data: countiesGeojson },
        state: { type: 'geojson', data: stateGeojson }
      },
      layers: [
        { id: 'bg', type: 'background', paint: { 'background-color': cssVar('--map-bg') } },
        { id: 'state-fill', type: 'fill', source: 'state',
          paint: { 'fill-color': cssVar('--map-land') } }
      ]
    },
    /* Computed from the state outline by the build, not typed in. */
    bounds: META.state_bbox,
    fitBoundsOptions: { padding: 12 },
    attributionControl: false,
    dragRotate: false,
    pitchWithRotate: false
  });

  /* Exposed immediately. An element with id="map" already puts a DOM node on
   * `window.map`, so this has to overwrite it before anything reads it. */
  window.map = map;

  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
  map.addControl(new maplibregl.AttributionControl({
    compact: true,
    customAttribution: 'U.S. Census Building Permits Survey; 2010 Decennial Census; TIGER/Line'
  }), 'bottom-right');

  map.on('load', () => {
    if (!map.hasImage('hatch')) map.addImage('hatch', hatchImage());

    map.addLayer({
      id: 'places-gap', type: 'fill', source: 'places',
      filter: ['!=', ['get', 'coverage'], 'reporting'],
      paint: { 'fill-pattern': 'hatch', 'fill-opacity': 0.9 }
    });
    map.addLayer({
      id: 'places-fill', type: 'fill', source: 'places',
      filter: ['==', ['get', 'coverage'], 'reporting'],
      paint: { 'fill-color': colorExpression(), 'fill-opacity': 0.95 }
    });
    map.addLayer({
      id: 'places-line', type: 'line', source: 'places',
      paint: { 'line-color': cssVar('--rule-strong'), 'line-width': 0.4, 'line-opacity': 0.8 }
    });
    map.addLayer({
      id: 'counties-line', type: 'line', source: 'counties',
      paint: { 'line-color': darkMode() ? '#55554f' : '#b3b4af', 'line-width': 0.7 }
    });
    /* Above the county lines so the state edge is the crispest line on the map,
     * but below the selection ring so a border town's outline is not cut. */
    map.addLayer({
      id: 'state-line', type: 'line', source: 'state',
      paint: { 'line-color': cssVar('--state-line'), 'line-width': 1.2 }
    });
    map.addLayer({
      id: 'places-selected', type: 'line', source: 'places',
      filter: ['==', ['get', 'geoid'], ''],
      paint: { 'line-color': '#E87722', 'line-width': 2.5 }
    });

    for (const layer of ['places-fill', 'places-gap']) {
      map.on('click', layer, e => {
        if (e.features && e.features.length) selectPlace(e.features[0].properties.geoid);
      });
      map.on('mouseenter', layer, () => { map.getCanvas().style.cursor = 'pointer'; });
      map.on('mouseleave', layer, () => { map.getCanvas().style.cursor = ''; });
    }

    layersReady = true;
    applyStyle();
  });
}

/* Rendering and counting share one source of truth: the list of highlighted
 * GEOIDs. The map opacity expression is built from that same list, so
 * highlightedCount() can never drift from what is on screen. */
function applyStyle() {
  if (!map || !layersReady || !map.getLayer('places-fill')) return;
  activeGeoids = FEATURES.filter(isHighlighted).map(p => p.geoid);
  const dimming = activeGeoids.length !== FEATURES.filter(isMeasured).length;
  const inActive = ['in', ['get', 'geoid'], ['literal', activeGeoids]];

  map.setPaintProperty('places-fill', 'fill-color', colorExpression());
  map.setPaintProperty('places-fill', 'fill-opacity',
    dimming ? ['case', inActive, 0.95, 0.1] : 0.95);
  map.setPaintProperty('places-gap', 'fill-opacity', dimming ? 0.35 : 0.9);
  map.setFilter('places-selected', ['==', ['get', 'geoid'], state.selected || '']);
}

/* ---------------------------------------------------------------- legend */

function renderLegend() {
  const stops = currentStops();
  const isPct = state.metric === 'pct_growth';
  const typeLabel = state.type === 'all' ? '' : ' — ' + TYPE_LABEL_SHORT[state.type].toLowerCase();

  document.getElementById('legend-title').textContent = isPct
    ? `Units permitted ${META.metric_start}–${META.ymax} as a share of 2010 housing stock${typeLabel}`
    : `Total units permitted ${META.metric_start}–${META.ymax}${typeLabel}`;

  /* Always numeric and always present: check 3 of the browser checks reads it,
   * and a reader needs the midpoint stated outright (SPEC.md §6.1). */
  const mid = midpoint();
  const midWhat = state.type === 'all'
    ? 'Illinois average'
    : `Illinois average, ${TYPE_LABEL_SHORT[state.type].toLowerCase()}`;
  /* The value gets its own element. The label can now contain a digit ("3-4
   * unit"), so anything reading the midpoint out of the sentence -- site check 3
   * did -- would pick up the wrong number. */
  const midValue = `<b>${midWhat}: <span id="legend-midpoint-value">${fmtPct(mid, 2)}</span></b>`;
  document.getElementById('legend-midpoint').innerHTML = isPct
    ? `Colour break is the state average for whatever is being shown — `
      + `${midValue}. Orange is below it, blue above.`
    : `Sequential scale, light to dark, with breaks fitted to `
      + `${state.type === 'all' ? 'all types' : TYPE_LABEL_SHORT[state.type].toLowerCase()}. `
      + `Reference: ${midValue} growth since ${META.metric_start} `
      + `(the midpoint used in Percent growth mode).`;

  const ramp = document.getElementById('legend-ramp');
  ramp.innerHTML = '';
  for (const [, c] of stops) {
    const d = document.createElement('div');
    d.className = 'step';
    d.style.background = c;
    ramp.appendChild(d);
  }
  const labels = document.getElementById('legend-labels');
  labels.innerHTML = '';
  stops.forEach(([v], i) => {
    const s = document.createElement('span');
    if (i === 0 || i === stops.length - 1 || i === 3) {
      /* Sub-1% midpoints on the sparse types need a second decimal or every
       * label reads "0%". */
      const dp = Math.abs(midpoint()) < 1 ? 2 : (i === 3 ? 1 : 0);
      s.textContent = isPct
        ? (i === stops.length - 1 ? fmtPct(v, dp) + '+' : fmtPct(v, dp))
        : (i === stops.length - 1 ? fmtInt(v) + '+' : fmtInt(v));
    } else {
      s.textContent = '';
    }
    labels.appendChild(s);
  });

  const sw = document.getElementById('legend-gap-swatch');
  sw.style.background = darkMode()
    ? 'repeating-linear-gradient(45deg,#2b2b29 0 3px,#6a6a64 3px 4px)'
    : 'repeating-linear-gradient(45deg,#dfe0dc 0 3px,#a6a7a2 3px 4px)';
  document.getElementById('legend-land-swatch').style.background = cssVar('--map-land');
}

/* ---------------------------------------------------------------- table */

const COLUMNS = [
  { key: 'name', label: 'Municipality', type: 'text' },
  { key: 'county', label: 'County', type: 'text' },
  { key: 'pct_growth', label: '% growth since 2010', type: 'num', fmt: v => fmtPct(v) },
  { key: 'units_total_2010', label: 'Total units', type: 'num', fmt: fmtInt },
  { key: 'mf5p_total', label: '5+ unit units', type: 'num', fmt: fmtInt },
  { key: 'pop2020', label: 'Population', type: 'num', fmt: fmtInt }
];

function columns() {
  const cols = COLUMNS.slice();
  if (META.ahpaa.enabled) cols.push({ key: 'ahpaa_status', label: 'AHPAA status', type: 'text' });
  return cols;
}

function renderTable() {
  const cols = columns();
  const head = document.getElementById('table-head-row');
  head.innerHTML = '';
  for (const c of cols) {
    const th = document.createElement('th');
    th.textContent = c.label;
    th.scope = 'col';
    if (state.sort.key === c.key) th.setAttribute('aria-sort', state.sort.dir === 'asc' ? 'ascending' : 'descending');
    th.onclick = () => {
      if (state.sort.key === c.key) state.sort.dir = state.sort.dir === 'asc' ? 'desc' : 'asc';
      else state.sort = { key: c.key, dir: c.type === 'num' ? 'desc' : 'asc' };
      renderTable();
      writeHash();
    };
    head.appendChild(th);
  }

  /* SPEC.md §5: coverage gaps are excluded from rankings, so the table holds
   * only reporting places, and only those passing the active filters. */
  const rows = FEATURES.filter(isHighlighted);
  const { key, dir } = state.sort;
  const col = cols.find(c => c.key === key) || cols[2];
  rows.sort((a, b) => {
    let x = a[key], y = b[key];
    if (col.type === 'num') {
      x = (x === null || x === undefined) ? -Infinity : x;
      y = (y === null || y === undefined) ? -Infinity : y;
      return dir === 'asc' ? x - y : y - x;
    }
    x = (x || '').toString(); y = (y || '').toString();
    return dir === 'asc' ? x.localeCompare(y) : y.localeCompare(x);
  });

  const body = document.getElementById('table-body');
  body.innerHTML = '';
  for (const p of rows) {
    const tr = document.createElement('tr');
    tr.tabIndex = 0;
    if (p.geoid === state.selected) tr.setAttribute('aria-selected', 'true');
    for (const c of cols) {
      const td = document.createElement('td');
      if (c.type === 'num') {
        td.className = 'num';
        td.textContent = c.fmt(p[c.key]);
      } else if (c.key === 'name') {
        td.textContent = p.name;
        if (p.zero_mf === true) {
          const tag = document.createElement('span');
          tag.className = 'zero-mf-tag';
          tag.textContent = 'no 5+';
          tag.title = 'No units in buildings of 5+ permitted here since 2010';
          td.appendChild(tag);
        }
      } else {
        td.textContent = p[c.key] || '—';
      }
      tr.appendChild(td);
    }
    tr.onclick = () => selectPlace(p.geoid);
    tr.onkeydown = e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); selectPlace(p.geoid); } };
    body.appendChild(tr);
  }

  const total = rows.reduce((s, p) => s + (p.units_total_2010 || 0), 0);
  const nZero = rows.filter(p => p.zero_mf === true).length;
  document.getElementById('table-title').textContent =
    state.zeroMf ? 'Municipalities with zero multifamily since 2010' : 'Municipalities';
  document.getElementById('table-meta').textContent =
    `${fmtInt(rows.length)} municipalities shown · ${fmtInt(total)} units permitted `
    + `${META.metric_start}–${META.ymax} · ${fmtInt(nZero)} of them have permitted no `
    + `building of 5+ units. Places with no permit office reporting to the Census are `
    + `excluded from this table rather than counted as zero. Tap a row to see detail.`;
}

/* ---------------------------------------------------------------- chart */

/* Stacked area, units by structure type per year (SPEC.md §6.4). Four series in
 * fixed order, a legend plus direct hover readout, and a 2px surface-coloured
 * gap between stacked bands so adjacent fills stay separable. */
function stackedAreaChart(series, shard) {
  const W = 320, H = 164, PAD = { t: 16, r: 6, b: 30, l: 34 };
  const colors = seriesColors();
  const surface = cssVar('--surface-raised') || '#fff';
  const ink = cssVar('--ink-muted') || '#888';
  const years = series.map(d => d.year);
  /* A year with no BPS record carries nulls, not zeros. It must stay a hole in
   * the chart: drawing it at zero would claim the Census counted nothing, when
   * the Census counted nothing *of this place*. */
  const reported = series.map(d => d.total !== null && d.total !== undefined);
  const totals = series.map((d, i) => reported[i]
    ? TYPE_ORDER.reduce((s, k) => s + (d[k] || 0), 0) : 0);
  const maxY = Math.max(1, ...totals);

  /* The percent-growth metric starts in 2010, but the chart runs from 2000 so a
   * reader can see the pre-crash baseline. Everything left of 2010 is therefore
   * context, not part of any number on this page, and is drawn faded with a
   * dashed outline so it cannot be read as part of the metric. */
  const mStart = META.metric_start;
  const mIdx = series.findIndex(d => d.year === mStart);
  /* Years where the permit office reported 0 of 12 months. The Census still
   * publishes a figure -- its own estimate for a non-reporting office -- so the
   * band is drawn, but it is marked and the tooltip says what it is. */
  const imputed = new Set((shard && shard.months_imputed_years) || []);

  const x = i => PAD.l + (W - PAD.l - PAD.r) * (series.length < 2 ? 0 : i / (series.length - 1));
  const y = v => PAD.t + (H - PAD.t - PAD.b) * (1 - v / maxY);

  // cumulative baselines, computed only where a year was reported
  const base = series.map(() => 0);
  const bands = [];
  for (const k of TYPE_ORDER) {
    const lower = base.slice();
    series.forEach((d, i) => { if (reported[i]) base[i] += (d[k] || 0); });
    bands.push({ key: k, lower, upper: base.slice() });
  }

  // contiguous runs of reported years -- each drawn as its own area
  const runs = [];
  let run = null;
  reported.forEach((ok, i) => {
    if (ok) { if (!run) { run = [i, i]; } else { run[1] = i; } }
    else if (run) { runs.push(run); run = null; }
  });
  if (run) runs.push(run);

  /* Split every run at 2010 so the two eras can be drawn differently. The two
   * halves share the boundary vertex, so they abut exactly with no seam. */
  const segs = [];
  for (const [a, z] of runs) {
    if (mIdx > a && mIdx < z) { segs.push([a, mIdx]); segs.push([mIdx, z]); }
    else segs.push([a, z]);
  }
  const isContext = ([a]) => mIdx >= 0 && series[a].year < mStart;

  let svg = `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" `
    + `aria-label="Units permitted per year by structure type, ${years[0]} to ${years[years.length - 1]}. `
    + `Years before ${mStart} are shown faded because the percent-growth metric starts in ${mStart}.">`;

  // recessive gridlines + y labels
  const ticks = [0, maxY / 2, maxY];
  for (const t of ticks) {
    svg += `<line x1="${PAD.l}" x2="${W - PAD.r}" y1="${y(t).toFixed(1)}" y2="${y(t).toFixed(1)}" `
      + `stroke="${ink}" stroke-opacity="0.22" stroke-width="1"/>`
      + `<text x="${PAD.l - 4}" y="${(y(t) + 3).toFixed(1)}" text-anchor="end" font-size="8" fill="${ink}">`
      + `${Math.round(t).toLocaleString('en-US')}</text>`;
  }

  for (const b of bands) {
    for (const seg of segs) {
      const [a, z] = seg;
      let d = '';
      for (let i = a; i <= z; i++) d += (i === a ? 'M' : 'L') + x(i).toFixed(1) + ' ' + y(b.upper[i]).toFixed(1) + ' ';
      for (let i = z; i >= a; i--) d += 'L' + x(i).toFixed(1) + ' ' + y(b.lower[i]).toFixed(1) + ' ';
      d += 'Z';
      svg += `<path d="${d}" fill="${colors[b.key]}" fill-opacity="${isContext(seg) ? 0.4 : 1}" `
        + `stroke="${surface}" stroke-width="2" stroke-linejoin="round"/>`;
    }
  }

  /* Dashed outline along the top of the faded era, so the pre-2010 span reads as
   * a dashed line rather than merely a lighter one (the shape Austin asked for). */
  for (const seg of segs) {
    if (!isContext(seg)) continue;
    const [a, z] = seg;
    let d = '';
    for (let i = a; i <= z; i++) d += (i === a ? 'M' : 'L') + x(i).toFixed(1) + ' ' + y(base[i]).toFixed(1) + ' ';
    svg += `<path d="${d}" fill="none" stroke="${ink}" stroke-width="1.2" stroke-opacity="0.75" `
      + `stroke-dasharray="3 2" stroke-linejoin="round"/>`;
  }

  // Mark the unreported span so a gap reads as "no record", not as "no permits".
  const bandW = (W - PAD.l - PAD.r) / Math.max(1, series.length - 1);
  reported.forEach((ok, i) => {
    if (ok) return;
    svg += `<rect x="${(x(i) - bandW / 2).toFixed(1)}" y="${PAD.t}" width="${bandW.toFixed(1)}" `
      + `height="${(H - PAD.t - PAD.b).toFixed(1)}" fill="${ink}" fill-opacity="0.07"/>`;
  });

  /* A tick under every year the permit office reported no month at all. The
   * number above it is the Census's estimate, not the town's count. */
  series.forEach((d, i) => {
    if (!imputed.has(d.year)) return;
    svg += `<rect x="${(x(i) - bandW / 2 + 0.5).toFixed(1)}" y="${(H - PAD.b + 1).toFixed(1)}" `
      + `width="${Math.max(2, bandW - 1).toFixed(1)}" height="3" fill="${ink}" fill-opacity="0.55"/>`;
  });

  // the 2010 boundary
  if (mIdx > 0) {
    svg += `<line x1="${x(mIdx).toFixed(1)}" x2="${x(mIdx).toFixed(1)}" y1="${PAD.t - 10}" `
      + `y2="${H - PAD.b}" stroke="${ink}" stroke-width="1" stroke-opacity="0.55" stroke-dasharray="2 2"/>`
      + `<text x="${(x(mIdx) - 3).toFixed(1)}" y="${PAD.t - 4}" text-anchor="end" font-size="7.5" `
      + `fill="${ink}" fill-opacity="0.9">context</text>`
      + `<text x="${(x(mIdx) + 3).toFixed(1)}" y="${PAD.t - 4}" text-anchor="start" font-size="7.5" `
      + `fill="${ink}" fill-opacity="0.9">counted in the metric →</text>`;
  }

  // x labels: first, the metric boundary, last
  const labelIdx = [...new Set([0, mIdx > 0 ? mIdx : Math.floor(series.length / 2), series.length - 1])];
  labelIdx.forEach(i => {
    svg += `<text x="${x(i).toFixed(1)}" y="${H - PAD.b + 13}" text-anchor="${i === 0 ? 'start' : i === series.length - 1 ? 'end' : 'middle'}" `
      + `font-size="8" fill="${ink}">${years[i]}</text>`;
  });

  svg += `<line id="chart-crosshair" x1="0" x2="0" y1="${PAD.t}" y2="${H - PAD.b}" `
    + `stroke="${ink}" stroke-width="1" stroke-opacity="0.6" visibility="hidden"/>`;
  svg += `<rect id="chart-hit" x="${PAD.l}" y="${PAD.t}" width="${W - PAD.l - PAD.r}" `
    + `height="${H - PAD.t - PAD.b}" fill="transparent" style="cursor:crosshair"/>`;
  svg += '</svg>';

  const notes = [];
  if (mIdx > 0) {
    notes.push(`Faded and dashed before ${mStart}: shown for the pre-2008 baseline, `
      + `not counted in any figure on this page.`);
  }
  if (imputed.size) {
    notes.push(`Ticked years are ones this permit office reported no months to the `
      + `Census; the figure shown for them is the Census's own estimate.`);
  }

  const legend = '<div class="chart-legend">' + TYPE_ORDER.map(k =>
    `<span><i style="background:${colors[k]}"></i>${TYPE_LABEL[k]}</span>`).join('') + '</div>'
    + (notes.length ? `<p class="chart-note">${notes.join(' ')}</p>` : '');

  return { html: `<div class="chart-holder">${svg}<div class="chart-tip" id="chart-tip" hidden></div></div>${legend}`,
           geom: { W, PAD, series, colors, imputed, mStart } };
}

function wireChartHover(geom) {
  const holder = document.querySelector('#detail .chart-holder');
  if (!holder) return;
  const svg = holder.querySelector('svg');
  const hit = holder.querySelector('#chart-hit');
  const tip = holder.querySelector('#chart-tip');
  const cross = holder.querySelector('#chart-crosshair');
  const { W, PAD, series, colors, imputed, mStart } = geom;
  if (!hit) return;

  function move(ev) {
    const r = svg.getBoundingClientRect();
    const px = ((ev.touches ? ev.touches[0].clientX : ev.clientX) - r.left) / r.width * W;
    const frac = (px - PAD.l) / (W - PAD.l - PAD.r);
    const i = Math.max(0, Math.min(series.length - 1, Math.round(frac * (series.length - 1))));
    const d = series[i];
    cross.setAttribute('visibility', 'visible');
    const cx = PAD.l + (W - PAD.l - PAD.r) * (series.length < 2 ? 0 : i / (series.length - 1));
    cross.setAttribute('x1', cx); cross.setAttribute('x2', cx);
    tip.hidden = false;
    const caveats = [];
    if (imputed && imputed.has(d.year)) caveats.push('Census estimate — the permit office reported no months this year');
    if (mStart !== undefined && d.year < mStart) caveats.push(`Before ${mStart}: context only, not counted in the metric`);
    tip.innerHTML = (d.total === null || d.total === undefined)
      ? `<b>${d.year}</b><div class="row"><span>No BPS record for this year</span></div>`
      : `<b>${d.year} — ${fmtInt(d.total)} units</b>`
        + TYPE_ORDER.map(k => `<div class="row"><span><i style="background:${colors[k]}"></i>${TYPE_LABEL[k]}</span><span>${fmtInt(d[k])}</span></div>`).join('')
        + caveats.map(c => `<div class="row caveat"><span>${c}</span></div>`).join('');
    const left = Math.max(0, Math.min(holder.clientWidth - 150, cx / W * holder.clientWidth - 70));
    tip.style.left = left + 'px';
    tip.style.top = '0px';
  }
  function leave() { tip.hidden = true; cross.setAttribute('visibility', 'hidden'); }

  hit.addEventListener('mousemove', move);
  hit.addEventListener('touchstart', move, { passive: true });
  hit.addEventListener('touchmove', move, { passive: true });
  hit.addEventListener('mouseleave', leave);
  hit.addEventListener('touchend', leave);
}

/* ---------------------------------------------------------------- detail */

async function loadShard(geoid) {
  if (SHARDS.has(geoid)) return SHARDS.get(geoid);
  const resp = await fetch(`data/places/${geoid}.json`);
  if (!resp.ok) throw new Error(`shard ${geoid}: HTTP ${resp.status}`);
  const shard = await resp.json();
  SHARDS.set(geoid, shard);
  return shard;
}

async function renderDetail(geoid) {
  const p = BY_GEOID.get(geoid);
  if (!p) return;
  const panel = document.getElementById('detail');
  document.getElementById('detail-name').textContent = p.namelsad || p.name;
  panel.hidden = false;

  let shard;
  try {
    shard = await loadShard(geoid);
  } catch (err) {
    document.getElementById('detail-sub').textContent = '';
    document.getElementById('detail-body').innerHTML =
      `<div class="coverage-note">Could not load the detail file for this place: ${err.message}</div>`;
    return;
  }

  document.getElementById('detail-sub').textContent =
    [shard.county || null,
     shard.pop2020 === null ? '2020 population not published' : fmtInt(shard.pop2020) + ' residents (2020)']
      .filter(Boolean).join(' · ');

  const body = document.getElementById('detail-body');

  /* SPEC.md §5: a place that is not reporting gets the explanation instead of
   * metrics. It is not shown as a zero anywhere. */
  if (shard.coverage !== 'reporting') {
    body.innerHTML = `<div class="coverage-note"><strong>No permit-office data for this place.</strong>
      <p style="margin:6px 0 0">${shard.coverage_note}</p></div>
      <p class="detail-source">${META.source_line}. Built ${META.build_date}.</p>`;
    return;
  }

  const pctText = shard.pct_growth === null
    ? 'Not available'
    : fmtPct(shard.pct_growth);
  const compare = `Since ${META.metric_start}, Illinois grew its housing stock by `
    + `${fmtPct(META.il_pct_growth)} and the U.S. by ${fmtPct(META.us_pct_growth)}. `
    + (shard.pct_growth === null
        ? `${shard.short_name} has no 2010 housing count published, so a percentage cannot be computed.`
        : `${shard.short_name} grew by ${fmtPct(shard.pct_growth)} — ${fmtInt(shard.units_total_2010)} units.`);

  const chart = stackedAreaChart(shard.series.map(d => ({ ...d })), shard);

  const byTypeRows = TYPE_ORDER.map(k => `<tr>
      <td style="text-align:left"><i style="display:inline-block;width:10px;height:10px;border-radius:2px;background:${seriesColors()[k]};margin-right:6px"></i>${TYPE_LABEL[k]}</td>
      <td class="num">${fmtInt(shard.units_by_type[k])}</td>
      <td class="num">${fmtPct(shard.pct_growth_by_type[k], 2)}</td>
    </tr>`).join('');

  body.innerHTML = `
    <div class="headline">
      <span class="pct">${pctText}</span>
      <span class="units">${shard.units_total_2010 === null ? '' : fmtInt(shard.units_total_2010) + ' units'}</span>
    </div>
    <p class="headline-caption">Units authorized by permit ${shard.first_metric_year || META.metric_start}–${META.ymax}, against
      ${shard.h1_2010 === null ? 'no published' : fmtInt(shard.h1_2010)} housing units counted here on April 1, 2010.${
        shard.metric_years_reported < (META.ymax - META.metric_start + 1)
          ? ` This place has a permit record for ${shard.metric_years_reported} of the ${META.ymax - META.metric_start + 1} years since ${META.metric_start}; the rest are blank in the chart, not zero.`
          : ''}</p>

    <div class="compare">${compare}</div>

    ${shard.months_note ? `<div class="coverage-note">${shard.months_note}</div>` : ''}

    ${shard.zero_mf ? `<div class="flag"><strong>No 5+ unit buildings.</strong> No units in
      buildings of five or more have been permitted here since ${shard.first_metric_year || META.metric_start}.${
        shard.mf34_total
          ? ` ${fmtInt(shard.mf34_total)} unit${shard.mf34_total === 1 ? '' : 's'} in 3–4 unit buildings ${shard.mf34_total === 1 ? 'was' : 'were'} permitted over the same period — this measure counts those separately, so a town can appear here and still have permitted a small apartment building.`
          : ' No 3–4 unit buildings either.'}${
        shard.first_metric_year && shard.first_metric_year > META.metric_start
          ? ` This place's permit office first reported to the Census in ${shard.first_metric_year}, so there is no record for ${META.metric_start}–${shard.first_metric_year - 1}.`
          : ''}</div>` : ''}

    <p class="chart-title">Units permitted per year by structure type</p>
    <p class="chart-sub">${META.ymin}–${META.ymax}. Hover or drag across the chart for a year.
      Only ${META.metric_start}–${META.ymax} feeds the figures above.</p>
    ${chart.html}

    <div class="detail-table">
      <table>
        <caption>Since ${shard.first_metric_year || META.metric_start}: units permitted, and each type as a share of the 2010 stock.</caption>
        <thead><tr><th scope="col" style="text-align:left">Structure type</th><th scope="col">Units</th><th scope="col">% of 2010 stock</th></tr></thead>
        <tbody>${byTypeRows}
          <tr style="border-top:2px solid var(--rule-strong)">
            <td style="text-align:left"><strong>All types</strong></td>
            <td class="num"><strong>${fmtInt(shard.units_total_2010)}</strong></td>
            <td class="num"><strong>${fmtPct(shard.pct_growth, 2)}</strong></td>
          </tr>
          <tr><td style="text-align:left">All types, ${shard.first_year_reported || META.ymin}–${META.ymax}</td>
            <td class="num">${fmtInt(shard.units_total_2000)}</td><td class="num">—</td></tr>
        </tbody>
      </table>
    </div>

    <p class="detail-source">${META.source_line}. Built ${META.build_date}.
      BPS counts units <em>authorized by permit</em>, not units completed, and does not
      subtract demolitions.${shard.ahpaa_status ? ' AHPAA status: ' + shard.ahpaa_status + '.' : ''}</p>`;

  wireChartHover(chart.geom);
}

function selectPlace(geoid) {
  state.selected = geoid;
  applyStyle();
  renderTable();
  renderDetail(geoid);
  writeHash();
}

function closeDetail() {
  state.selected = null;
  document.getElementById('detail').hidden = true;
  applyStyle();
  renderTable();
  writeHash();
}

/* ---------------------------------------------------------------- hash */

function writeHash() {
  const parts = [];
  if (state.selected) parts.push('place=' + state.selected);
  parts.push('metric=' + state.metric);
  parts.push('type=' + state.type);
  parts.push('pop=' + state.popMin);
  if (state.zeroMf) parts.push('zeromf=1');
  if (state.zeroMf3p) parts.push('zeromf3p=1');
  if (state.ahpaa) parts.push('ahpaa=1');
  parts.push('sort=' + state.sort.key + ':' + state.sort.dir);
  const h = '#' + parts.join('&');
  if (location.hash !== h) history.replaceState(null, '', h);
}

function readHash() {
  const h = location.hash.replace(/^#/, '');
  if (!h) return null;
  const q = {};
  for (const kv of h.split('&')) {
    const i = kv.indexOf('=');
    if (i > 0) q[kv.slice(0, i)] = decodeURIComponent(kv.slice(i + 1));
  }
  if (q.metric === 'pct_growth' || q.metric === 'units_total') state.metric = q.metric;
  if (TYPE_ORDER.includes(q.type) || q.type === 'all') state.type = q.type;
  if (q.pop !== undefined && ['0', '5000', '25000'].includes(q.pop)) state.popMin = +q.pop;
  state.zeroMf = q.zeromf === '1';
  state.zeroMf3p = q.zeromf3p === '1';
  state.ahpaa = q.ahpaa === '1' && !!(META && META.ahpaa.enabled);
  if (q.sort) {
    const [k, d] = q.sort.split(':');
    if (k) state.sort = { key: k, dir: d === 'desc' ? 'desc' : 'asc' };
  }
  return q.place || null;
}

/* ---------------------------------------------------------------- controls */

function syncControls() {
  document.querySelectorAll('[data-metric]').forEach(b =>
    b.setAttribute('aria-pressed', String(b.dataset.metric === state.metric)));
  document.querySelectorAll('[data-type]').forEach(b =>
    b.setAttribute('aria-pressed', String(b.dataset.type === state.type)));
  document.querySelectorAll('[data-pop]').forEach(b =>
    b.setAttribute('aria-pressed', String(+b.dataset.pop === state.popMin)));
  document.getElementById('toggle-zero-mf').checked = state.zeroMf;
  document.getElementById('toggle-zero-mf3p').checked = state.zeroMf3p;
  document.getElementById('toggle-ahpaa').checked = state.ahpaa;
}

function refresh() {
  syncControls();
  applyStyle();
  renderLegend();
  renderTable();
  writeHash();
}

function wireControls() {
  document.querySelectorAll('[data-metric]').forEach(b => b.onclick = () => {
    state.metric = b.dataset.metric; refresh();
  });
  document.querySelectorAll('[data-type]').forEach(b => b.onclick = () => {
    state.type = b.dataset.type; refresh();
  });
  document.querySelectorAll('[data-pop]').forEach(b => b.onclick = () => {
    state.popMin = +b.dataset.pop; refresh();
  });
  document.getElementById('toggle-zero-mf').onchange = e => {
    state.zeroMf = e.target.checked; refresh();
  };
  document.getElementById('toggle-zero-mf3p').onchange = e => {
    state.zeroMf3p = e.target.checked; refresh();
  };
  /* .hint is a single clipped line by design, so the short form goes here and
   * the counts go in the footer, which has room for them. */
  const mfFull = `"5+ unit" counts units in buildings of five or more; buildings of `
    + `three or four are counted separately, so a municipality can have permitted no `
    + `5+ unit building and still have permitted a 3–4 unit one. `
    + `${fmtInt(META.n_zero_mf)} municipalities have permitted no 5+ unit building since `
    + `${META.metric_start}; ${fmtInt(META.n_zero_mf3p)} have permitted nothing above a duplex.`;
  const mfHint = document.getElementById('mf-hint');
  mfHint.textContent = '3–4 unit buildings are counted separately from 5+ unit ones.';
  mfHint.title = mfFull;
  document.getElementById('label-zero-mf').title = mfFull;
  document.getElementById('label-zero-mf3p').title =
    'Municipalities that have permitted nothing larger than a two-unit building since '
    + META.metric_start + '.';
  document.getElementById('detail-close').onclick = closeDetail;

  /* SPEC.md §3.5 / §6.2: with no AHPAA rows loaded the filter is disabled and
   * says why. It is never populated from anything but data/manual/ahpaa.csv. */
  const ahpaaInput = document.getElementById('toggle-ahpaa');
  const ahpaaLabel = document.getElementById('label-ahpaa');
  const hint = document.getElementById('ahpaa-hint');
  if (META.ahpaa.enabled) {
    ahpaaInput.onchange = e => { state.ahpaa = e.target.checked; refresh(); };
    hint.textContent = META.ahpaa.as_of
      ? `AHPAA list as of ${META.ahpaa.as_of} (${fmtInt(META.ahpaa.rows)} municipalities).`
      : `AHPAA list loaded (${fmtInt(META.ahpaa.rows)} municipalities).`;
  } else {
    ahpaaInput.disabled = true;
    ahpaaLabel.setAttribute('aria-disabled', 'true');
    ahpaaLabel.title = META.ahpaa.disabled_reason;
    /* The full reason lives in the tooltip and the footer. The inline hint stays
     * one line so the map is not pushed off a phone screen. */
    ahpaaLabel.setAttribute('aria-label', META.ahpaa.disabled_reason);
    hint.textContent = 'AHPAA filter unavailable — the IHDA list has not been loaded.';
    hint.title = META.ahpaa.disabled_reason;
  }

  window.addEventListener('hashchange', () => {
    const place = readHash();
    syncControls();
    applyStyle();
    renderLegend();
    renderTable();
    if (place) renderDetail(place); else closeDetail();
  });

  const mq = window.matchMedia('(prefers-color-scheme: dark)');
  mq.addEventListener('change', () => {
    renderLegend();
    if (state.selected) renderDetail(state.selected);
    if (map && layersReady) {
      map.setPaintProperty('bg', 'background-color', cssVar('--map-bg'));
      map.setPaintProperty('state-fill', 'fill-color', cssVar('--map-land'));
      map.setPaintProperty('state-line', 'line-color', cssVar('--state-line'));
      if (map.hasImage('hatch')) map.updateImage('hatch', hatchImage());
    }
  });
}

function renderFooter() {
  const c = META.coverage_counts || {};
  document.getElementById('sourceline').textContent =
    `${META.source_line} · built ${META.build_date}`;
  document.getElementById('footer-method').textContent =
    `Percent growth is units authorized by permit ${META.metric_start}–${META.ymax} divided by the `
    + `housing units counted in that municipality on April 1, 2010. BPS counts units authorized, `
    + `not completed, and does not subtract demolitions — this is "how much was added", not net change.`;
  document.getElementById('footer-coverage').textContent =
    `${fmtInt(META.n_places)} Illinois places. ${fmtInt(c.reporting || 0)} have a permit office `
    + `reporting to the Census; ${fmtInt(c.no_permit_office || 0)} do not and are drawn with a hatch `
    + `rather than a zero-value colour, excluded from rankings and from statewide totals. `
    + `${fmtInt(META.n_zero_mf)} reporting municipalities have permitted no building of 5+ units since `
    + `${META.metric_start}, and ${fmtInt(META.n_zero_mf3p)} have permitted nothing above a duplex. `
    + `A permit office can also file for only part of a year: `
    + `${fmtInt((META.months_counts || {}).full || 0)} of the reporting municipalities reported all `
    + `twelve months in every year since ${META.metric_start}, and `
    + `${fmtInt((META.months_counts || {}).none || 0)} reported none at all — for those, every figure `
    + `is the Census's own estimate, and they are left out of the counts above.`;
  document.getElementById('footer-join').textContent =
    `Illinois permitted ${fmtInt(META.il_units_2010_ymax)} units ${META.metric_start}–${META.ymax} `
    + `against ${fmtInt(META.il_h1_2010)} housing units in 2010 (${fmtPct(META.il_pct_growth)}); the `
    + `U.S. figure is ${fmtPct(META.us_pct_growth)}. `
    + `${fmtPct(META.join.join_rate_municipal_pct, 2)} of Illinois municipal permit records joined to `
    + `a mapped boundary.`;
}

/* ---------------------------------------------------------------- boot */

const app = {
  ready: false,
  state: () => ({
    metric: state.metric, structure: state.type, popMin: state.popMin,
    zeroMf: state.zeroMf, zeroMf3p: state.zeroMf3p,
    ahpaa: state.ahpaa, selected: state.selected
  }),
  featureCount: () => FEATURES.length,
  highlightedCount: () => activeGeoids.length,
  highlightedGeoids: () => activeGeoids.slice(),
  selectPlace,
  closeDetail,
  detail: () => {
    const panel = document.getElementById('detail');
    return {
      open: !panel.hidden,
      geoid: state.selected,
      name: document.getElementById('detail-name').textContent,
      percentText: (document.querySelector('#detail .pct') || {}).textContent || '',
      unitsText: (document.querySelector('#detail .units') || {}).textContent || '',
      compare: (document.querySelector('#detail .compare') || {}).textContent || ''
    };
  },
  meta: () => META
};
window.app = app;

(async function boot() {
  try {
    const [meta, places, counties, stateOutline] = await Promise.all([
      fetch('data/meta.json').then(r => r.json()),
      fetch('data/places.geojson').then(r => r.json()),
      fetch('data/counties.geojson').then(r => r.json()),
      fetch('data/state.geojson').then(r => r.json())
    ]);
    META = meta;
    FEATURES = places.features.map(f => f.properties);
    for (const p of FEATURES) BY_GEOID.set(p.geoid, p);

    const place = readHash();
    renderFooter();
    wireControls();
    buildMap(places, counties, stateOutline);
    syncControls();
    renderLegend();
    renderTable();

    /* Wait for the map's own load event, with a ceiling so that a WebGL problem
     * surfaces as a console error in site check 1 rather than as a hang. */
    await new Promise(res => {
      if (layersReady) return res();
      let done = false;
      const finish = () => { if (!done) { done = true; res(); } };
      map.once('load', () => setTimeout(finish, 0));
      setTimeout(finish, 20000);
    });
    applyStyle();
    if (place && BY_GEOID.has(place)) {
      state.selected = place;
      applyStyle();
      renderTable();
      await renderDetail(place);
    }
    writeHash();
    app.ready = true;
  } catch (err) {
    app.ready = false;
    document.body.insertAdjacentHTML('afterbegin',
      `<div style="padding:16px;background:#fdecea;color:#611a15;font:14px system-ui">
         Could not load the data files: ${err && err.message ? err.message : err}
       </div>`);
    throw err;
  }
})();
