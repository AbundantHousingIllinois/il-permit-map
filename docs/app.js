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
let map = null;
let activeGeoids = [];      // single source of truth for "highlighted"
let layersReady = false;    // the fill layers are added in the map's load handler

/* ---------------------------------------------------------------- helpers */

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

function divergingStops() { return divergingStopsAt(midpoint()); }

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
  /* Housing-count change 2010→2020 minus units permitted 2010–2019. Blank for
   * any place whose office did not report every month of that decade. */
  { key: 'built_gap', label: '2020 count vs permits', type: 'num', fmt: fmtSigned },
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
      /* A missing value sorts last in either direction. Sorting it as -Infinity
       * put every blank at the top of an ascending sort, which reads as "lowest"
       * -- a missing value presented as a ranking. */
      const xm = x === null || x === undefined, ym = y === null || y === undefined;
      if (xm || ym) return xm && ym ? 0 : xm ? 1 : -1;
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

/* ---------------------------------------------------------------- detail */

/* The panel itself is drawn by renderPlaceDetail() in detail.js. */
function renderDetail(geoid) {
  const p = BY_GEOID.get(geoid);
  if (!p) return Promise.resolve();
  return renderPlaceDetail(geoid, p.namelsad || p.name);
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
