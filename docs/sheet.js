/* Illinois Housing Permits Dashboard -- the printable one-page district sheet.
 *
 * "Print one-page sheet" in a district's panel fills #print-sheet and opens the
 * browser's print dialog, where "Save as PDF" makes the PDF. Nothing is fetched:
 * every figure comes from districts.json, places.geojson and meta.json, which
 * the page has already loaded, so the sheet cannot disagree with the map.
 *
 * The municipalities, up to four, are chosen in the build
 * (scripts/district_towns.py), not here: the district office municipality
 * first, then the best of the rest. The reasoning for every district is in
 * docs/data/district_sheet_towns.csv. Reader-facing text says "municipality"
 * (Austin, 2026-10-05); "town" survives only in code names and URLs.
 *
 * Loaded after districts.js; uses its globals (META, DIST, OUTLINES, PLACES).
 */
'use strict';

/* ---------------------------------------------------------------- map */

/* A small static map of the district: towns coloured as on the main map, the
 * district outlined, and the sheet's featured towns ringed in orange and numbered.
 * Drawn as inline SVG so it prints sharply, which a WebGL canvas does not. */
function sheetMap(ch, d, towns) {
  const W = 360, H = 300, PAD = 10;
  const feat = OUTLINES[ch].features.find(f => f.properties.district === d);
  if (!feat) return '';
  const polys = g => g.type === 'Polygon' ? [g.coordinates]
    : g.type === 'MultiPolygon' ? g.coordinates : [];
  const k = Math.cos(40 * Math.PI / 180);
  const bbox = ps => {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    for (const p of ps) for (const [x, y] of p[0]) {
      x0 = Math.min(x0, x * k); x1 = Math.max(x1, x * k);
      y0 = Math.min(y0, -y); y1 = Math.max(y1, -y);
    }
    return [x0, y0, x1, y1];
  };
  const dp = polys(feat.geometry);
  const [bx0, by0, bx1, by1] = bbox(dp);
  const padX = (bx1 - bx0) * 0.06, padY = (by1 - by0) * 0.06;
  const vx0 = bx0 - padX, vy0 = by0 - padY, vx1 = bx1 + padX, vy1 = by1 + padY;
  const s = Math.min((W - 2 * PAD) / (vx1 - vx0), (H - 2 * PAD) / (vy1 - vy0));
  const ox = (W - (vx1 - vx0) * s) / 2 - vx0 * s, oy = (H - (vy1 - vy0) * s) / 2 - vy0 * s;
  const pt = ([x, y]) => `${(x * k * s + ox).toFixed(1)},${(-y * s + oy).toFixed(1)}`;
  const path = ps => ps.map(p => p.map(r => 'M' + r.map(pt).join('L') + 'Z').join('')).join('');

  /* A town's marker sits on its own interior point when that point is inside the
   * district. Chicago's is often not (a district can hold 7% of the city), so a
   * town whose point falls outside is marked at the district's label point. */
  const inside = ([x, y]) => dp.some(p => p.reduce((odd, r) => {
    let c = false;
    for (let i = 0, j = r.length - 1; i < r.length; j = i++) {
      const [xi, yi] = r[i], [xj, yj] = r[j];
      if ((yi > y) !== (yj > y) && x < (xj - xi) * (y - yi) / (yj - yi) + xi) c = !c;
    }
    return odd !== c;
  }, false));
  const row = districtRow(ch, d);
  let fallbackUsed = 0;
  const markAt = p => {
    if (p.lon !== null && p.lat !== null && inside([p.lon, p.lat])) return [p.lon, p.lat];
    if (!row.label) return null;
    const [lx, ly] = row.label;
    return [lx + 0.02 * fallbackUsed++, ly];
  };

  const mid = META.il_pct_growth;
  const featured = new Map(towns.map((t, i) => [t.geoid, i + 1]));
  let fills = '', rings = '', marks = '';
  for (const f of PLACES.features) {
    const ps = polys(f.geometry);
    if (!ps.length) continue;
    const [x0, y0, x1, y1] = bbox(ps);
    if (x1 < vx0 || x0 > vx1 || y1 < vy0 || y0 > vy1) continue;
    const p = f.properties;
    const fill = p.coverage === 'reporting' ? divergingColor(p.pct_growth, mid) : 'url(#sheet-hatch)';
    fills += `<path d="${path(ps)}" fill="${fill}" fill-rule="evenodd" stroke="#b9bab6" stroke-width="0.4"/>`;
    if (featured.has(p.geoid)) {
      rings += `<path d="${path(ps)}" fill="none" stroke="#fff" stroke-width="5" stroke-linejoin="round"/>`
        + `<path d="${path(ps)}" fill="none" stroke="#E87722" stroke-width="2.6" stroke-linejoin="round"/>`;
      const at = markAt(p);
      if (at) {
        const [cx, cy] = pt(at).split(',');
        marks += `<circle cx="${cx}" cy="${cy}" r="9" fill="#004B87" stroke="#fff" stroke-width="1.5"/>`
          + `<text x="${cx}" y="${cy}" dy="3.6" text-anchor="middle" font-size="10.5" font-weight="700" fill="#fff">${featured.get(p.geoid)}</text>`;
      }
    }
  }
  return `<svg class="sheet-map" viewBox="0 0 ${W} ${H}" role="img"
      aria-label="Map of ${esc(districtLabel(ch, d))} with its municipalities colored by housing permitted since ${META.metric_start}">
    <defs>
      <pattern id="sheet-hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">
        <rect width="6" height="6" fill="#dfe0dc"/><line x1="0" y1="0" x2="0" y2="6" stroke="#a6a7a2" stroke-width="1.6"/>
      </pattern>
      <clipPath id="sheet-clip"><rect width="${W}" height="${H}" rx="6"/></clipPath>
    </defs>
    <g clip-path="url(#sheet-clip)">
      <rect width="${W}" height="${H}" fill="#eef0ec"/>
      ${fills}
      <path d="M0,0H${W}V${H}H0Z${path(dp)}" fill="#fff" fill-opacity="0.62" fill-rule="evenodd"/>
      <path d="${path(dp)}" fill="none" stroke="#fff" stroke-width="5" stroke-linejoin="round"/>
      <path d="${path(dp)}" fill="none" stroke="#1E1E1E" stroke-width="2" stroke-linejoin="round"/>
      ${rings}${marks}
    </g>
  </svg>`;
}

/* ---------------------------------------------------------------- blocks */

function sheetTown(t, i) {
  const ms = META.metric_start, ym = META.ymax;
  /* Kept to one line each, with the types two to a row: four blocks in a 2 x 2
   * grid must leave the page 20% spare for an iPhone's larger print. */
  const where = `${fmtPct(t.share * 100, 0)} of its land in district`
    + (t.pop2020 === null ? '' : ` · ${fmtInt(t.pop2020)} people (2020)`);
  let figure;
  if (t.coverage !== 'reporting') {
    figure = `<p class="sheet-big sheet-big-text">No permit data</p>
      <p class="sheet-cap">No permit office here reports to the U.S. Census, so there is no
        permit record to count. That is not the same as no housing being built.</p>`;
  } else if (t.pct_growth === null) {
    figure = `<p class="sheet-big">${fmtInt(t.units_total_2010)} units</p>
      <p class="sheet-cap">permitted ${t.first_metric_year || ms}–${ym}. No 2010 housing count is
        published, so no percentage.</p>`;
  } else {
    figure = `<p class="sheet-big">${fmtPct(t.pct_growth)}</p>
      <p class="sheet-cap">of its 2010 housing stock permitted ${t.first_metric_year || ms}–${ym}
        (${fmtInt(t.units_total_2010)} units). Illinois: <b>${fmtPct(META.il_pct_growth)}</b>.</p>`;
  }
  const cell = k => `<td>${k === 'mf5p' ? '5+ units' : TYPE_LABEL_SHORT[k]}</td><td class="num">${fmtInt(t.units_by_type[k])}</td>`;
  const byType = t.coverage === 'reporting'
    ? `<table class="sheet-types"><tbody>${[0, 2].map(i =>
        `<tr>${cell(TYPE_ORDER[i])}${cell(TYPE_ORDER[i + 1])}</tr>`).join('')}
      </tbody></table>` : '';
  const flag = t.zero_mf3p === true
    ? `<p class="sheet-flag">Nothing above a 2-flat permitted since ${t.first_metric_year || ms}.</p>`
    : t.zero_mf === true
      ? `<p class="sheet-flag">No 5+ unit buildings permitted since ${t.first_metric_year || ms}${
          t.mf34_total ? ` (${fmtInt(t.mf34_total)} units in 3- and 4-flats)` : ''}.</p>`
      : '';
  const ahpaa = t.ahpaa_status
    ? `<p class="sheet-ahpaa">AHPAA: <b>${esc(t.ahpaa_status)}</b> · ${fmtShare(t.affordable_share)} affordable</p>`
    : '<p class="sheet-ahpaa">AHPAA: not scored by IHDA</p>';
  return `<section class="sheet-town">
    <div class="sheet-town-head"><span class="sheet-num">${i + 1}</span><h2>${esc(t.name)}</h2></div>
    <p class="sheet-where">${where}</p>
    ${figure}${byType}${flag}${ahpaa}
  </section>`;
}

function sheetHTML(ch, d) {
  const row = districtRow(ch, d);
  const m = row.member || {};
  const sheet = row.sheet || { towns: [], office: {} };
  const title = chamberInfo(ch).title;
  const who = m.vacant ? 'Seat listed as vacant'
    : `${esc(title)} ${esc(m.name || 'not listed')}${m.party ? ` (${esc(PARTY_SHORT[m.party] || m.party)})` : ''}`;
  const nNe = row.places.filter(p => (p.ahpaa_status || '').toLowerCase() === 'non-exempt').length;
  const link = new URL(`${ch}/${d}/`, document.baseURI).href;
  const ms = META.metric_start, ym = META.ymax;
  const office = sheet.office || {};
  const n = sheet.towns.length;
  const how = n < 2
    ? (n === 1 ? `${esc(sheet.towns[0].name)} is the only municipality in this district.` : '')
      + (n === 1 && sheet.towns[0].name === 'Chicago' ? ' Chicago is shown as one city, never split by ward.' : '')
    : office.status === 'in district'
    ? `Number 1 is where the member's district office is. ${n === 2 ? 'Number 2 is' : 'The others are'} the best of the rest.`
    : `${office.status === 'none listed' ? 'No district office is listed'
        : office.status === 'capitol office' ? 'The only office listed is in Springfield'
          : `The district office, in ${esc(office.city || '')}, is outside the district`}, so these are the ${['', 'one', 'two', 'three', 'four'][n] || n} best municipalities.`;
  return `<div class="sheet">
    <header class="sheet-head">
      <img class="sheet-logo" src="ahil-logo.png" alt="Abundant Housing Illinois">
      <div>
        <p class="sheet-kicker">How much housing has this district permitted?</p>
        <h1>${esc(districtLabel(ch, d))}</h1>
        <p class="sheet-member">${who}</p>
      </div>
    </header>

    <div class="sheet-stats">
      <div><b>${roughly(row.est_units_2010)}</b><span>units permitted ${ms}–${ym} in this district (estimate)</span></div>
      <div><b>${row.est_net_change < 0 ? '−' : '+'}${roughly(Math.abs(row.est_net_change))}</b>
        <span>change in homes counted by the census, 2010–2020 (estimate)</span></div>
      <div><b>${fmtInt(row.n_zero_mf)} of ${fmtInt(row.n_places)}</b><span>municipalities here have permitted no building of 5+ units since ${ms}</span></div>
      ${META.ahpaa.enabled ? `<div><b>${fmtInt(nNe)}</b><span>municipalities here are AHPAA non-exempt (under 10% affordable)</span></div>` : ''}
      <figure class="sheet-qr">
        <img src="${ch}/${d}/qr.svg" alt="QR code linking to this district's page">
        <figcaption>${esc(link.replace(/^https?:\/\//, ''))}</figcaption>
      </figure>
    </div>

    <div class="sheet-body">
      <div class="sheet-mapcol">
        ${sheetMap(ch, d, sheet.towns)}
        <p class="sheet-note">Municipalities colored by housing permitted since ${ms} as a share of their
          2010 stock: orange below Illinois's ${fmtPct(META.il_pct_growth)}, blue above.
          Hatched: no permit office. Numbers mark the municipalities at right.</p>
      </div>
      <div class="sheet-towns">
        ${sheet.towns.map(sheetTown).join('') || '<p>No municipality lies mostly in this district.</p>'}
        <p class="sheet-note">${how} Ranking: municipalities with more than half their land in the
          district and more than 5,000 people first, then by land share × population.</p>
      </div>
    </div>

    <!-- A div, not <footer>: the district page's print rules hide footers. -->
    <div class="sheet-foot">
      <p><b>Permits show what a municipality approved, not every home added.</b> They count new buildings
        only. Conversions, additions and basement apartments never show up, so the real number of
        homes added can be higher. A district's totals count each municipality's permits by the share of
        its land inside the district.</p>
      <p>Sources: ${esc(META.source_line)}; 2020 Decennial Census; Open States (members, retrieved
        ${esc(DIST.legislators_as_of || 'date unknown')})${META.ahpaa.enabled ? `; IHDA ${esc(META.ahpaa.as_of || '')} AHPAA report` : ''}.
        Built ${esc(META.build_date)}. ${esc(link.replace(/^https?:\/\//, ''))}</p>
    </div>
  </div>`;
}

/* ---------------------------------------------------------------- print */

/* The sheet is filled as soon as a district is opened, not when printing starts:
 * a print from the browser's menu or Ctrl+P gives no time to wait for the logo
 * and QR code to load, and an <img> inside a hidden element still loads. */
let preparedFor = null;
function prepareSheet() {
  const key = view.district ? `${view.chamber}-${view.district}` : null;
  if (key === preparedFor) return;
  preparedFor = key;
  document.getElementById('print-sheet').innerHTML =
    key ? sheetHTML(view.chamber, view.district) : '';
}

async function printSheet() {
  if (!view.district) return;
  prepareSheet();
  const el = document.getElementById('print-sheet');
  await Promise.all([...el.querySelectorAll('img')].map(img =>
    img.complete ? null : new Promise(res => { img.onload = img.onerror = res; })));
  document.body.classList.add('print-sheet-on');
  window.print();
}

/* Any print of an open district is the sheet, however it was started. Austin
 * printed from the browser's menu in Edge and Firefox and got the whole page,
 * with the live map printing black or blank. With no district open, the page
 * prints as it always has. */
window.addEventListener('beforeprint', () => {
  if (!view.district) return;
  prepareSheet();
  document.body.classList.add('print-sheet-on');
});
window.addEventListener('afterprint', () => document.body.classList.remove('print-sheet-on'));

document.addEventListener('click', e => {
  if (e.target.closest && e.target.closest('#print-sheet-btn')) printSheet();
});

window.sheetApp = { html: sheetHTML, print: printSheet, prepare: prepareSheet };
