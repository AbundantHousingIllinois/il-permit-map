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

const view = { chamber: 'senate', district: null };

let META = null;
let DIST = null;            // districts.json
let OUTLINES = {};          // chamber -> geojson
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
    for (const ch of ['senate', 'house']) {
      /* A near-transparent fill so the whole district, not just its edge, takes
       * a tap and a hover. */
      map.addLayer({
        id: `${ch}-hit`, type: 'fill', source: ch,
        layout: { visibility: ch === view.chamber ? 'visible' : 'none' },
        paint: { 'fill-color': '#000', 'fill-opacity': 0.01 }
      });
      map.addLayer({
        id: `${ch}-line`, type: 'line', source: ch,
        layout: { visibility: ch === view.chamber ? 'visible' : 'none' },
        paint: { 'line-color': cssVar('--ink-secondary'), 'line-width': 1.1, 'line-opacity': 0.85 }
      });
      map.addLayer({
        id: `${ch}-selected`, type: 'line', source: ch,
        filter: ['==', ['get', 'district'], -1],
        layout: { visibility: ch === view.chamber ? 'visible' : 'none' },
        paint: { 'line-color': '#E87722', 'line-width': 3 }
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

function applyView(fly) {
  if (!layersReady) return;
  for (const ch of ['senate', 'house']) {
    const vis = ch === view.chamber ? 'visible' : 'none';
    for (const suffix of ['hit', 'line', 'selected', 'labels']) map.setLayoutProperty(`${ch}-${suffix}`, 'visibility', vis);
    /* The chosen district's number turns orange and always shows. */
    const sel = ch === view.chamber && view.district ? view.district : -1;
    map.setLayoutProperty(`${ch}-labels`, 'icon-image', ['case',
      ['==', ['get', 'district'], sel],
      ['concat', `lbl-${ch}-`, ['to-string', ['get', 'district']], '-sel'],
      ['concat', `lbl-${ch}-`, ['to-string', ['get', 'district']]]]);
    map.setLayoutProperty(`${ch}-labels`, 'symbol-sort-key', ['case',
      ['==', ['get', 'district'], sel], -1e15, ['-', 0, ['get', 'aland']]]);
    map.setFilter(`${ch}-selected`, ['==', ['get', 'district'],
      ch === view.chamber && view.district ? view.district : -1]);
  }
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

function renderPanel() {
  const empty = document.getElementById('district-empty');
  const body = document.getElementById('district-body');
  if (!view.district) { empty.hidden = false; body.hidden = true; return; }
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

  const places = row.places;
  const rows = places.map(p => {
    const measured = p.coverage === 'reporting';
    return `<tr>
      <td><a href="index.html#place=${p.geoid}">${esc(p.name)}</a>${p.zero_mf === true ? '<span class="zero-mf-tag">no 5+</span>' : ''}</td>
      <td class="num">${sharePct(p.share)}</td>
      <td class="num">${measured ? fmtPct(p.pct_growth) : '<span class="muted">no permit office</span>'}</td>
      <td class="num">${measured ? fmtInt(p.units_total_2010) : '—'}</td>
      <td class="num">${measured ? fmtInt(p.mf5p_total) : '—'}</td>
      <td class="num">${fmtSigned(p.net_change)}</td>
      <td class="num">${fmtSigned(p.built_gap)}</td>
    </tr>`;
  }).join('');

  body.innerHTML = `
    <div class="member-card">
      <p class="member-district">${esc(districtLabel(ch, d))}</p>
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
        the district, which assumes they are spread evenly across the town. Permits filed by the county for
        unincorporated land are not included. The census change is weighted the same way, covers 2010–2020
        only, and counts every home that appeared or disappeared, so it is not a count of homes built.
        Illinois as a whole permitted ${fmtPct(META.il_pct_growth)} of its 2010 housing stock over
        ${META.metric_start}–${META.ymax}, and its housing count rose by ${fmtInt(META.built.il_net_change)} from 2010 to 2020.</p>
    </div>

    <div class="table-scroll">
      <table class="district-table">
        <caption>Municipalities overlapping ${esc(districtLabel(ch, d))}, largest share first. A municipality
          split between districts appears under each of them.</caption>
        <thead><tr>
          <th scope="col">Municipality</th><th scope="col">Share of its land in district</th>
          <th scope="col">% growth since ${META.metric_start}</th><th scope="col">Units since ${META.metric_start}</th>
          <th scope="col">5+ unit units</th><th scope="col">Census count change 2010–20</th>
          <th scope="col">2020 count vs permits</th>
        </tr></thead>
        <tbody id="district-rows">${rows}</tbody>
      </table>
    </div>`;
  empty.hidden = true;
  body.hidden = false;
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

function writeHash() {
  const h = view.district ? `#${view.chamber}-${view.district}` : `#${view.chamber}`;
  if (location.hash !== h) history.replaceState(null, '', h);
}

/* "#senate-28", "#house", or nothing. Anything else is ignored. */
function readHash() {
  const m = location.hash.match(/^#(senate|house)(?:-(\d{1,3}))?$/);
  if (!m) return;
  view.chamber = m[1];
  const d = m[2] ? +m[2] : null;
  view.district = d && d >= 1 && d <= chamberInfo(m[1]).n ? d : null;
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
  window.addEventListener('hashchange', () => { readHash(); syncControls(); applyView(true); renderPanel(); });
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
    + `falls inside it, so a town split between districts appears under each. District boundaries are the `
    + `Census ${DIST.boundary_vintage} files; members are the current Open States list, retrieved `
    + `${DIST.legislators_as_of || 'on an unknown date'}.`;
}

/* ---------------------------------------------------------------- boot */

const districtsApp = {
  ready: false,
  view: () => ({ ...view }),
  selectDistrict,
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
    META = meta; DIST = dist; OUTLINES = { senate, house };
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
        map.setPaintProperty(`${ch}-line`, 'line-color', cssVar('--ink-secondary'));
      }
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
    writeHash();
    districtsApp.ready = true;
  } catch (err) {
    document.body.insertAdjacentHTML('afterbegin',
      `<div style="padding:16px;background:#fdecea;color:#611a15;font:14px system-ui">
         Could not load the data files: ${esc(err && err.message ? err.message : err)}</div>`);
    throw err;
  }
})();
