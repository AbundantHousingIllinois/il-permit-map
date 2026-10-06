/* Illinois Housing Permits -- the legislator view (districts.html).
 *
 * One map, a Senate / House toggle. Municipalities are coloured exactly as on the
 * main map (shared.js); district outlines are drawn over them. Choosing a
 * district shows its member and every municipality that overlaps it.
 *
 * No runtime API calls: everything is read from docs/data/, built by build.py.
 * A missing value is never drawn or printed as zero.
 */
'use strict';

const view = { chamber: 'senate', district: null, place: null, sort: null };

let META = null;
let DIST = null;            // districts.json
let OUTLINES = {};          // chamber -> geojson
let PLACES = null;          // places.geojson, for the printable sheet's map
let map = null;
let layersReady = false;

const PARTY_SHORT = { Democratic: 'D', Republican: 'R', Independent: 'I' };

function chamberInfo(ch) { return DIST.chambers[ch]; }
function districtRow(ch, d) { return chamberInfo(ch).districts[d - 1]; }
function memberLine(ch, d) {
  const m = districtRow(ch, d).member;
  if (!m) return 'no member listed';
  if (m.vacant) return 'vacant';
  return `${m.name}${m.party ? ` (${PARTY_SHORT[m.party] || m.party})` : ''}`;
}
/* A polygon covering the world with the district cut out of it, for the fade.
 * MapLibre tells a hole from an outer ring by winding, so each hole is wound
 * against the outer ring. (The district's own holes are left unfaded.) */
function maskOutside(f) {
  const area = r => r.reduce((a, [x, y], i) => {
    const [x2, y2] = r[(i + 1) % r.length];
    return a + x * y2 - x2 * y;
  }, 0);
  const world = [[-180, -85], [180, -85], [180, 85], [-180, 85], [-180, -85]];
  const polys = f.geometry.type === 'Polygon' ? [f.geometry.coordinates] : f.geometry.coordinates;
  const holes = polys.map(p => Math.sign(area(p[0])) === Math.sign(area(world)) ? p[0].slice().reverse() : p[0]);
  return { type: 'Feature', properties: {}, geometry: { type: 'Polygon', coordinates: [world, ...holes] } };
}

function districtLabel(ch, d) { return `${chamberInfo(ch).label} District ${d}`; }

/* ---------------------------------------------------------------- map */

/* District numbers. MapLibre text labels need a glyph server -- a second runtime
 * request this site does not make -- but an icon needs nothing, so each number is
 * drawn to a small canvas in the page's own font and placed as an icon. MapLibre's
 * own collision detection then hides numbers that would overlap, largest district
 * first, and brings them back as the reader zooms in. */
function labelImage(text, selected) {
  const r = window.devicePixelRatio || 1;
  const font = getComputedStyle(document.body).fontFamily;
  const c = document.createElement('canvas');
  /* Read back once with getImageData; saying so up front avoids a GPU stall. */
  const g = c.getContext('2d', { willReadFrequently: true });
  g.font = `700 ${13 * r}px ${font}`;
  const w = Math.ceil(g.measureText(text).width + 12 * r), h = Math.ceil(20 * r);
  c.width = w; c.height = h;
  g.font = `700 ${13 * r}px ${font}`;
  g.textAlign = 'center';
  g.textBaseline = 'middle';
  /* A pill behind the number, so it reads over any fill colour. */
  g.fillStyle = selected ? '#E87722' : cssVar('--surface-raised');
  g.strokeStyle = selected ? '#E87722' : cssVar('--ink-secondary');
  g.lineWidth = r;
  const rad = h / 2;
  g.beginPath();
  g.moveTo(rad, 0.5 * r); g.lineTo(w - rad, 0.5 * r);
  g.arc(w - rad, h / 2, rad - 0.5 * r, -Math.PI / 2, Math.PI / 2);
  g.lineTo(rad, h - 0.5 * r);
  g.arc(rad, h / 2, rad - 0.5 * r, Math.PI / 2, -Math.PI / 2);
  g.closePath();
  g.fill(); g.stroke();
  g.fillStyle = selected ? '#fff' : cssVar('--ink');
  g.fillText(text, w / 2, h / 2 + 0.5 * r);
  return { image: g.getImageData(0, 0, w, h), pixelRatio: r };
}

function addLabelImages(update) {
  for (const ch of ['senate', 'house']) {
    for (const r of chamberInfo(ch).districts) {
      for (const sel of [false, true]) {
        const id = `lbl-${ch}-${r.district}${sel ? '-sel' : ''}`;
        const { image, pixelRatio } = labelImage(String(r.district), sel);
        if (map.hasImage(id)) { if (update) map.removeImage(id); else continue; }
        map.addImage(id, image, { pixelRatio });
      }
    }
  }
}

function labelPoints(ch) {
  return {
    type: 'FeatureCollection',
    features: chamberInfo(ch).districts.filter(r => r.label).map(r => ({
      type: 'Feature',
      properties: { district: r.district, aland: r.aland || 0 },
      geometry: { type: 'Point', coordinates: r.label }
    }))
  };
}

function bboxOf(feature) {
  let w = 180, s = 90, e = -180, n = -90;
  const walk = c => {
    if (typeof c[0] === 'number') {
      w = Math.min(w, c[0]); e = Math.max(e, c[0]);
      s = Math.min(s, c[1]); n = Math.max(n, c[1]);
    } else c.forEach(walk);
  };
  walk(feature.geometry.coordinates);
  return [[w, s], [e, n]];
}

function buildMap(places, stateOutline) {
  map = new maplibregl.Map({
    container: 'map',
    /* Same no-tiles rule as the main map: plain background, the state
     * silhouette, and static geometry shipped with the site. */
    style: {
      version: 8,
      sources: {
        places: { type: 'geojson', data: places, promoteId: 'geoid' },
        state: { type: 'geojson', data: stateOutline },
        senate: { type: 'geojson', data: OUTLINES.senate, promoteId: 'district' },
        house: { type: 'geojson', data: OUTLINES.house, promoteId: 'district' }
      },
      layers: [
        { id: 'bg', type: 'background', paint: { 'background-color': cssVar('--map-bg') } },
        { id: 'state-fill', type: 'fill', source: 'state',
          paint: { 'fill-color': cssVar('--map-land') } }
      ]
    },
    bounds: META.state_bbox,
    fitBoundsOptions: { padding: 12 },
    attributionControl: false,
    dragRotate: false,
    pitchWithRotate: false
  });
  window.map = map;
  map.addControl(new maplibregl.NavigationControl({ showCompass: false }), 'top-right');
  map.addControl(new maplibregl.AttributionControl({
    compact: true,
    customAttribution: 'U.S. Census Building Permits Survey; 2010 Decennial Census; TIGER/Line; Open States'
  }), 'bottom-right');

  map.on('load', () => {
    if (!map.hasImage('hatch')) map.addImage('hatch', hatchImage());
    map.addLayer({
      id: 'places-gap', type: 'fill', source: 'places',
      filter: ['!=', ['get', 'coverage'], 'reporting'],
      paint: { 'fill-pattern': 'hatch', 'fill-opacity': 0.9 }
    });
    const expr = ['interpolate', ['linear'], ['to-number', ['get', 'pct_growth'], 0]];
    for (const [v, c] of divergingStopsAt(META.il_pct_growth)) expr.push(v, c);
    map.addLayer({
      id: 'places-fill', type: 'fill', source: 'places',
      filter: ['all', ['==', ['get', 'coverage'], 'reporting'], ['!=', ['get', 'pct_growth'], null]],
      paint: { 'fill-color': expr, 'fill-opacity': 0.9 }
    });
    map.addLayer({
      id: 'places-line', type: 'line', source: 'places',
      paint: { 'line-color': cssVar('--rule-strong'), 'line-width': 0.3, 'line-opacity': 0.7 }
    });
    /* Everything outside the selected district, faded toward the page color, so
     * the district's own part of the map stands out. An orange edge alone was
     * hard to see over the orange (below-Illinois) towns (Austin, 2026-10-03). */
    map.addSource('district-mask', { type: 'geojson', data: { type: 'FeatureCollection', features: [] } });
    map.addLayer({
      id: 'district-mask', type: 'fill', source: 'district-mask',
      paint: { 'fill-color': cssVar('--surface'), 'fill-opacity': 0.62 }
    });
    for (const ch of ['senate', 'house']) {
      /* A near-transparent fill so the whole district, not just its edge, takes
       * a tap and a hover. */
      map.addLayer({
        id: `${ch}-hit`, type: 'fill', source: ch,
        layout: { visibility: ch === view.chamber ? 'visible' : 'none' },
        paint: { 'fill-color': '#000', 'fill-opacity': 0.01 }
      });
      /* The district edge has to read as a different kind of line from a
       * municipal border, which follows the same streets: a pale casing under a
       * heavier dark stroke. */
      map.addLayer({
        id: `${ch}-casing`, type: 'line', source: ch,
        layout: { visibility: ch === view.chamber ? 'visible' : 'none', 'line-join': 'round' },
        paint: { 'line-color': cssVar('--surface'), 'line-width': ['interpolate', ['linear'], ['zoom'], 6, 2.5, 11, 5], 'line-opacity': 0.8 }
      });
      map.addLayer({
        id: `${ch}-line`, type: 'line', source: ch,
        layout: { visibility: ch === view.chamber ? 'visible' : 'none', 'line-join': 'round' },
        paint: { 'line-color': cssVar('--ink'), 'line-width': ['interpolate', ['linear'], ['zoom'], 6, 1, 11, 2.2] }
      });
      /* The selected district's edge: dark ink over a wide pale casing. */
      map.addLayer({
        id: `${ch}-selected-casing`, type: 'line', source: ch,
        filter: ['==', ['get', 'district'], -1],
        layout: { visibility: ch === view.chamber ? 'visible' : 'none', 'line-join': 'round' },
        paint: { 'line-color': cssVar('--surface'), 'line-width': 7 }
      });
      map.addLayer({
        id: `${ch}-selected`, type: 'line', source: ch,
        filter: ['==', ['get', 'district'], -1],
        layout: { visibility: ch === view.chamber ? 'visible' : 'none', 'line-join': 'round' },
        paint: { 'line-color': cssVar('--ink'), 'line-width': 3 }
      });
      map.on('click', `${ch}-hit`, e => {
        if (e.features && e.features.length) selectDistrict(ch, e.features[0].properties.district, true);
      });
      map.on('mousemove', `${ch}-hit`, e => {
        map.getCanvas().style.cursor = 'pointer';
        const d = e.features && e.features[0] && e.features[0].properties.district;
        if (!d) return;
        const tip = document.getElementById('district-tip');
        tip.textContent = `${ch === 'senate' ? 'Senate' : 'House'} ${d} · ${memberLine(ch, d)}`;
        tip.style.left = `${e.point.x + 12}px`;
        tip.style.top = `${e.point.y + 12}px`;
        tip.hidden = false;
      });
      map.on('mouseleave', `${ch}-hit`, () => {
        map.getCanvas().style.cursor = '';
        document.getElementById('district-tip').hidden = true;
      });
    }
    /* The municipality open in the detail panel, outlined over the districts. */
    map.addLayer({
      id: 'place-selected', type: 'line', source: 'places',
      filter: ['==', ['get', 'geoid'], view.place || ''],
      layout: { 'line-join': 'round' },
      paint: { 'line-color': placeOutlineColor(), 'line-width': 3 }
    });
    addLabelImages(false);
    for (const ch of ['senate', 'house']) {
      map.addSource(`${ch}-labels`, { type: 'geojson', data: labelPoints(ch) });
      map.addLayer({
        id: `${ch}-labels`, type: 'symbol', source: `${ch}-labels`,
        layout: {
          visibility: ch === view.chamber ? 'visible' : 'none',
          'icon-image': ['concat', `lbl-${ch}-`, ['to-string', ['get', 'district']]],
          'icon-allow-overlap': false,
          'icon-padding': 2,
          /* Lower sorts first and wins a collision: the larger district keeps its
           * number at statewide zoom, the small Chicago ones appear on zooming in. */
          'symbol-sort-key': ['-', 0, ['get', 'aland']]
        }
      });
    }
    layersReady = true;
    applyView(false);
  });
}

function placeOutlineColor() { return darkMode() ? '#6fa8d8' : '#004B87'; }

function applyView(fly) {
  if (!layersReady) return;
  for (const ch of ['senate', 'house']) {
    const vis = ch === view.chamber ? 'visible' : 'none';
    for (const suffix of ['hit', 'casing', 'line', 'selected-casing', 'selected', 'labels']) map.setLayoutProperty(`${ch}-${suffix}`, 'visibility', vis);
    /* The chosen district's number turns orange and always shows. */
    const sel = ch === view.chamber && view.district ? view.district : -1;
    map.setLayoutProperty(`${ch}-labels`, 'icon-image', ['case',
      ['==', ['get', 'district'], sel],
      ['concat', `lbl-${ch}-`, ['to-string', ['get', 'district']], '-sel'],
      ['concat', `lbl-${ch}-`, ['to-string', ['get', 'district']]]]);
    map.setLayoutProperty(`${ch}-labels`, 'symbol-sort-key', ['case',
      ['==', ['get', 'district'], sel], -1e15, ['-', 0, ['get', 'aland']]]);
    for (const layer of [`${ch}-selected`, `${ch}-selected-casing`]) {
      map.setFilter(layer, ['==', ['get', 'district'],
        ch === view.chamber && view.district ? view.district : -1]);
    }
  }
  const sel = view.district
    && OUTLINES[view.chamber].features.find(x => x.properties.district === view.district);
  map.getSource('district-mask').setData(sel ? maskOutside(sel)
    : { type: 'FeatureCollection', features: [] });
  map.setFilter('place-selected', ['==', ['get', 'geoid'], view.place || '']);
  if (fly && view.district) {
    const f = OUTLINES[view.chamber].features.find(x => x.properties.district === view.district);
    if (f) map.fitBounds(bboxOf(f), { padding: 30, maxZoom: 12, duration: 600 });
  }
}

/* ---------------------------------------------------------------- panel */

function sharePct(s) {
  if (s >= 0.995) return 'all';
  if (s < 0.1) return `${(s * 100).toFixed(1)}%`;
  return `${Math.round(s * 100)}%`;
}

/* "About 2,180". The total is an estimate, so it is never printed to the unit. */
function roughly(n) {
  if (n === null || n === undefined) return '—';
  const step = n >= 1000 ? 10 : 1;
  return (Math.round(n / step) * step).toLocaleString('en-US');
}

/* The district table's columns: what each shows and what it sorts by. A value
 * that is not measured sorts as null, so it falls to the bottom. */
function districtColumns() {
  const rep = p => p.coverage === 'reporting';
  const cols = [
    { key: 'name', label: 'Municipality', value: p => p.name,
      cell: p => `<td><a href="index.html#place=${p.geoid}">${esc(p.name)}</a>${ahpaaTag(p)}</td>` },
    { key: 'share', label: 'Share of its land in district', value: p => p.share,
      cell: p => `<td class="num">${sharePct(p.share)}</td>` },
    { key: 'pct_growth', label: `% growth since ${META.metric_start}`,
      value: p => rep(p) ? p.pct_growth : null,
      cell: p => `<td class="num">${rep(p) ? fmtPct(p.pct_growth) : '<span class="muted">no permit office</span>'}</td>` },
    { key: 'units', label: `Units since ${META.metric_start}`,
      value: p => rep(p) ? p.units_total_2010 : null,
      cell: p => `<td class="num">${rep(p) ? fmtInt(p.units_total_2010) : '—'}</td>` },
    { key: 'mf5p', label: '5+ unit units', value: p => rep(p) ? p.mf5p_total : null,
      cell: p => `<td class="num">${rep(p) ? fmtInt(p.mf5p_total) : '—'}</td>` },
    { key: 'mf_flag', label: `Multifamily since ${META.metric_start}`, value: mfRank,
      cell: p => `<td class="mf-flag">${mfLabel(p)}</td>` },
    { key: 'net', label: 'Census count change 2010–20', value: p => p.net_change,
      cell: p => `<td class="num">${fmtSigned(p.net_change)}</td>` },
    { key: 'gap', label: '2020 count vs permits', value: p => p.built_gap,
      cell: p => `<td class="num">${fmtSigned(p.built_gap)}</td>` }
  ];
  if (META.ahpaa.enabled) {
    cols.push({ key: 'afford', label: `Affordable share (${esc(META.ahpaa.as_of)})`,
      value: p => p.affordable_share,
      cell: p => `<td class="num">${fmtShare(p.affordable_share)}</td>` });
  }
  return cols;
}

function renderPanel() {
  const empty = document.getElementById('district-empty');
  const body = document.getElementById('district-body');
  if (!view.district) {
    empty.hidden = false; body.hidden = true;
    if (typeof prepareSheet === 'function') prepareSheet();
    return;
  }
  const ch = view.chamber, d = view.district;
  const row = districtRow(ch, d);
  const m = row.member || {};
  const title = chamberInfo(ch).title;

  const contact = m.vacant
    ? '<p class="member-contact">This seat is listed as vacant.</p>'
    : `<p class="member-contact">
        ${m.email ? `<a href="mailto:${esc(m.email)}">${esc(m.email)}</a>` : 'Email not listed'}
        · ${m.phone ? `<a href="tel:${esc(m.phone.replace(/[^0-9+]/g, ''))}">${esc(m.phone)}</a>` : 'Phone not listed'}
        ${m.url && /^https:\/\//.test(m.url) ? ` · <a href="${esc(m.url)}" rel="noopener">ilga.gov page</a>` : ''}</p>
       ${m.office ? `<p class="member-office">District office: ${esc(m.office)}</p>` : ''}`;

  const cols = districtColumns();
  const sort = view.sort || { key: 'share', dir: 'desc' };
  const col = cols.find(c => c.key === sort.key) || cols[1];
  const places = row.places.slice();
  if (view.sort) {
    places.sort((a, b) => {
      const x = col.value(a), y = col.value(b);
      /* A blank sorts last in either direction, never as the lowest rank. */
      const xm = x === null || x === undefined, ym = y === null || y === undefined;
      if (xm || ym) return xm && ym ? 0 : xm ? 1 : -1;
      const c = typeof x === 'string' ? x.localeCompare(y) : x - y;
      return sort.dir === 'asc' ? c : -c;
    });
  }
  const rows = places.map(p => `<tr data-geoid="${esc(p.geoid)}"${p.geoid === view.place ? ' aria-selected="true"' : ''}>
      ${cols.map(c => c.cell(p)).join('')}
    </tr>`).join('');
  const head = cols.map(c => {
    const on = c.key === sort.key;
    return `<th scope="col"${on ? ` aria-sort="${sort.dir === 'asc' ? 'ascending' : 'descending'}"` : ''}>`
      + `<button type="button" class="th-sort" data-sort="${c.key}">${c.label}</button></th>`;
  }).join('');
  const order = view.sort
    ? `sorted by ${col.label.toLowerCase()}, ${sort.dir === 'asc' ? 'lowest' : 'highest'} first; blanks last`
    : 'largest share first';

  body.innerHTML = `
    <div class="member-card">
      <p class="member-district">${esc(districtLabel(ch, d))}</p>
      <p class="share-line">${copyLinkButton(`${ch}/${d}/`, false)}
        <button type="button" class="copy-link" id="print-sheet-btn">Print one-page sheet</button></p>
      <h2 class="member-name" id="member-name">${m.vacant ? 'Vacant' : `${esc(title)} ${esc(m.name || 'not listed')}`}${
        m.party ? ` <span class="party">(${esc(PARTY_SHORT[m.party] || m.party)})</span>` : ''}</h2>
      ${contact}
      <p class="member-asof">Member details from Open States, retrieved ${esc(DIST.legislators_as_of || 'date unknown')}.</p>
    </div>

    <div class="district-summary">
      <p><b id="district-n">${fmtInt(row.n_places)}</b> municipalities overlap this district.
        <b>${fmtInt(row.n_zero_mf)}</b> of them have permitted no building of 5+ units since ${META.metric_start}.${
        row.n_no_permit_office ? ` ${fmtInt(row.n_no_permit_office)} have no permit office reporting to the Census and are not counted in the estimate below.` : ''}</p>
      <p class="district-est">About <b id="district-est">${roughly(row.est_units_2010)}</b> units permitted
        ${META.metric_start}–${META.ymax} inside this district <span class="est-tag">estimate</span></p>
      <p class="district-est">The census housing count here changed by about
        <b id="district-net">${row.est_net_change < 0 ? '\u2212' : '+'}${roughly(Math.abs(row.est_net_change))}</b>
        units from 2010 to 2020 <span class="est-tag">estimate</span></p>
      <p class="fineprint">Each municipality's permits are counted in proportion to the share of its land inside
        the district, which assumes they are spread evenly across the municipality. Permits filed by the county for
        unincorporated land are not included. The census change is weighted the same way, covers 2010–2020
        only, and counts every home that appeared or disappeared, so it is not a count of homes built.
        Illinois as a whole permitted ${fmtPct(META.il_pct_growth)} of its 2010 housing stock over
        ${META.metric_start}–${META.ymax}, and its housing count rose by ${fmtInt(META.built.il_net_change)} from 2010 to 2020.</p>
    </div>

    <div class="table-scroll">
      <table class="district-table">
        <caption>Municipalities overlapping ${esc(districtLabel(ch, d))}, ${order}. Select a column
          heading to sort. A municipality split between districts appears under each of them.</caption>
        <thead><tr>${head}</tr></thead>
        <tbody id="district-rows">${rows}</tbody>
      </table>
    </div>`;
  empty.hidden = true;
  body.hidden = false;
  if (typeof prepareSheet === 'function') prepareSheet();
}

/* ---------------------------------------------------------------- state */

function selectDistrict(ch, d, fly) {
  view.chamber = ch;
  view.district = d;
  syncControls();
  applyView(fly);
  renderPanel();
  writeHash();
}

/* A municipality opens beside the district table rather than sending the reader
 * back to the municipality map. The row link still points there, so a
 * modified click or "open in new tab" behaves as a link. */
function openPlace(geoid, title) {
  view.place = geoid;
  document.getElementById('district-split').classList.add('has-detail');
  document.getElementById('detail-maplink').href = `index.html#place=${geoid}`;
  document.querySelectorAll('#district-rows tr').forEach(tr => {
    if (tr.dataset.geoid === geoid) tr.setAttribute('aria-selected', 'true');
    else tr.removeAttribute('aria-selected');
  });
  applyView(false);
  writeHash();
  return renderPlaceDetail(geoid, title);
}

function closePlace() {
  view.place = null;
  document.getElementById('detail').hidden = true;
  document.getElementById('district-split').classList.remove('has-detail');
  document.querySelectorAll('#district-rows tr[aria-selected]').forEach(tr => tr.removeAttribute('aria-selected'));
  applyView(false);
  writeHash();
}

function placeTitle(geoid) {
  for (const ch of ['senate', 'house']) {
    for (const r of chamberInfo(ch).districts) {
      const p = r.places.find(x => x.geoid === geoid);
      if (p) return p.name;
    }
  }
  return null;
}

function writeHash() {
  const h = (view.district ? `#${view.chamber}-${view.district}` : `#${view.chamber}`)
    + (view.place ? `&place=${view.place}` : '');
  setAddress(view.district ? `${view.chamber}/${view.district}/` : 'districts.html', h);
}

/* "#senate-28", "#house", "#senate-28&place=1751622", or nothing. Anything else
 * is ignored. */
function readHash() {
  const m = location.hash.match(/^#(senate|house)(?:-(\d{1,3}))?(?:&place=(\d{7}))?$/);
  if (!m) { view.place = null; return; }
  view.chamber = m[1];
  const d = m[2] ? +m[2] : null;
  view.district = d && d >= 1 && d <= chamberInfo(m[1]).n ? d : null;
  view.place = m[3] && placeTitle(m[3]) ? m[3] : null;
}

/* Open or close the detail panel to match view.place after a hash change. */
function syncPlace() {
  if (view.place) openPlace(view.place, placeTitle(view.place));
  else if (!document.getElementById('detail').hidden) closePlace();
}

function syncControls() {
  document.querySelectorAll('[data-chamber]').forEach(b =>
    b.setAttribute('aria-pressed', String(b.dataset.chamber === view.chamber)));
}

function searchOptions() {
  const out = [];
  for (const ch of ['senate', 'house']) {
    const short = ch === 'senate' ? 'Senate' : 'House';
    for (const r of chamberInfo(ch).districts) {
      out.push({ ch, d: r.district, text: `${short} ${r.district} — ${memberLine(ch, r.district)}` });
    }
  }
  return out;
}

function wireControls() {
  document.querySelectorAll('[data-chamber]').forEach(b => b.addEventListener('click', () => {
    if (b.dataset.chamber === view.chamber) return;
    view.chamber = b.dataset.chamber;
    view.district = null;
    syncControls(); applyView(false); renderPanel(); writeHash();
  }));
  const opts = searchOptions();
  const list = document.getElementById('district-options');
  list.innerHTML = opts.map(o => `<option value="${esc(o.text)}"></option>`).join('');
  const input = document.getElementById('district-search');
  const pick = () => {
    const q = input.value.trim().toLowerCase();
    if (!q) return;
    let hit = opts.find(o => o.text.toLowerCase() === q);
    const m = q.match(/^(senate|house|sen\.?|rep\.?)\s*(\d{1,3})$/);
    if (!hit && m) {
      const ch = m[1].startsWith('s') ? 'senate' : 'house';
      hit = opts.find(o => o.ch === ch && o.d === +m[2]);
    }
    if (!hit) hit = opts.find(o => o.text.toLowerCase().includes(q));
    if (hit) selectDistrict(hit.ch, hit.d, true);
  };
  input.addEventListener('change', pick);
  input.addEventListener('keydown', e => { if (e.key === 'Enter') pick(); });
  window.addEventListener('hashchange', () => {
    readHash(); syncControls(); applyView(true); renderPanel(); syncPlace();
  });
  document.getElementById('district-body').addEventListener('click', e => {
    const th = e.target.closest('.th-sort');
    if (th) {
      const k = th.dataset.sort;
      const cur = view.sort || { key: 'share', dir: 'desc' };
      view.sort = cur.key === k ? { key: k, dir: cur.dir === 'asc' ? 'desc' : 'asc' }
        : { key: k, dir: k === 'name' ? 'asc' : 'desc' };
      renderPanel();
      return;
    }
    const a = e.target.closest('#district-rows td:first-child a');
    if (!a || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    e.preventDefault();
    const geoid = a.closest('tr').dataset.geoid;
    openPlace(geoid, placeTitle(geoid) || a.textContent);
  });
  document.getElementById('detail-close').addEventListener('click', closePlace);
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && !document.getElementById('detail').hidden) closePlace();
  });
}

function renderLegend() {
  const stops = divergingStopsAt(META.il_pct_growth);
  const ramp = document.getElementById('legend-ramp');
  ramp.innerHTML = stops.map(([, c]) => `<div class="step" style="background:${c}"></div>`).join('');
  document.getElementById('legend-labels').innerHTML = stops.map(([v], i) =>
    `<span>${i === 0 ? fmtPct(v, 0) : i === 3 ? fmtPct(v, 1) : i === stops.length - 1 ? fmtPct(v, 0) + '+' : ''}</span>`).join('');
  document.getElementById('legend-gap-swatch').style.background = darkMode()
    ? 'repeating-linear-gradient(45deg,#2b2b29 0 3px,#6a6a64 3px 4px)'
    : 'repeating-linear-gradient(45deg,#dfe0dc 0 3px,#a6a7a2 3px 4px)';
  document.querySelectorAll('[data-meta]').forEach(el => {
    const v = META[el.dataset.meta];
    if (v !== undefined && v !== null) el.textContent = v;
  });
  document.getElementById('sourceline').textContent =
    `U.S. Census Building Permits Survey; 2010 Decennial Census; Open States · built ${META.build_date}`;
  document.getElementById('footer-districts').textContent =
    `A municipality is listed under a district when at least ${Math.round(DIST.share_min * 100)}% of its land `
    + `falls inside it, so a municipality split between districts appears under each. District boundaries are the `
    + `Census ${DIST.boundary_vintage} files; members are the current Open States list, retrieved `
    + `${DIST.legislators_as_of || 'on an unknown date'}.`;
}

/* ---------------------------------------------------------------- boot */

const districtsApp = {
  ready: false,
  view: () => ({ ...view }),
  selectDistrict,
  openPlace: geoid => openPlace(geoid, placeTitle(geoid)),
  closePlace,
  place: () => ({
    open: !document.getElementById('detail').hidden,
    geoid: view.place,
    name: document.getElementById('detail-name').textContent,
    percentText: (document.querySelector('#detail .pct') || {}).textContent || ''
  }),
  panel: () => ({
    member: (document.getElementById('member-name') || {}).textContent || '',
    rows: [...document.querySelectorAll('#district-rows tr td:first-child a')].map(a => a.textContent),
    estimate: (document.getElementById('district-est') || {}).textContent || '',
    netChange: (document.getElementById('district-net') || {}).textContent || ''
  })
};
window.districtsApp = districtsApp;

(async function boot() {
  try {
    const get = u => fetch(u).then(r => { if (!r.ok) throw new Error(`${u}: HTTP ${r.status}`); return r.json(); });
    const [meta, dist, places, stateOutline, senate, house] = await Promise.all([
      get('data/meta.json'), get('data/districts.json'), get('data/places.geojson'),
      get('data/state.geojson'), get('data/senate.geojson'), get('data/house.geojson')
    ]);
    META = meta; DIST = dist; OUTLINES = { senate, house }; PLACES = places;
    readHash();
    renderLegend();
    wireControls();
    /* Follow a theme change mid-visit, as the main map does. */
    window.matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => {
      renderLegend();
      if (!layersReady) return;
      map.setPaintProperty('bg', 'background-color', cssVar('--map-bg'));
      map.setPaintProperty('state-fill', 'fill-color', cssVar('--map-land'));
      map.setPaintProperty('places-line', 'line-color', cssVar('--rule-strong'));
      for (const ch of ['senate', 'house']) {
        map.setPaintProperty(`${ch}-casing`, 'line-color', cssVar('--surface'));
        map.setPaintProperty(`${ch}-line`, 'line-color', cssVar('--ink'));
        map.setPaintProperty(`${ch}-selected-casing`, 'line-color', cssVar('--surface'));
        map.setPaintProperty(`${ch}-selected`, 'line-color', cssVar('--ink'));
      }
      map.setPaintProperty('place-selected', 'line-color', placeOutlineColor());
      map.setPaintProperty('district-mask', 'fill-color', cssVar('--surface'));
      if (map.hasImage('hatch')) map.updateImage('hatch', hatchImage());
      addLabelImages(true);
    });
    syncControls();
    buildMap(places, stateOutline);
    renderPanel();
    await new Promise(res => {
      if (layersReady) return res();
      let done = false;
      const finish = () => { if (!done) { done = true; res(); } };
      map.once('load', () => setTimeout(finish, 0));
      setTimeout(finish, 20000);
    });
    applyView(true);
    if (view.place) await openPlace(view.place, placeTitle(view.place));
    writeHash();
    districtsApp.ready = true;
  } catch (err) {
    document.body.insertAdjacentHTML('afterbegin',
      `<div style="padding:16px;background:#fdecea;color:#611a15;font:14px system-ui">
         Could not load the data files: ${esc(err && err.message ? err.message : err)}</div>`);
    throw err;
  }
})();
