/* Illinois Housing Permits Dashboard -- the municipality detail panel, shared by
 * index.html and districts.html so a town reads identically from either page.
 *
 * Loaded as a classic script after shared.js and before the page's own script.
 * Reads the page's global ``META`` (meta.json); fills ``#detail``,
 * ``#detail-name``, ``#detail-sub`` and ``#detail-body``.
 */
'use strict';

const SHARDS = new Map();     // geoid -> shard JSON, fetched on demand

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

/* Fill the #detail panel for one municipality. ``title`` is shown at once, while
 * the shard is still loading. */
let detailToken = 0;          // a later click wins over a slower earlier fetch

async function renderPlaceDetail(geoid, title) {
  const token = ++detailToken;
  const panel = document.getElementById('detail');
  document.getElementById('detail-name').textContent = title;
  panel.hidden = false;

  let shard;
  try {
    shard = await loadShard(geoid);
  } catch (err) {
    if (token !== detailToken) return;
    document.getElementById('detail-sub').textContent = '';
    document.getElementById('detail-body').innerHTML =
      `<div class="coverage-note">Could not load the detail file for this place: ${err.message}</div>`;
    return;
  }
  if (token !== detailToken) return;
  if (shard.name) document.getElementById('detail-name').textContent = shard.name;

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
      ${ahpaaLine(shard)}
      ${districtsLine(shard)}
      <p class="detail-source">${META.source_line}. Built ${META.build_date}.</p>`;
    return;
  }

  const pctText = shard.pct_growth === null
    ? 'Not available'
    : fmtPct(shard.pct_growth);
  /* "Permitted", never "grew": this is permits against the 2010 stock, and the
   * census line just above it is what the stock actually did. */
  const compare = `Since ${META.metric_start}, Illinois has permitted new housing equal to `
    + `${fmtPct(META.il_pct_growth)} of its 2010 stock, and the U.S. ${fmtPct(META.us_pct_growth)}. `
    + (shard.pct_growth === null
        ? `${shard.short_name} has no 2010 housing count published, so a percentage cannot be computed.`
        : `${shard.short_name} has permitted ${fmtPct(shard.pct_growth)} — ${fmtInt(shard.units_total_2010)} units.`);

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
    ${censusLine(shard)}

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
            <td style="text-align:left"><strong>All types, ${shard.first_metric_year || META.metric_start}–${META.ymax}</strong></td>
            <td class="num"><strong>${fmtInt(shard.units_total_2010)}</strong></td>
            <td class="num"><strong>${fmtPct(shard.pct_growth, 2)}</strong></td>
          </tr>
          <tr><td style="text-align:left">All types, ${shard.first_year_reported || META.ymin}–${META.ymax}</td>
            <td class="num">${fmtInt(shard.units_total_2000)}</td><td class="num">—</td></tr>
        </tbody>
      </table>
    </div>

    ${builtSection(shard)}

    ${ahpaaLine(shard)}

    ${districtsLine(shard)}

    <p class="detail-source">${META.source_line}. Built ${META.build_date}.
      BPS counts units <em>authorized by permit</em>, not units completed, and does not
      subtract demolitions.</p>`;

  wireChartHover(chart.geom);
}

/* The census count, 2010 to 2020, right under the permit headline: permits are
 * what the town approved, the count is what was standing. */
function censusLine(shard) {
  const net = shard.built && shard.built.net_change;
  if (net === null || net === undefined) return '';
  return `<p class="census-line"><b>Census housing count:</b> ${fmtInt(shard.h1_2010)} (2010) →
    ${fmtInt(shard.h1_2020)} (2020), <b class="census-net">${fmtSigned(net)}</b> units.
    <span class="muted">A change in homes standing, not homes built — see below.</span></p>`;
}

/* IHDA's AHPAA determination and affordable housing share. A place IHDA did not
 * score says so; it is never shown as exempt. */
function ahpaaLine(shard) {
  const A = META.ahpaa;
  if (!A || !A.enabled) return '';
  const src = `IHDA ${esc(A.as_of)} AHPAA report, from Census ACS 2017–2021 estimates.`;
  if (!shard.ahpaa_status) {
    return `<p class="detail-ahpaa"><b>Affordable housing share:</b> <span class="muted">not in
      IHDA's ${esc(A.as_of)} AHPAA report for this place.</span></p>`;
  }
  const non = shard.ahpaa_status.toLowerCase() === 'non-exempt';
  return `<p class="detail-ahpaa"><b>Affordable housing share:</b>
    <span class="ahpaa-share">${fmtShare(shard.affordable_share)}</span>
    ${non ? '<span class="ahpaa-tag">AHPAA non-exempt</span>' : '<span class="ahpaa-exempt">AHPAA exempt</span>'}
    <span class="muted">${src} Under ${fmtPct(A.share_threshold * 100, 0)} is the Act's
    non-exempt threshold.</span></p>`;
}

/* Which legislators represent this place, each linking to the district view.
 * A place split between districts lists each, with the share of its land. */
function districtsLine(shard) {
  const D = shard.districts;
  if (!D) return '';
  const party = { Democratic: 'D', Republican: 'R' };
  const part = (ch, label) => (D[ch] || []).map(x => {
    const who = x.name ? ` — ${esc(x.name)}${x.party ? ` (${esc(party[x.party] || x.party)})` : ''}` : '';
    const share = D[ch].length > 1 ? `, ${x.share >= 0.995 ? 'all' : Math.round(x.share * 100) + '%'}` : '';
    return `<a href="districts.html#${ch}-${x.d}">${label} ${x.d}${who}</a>${share ? `<span class="muted">${share}</span>` : ''}`;
  }).join('; ');
  const s = part('senate', 'Senate'), h = part('house', 'House');
  if (!s && !h) return '';
  return `<p class="detail-districts"><b>Legislators:</b> ${[s, h].filter(Boolean).join(' · ')}</p>`;
}

/* Permits vs. what the 2020 Census counted. A diagnostic, not a scorecard: the
 * two sides measure different things, and the caveats ship with the number. */
function builtSection(shard) {
  const B = META.built;
  const b = shard.built || {};
  const [y0, y1] = B.years;
  const ref = `Illinois as a whole: the housing count rose by ${fmtInt(B.il_net_change)} `
    + `while ${fmtInt(B.il_permits)} units were permitted, a difference of `
    + `<b id="built-il-gap">${fmtSigned(B.il_gap)}</b>.`;
  const caveat = `These do not have to match. Demolitions and conversions lower the count but `
    + `not the permit total; not every permitted unit gets built, and one permitted late in `
    + `${y1} may not be standing on April 1, ${y1 + 1}; annexation moves boundaries between `
    + `the two counts; and each count has its own error. A large gap is a question to ask, `
    + `not a verdict.`;
  /* The change in the count is a fact whenever both counts exist, whatever the
   * permit office filed, so it is shown even when the comparison is withheld.
   * The two counts themselves are in the census line under the headline. */
  const countRows = (b.net_change === null || b.net_change === undefined) ? '' : `
          <tr><td>Change in the count, ${y0}–${y1 + 1}</td><td class="num built-net">${fmtSigned(b.net_change)}</td></tr>`;
  let inner;
  if (b.gap === null || b.gap === undefined) {
    const why = {
      months_not_full: `This municipality's permit office did not report all twelve months to `
        + `the Census in ${yearRanges(b.short_years || [])}, so the permit side would be partly `
        + `the Census's own estimate.`,
      no_2010_count: `No 2010 housing count is published for this place.`,
      no_2020_count: `No 2020 housing count is published for this place.`
    }[b.reason] || 'Not available for this place.';
    inner = (countRows ? `<table><tbody>${countRows}</tbody></table>` : '')
      + `<p class="built-none"><strong>No permit comparison.</strong> ${why}</p>`;
  } else {
    const dir = b.gap < 0 ? 'less than' : b.gap > 0 ? 'more than' : 'exactly';
    inner = `<table>
        <tbody>${countRows}
          <tr><td>Units permitted ${y0}–${y1}</td><td class="num built-permits">${fmtInt(b.permits)}</td></tr>
        </tbody>
      </table>
      <p class="built-read">The count changed by <span class="built-gap">${dir === 'exactly' ? 'exactly' : fmtInt(Math.abs(b.gap)) + ' units ' + dir}</span>
        the number of units permitted.</p>`;
    const share = `${fmtPct(Math.abs(b.gap_pct), 1)} of the ${y0} housing stock`;
    if (b.flag === 'rose') {
      inner += `<div class="flag"><strong>The count grew far more than permits explain</strong> — by
        ${share}. No permit in the Census record accounts for that housing. The usual causes:
        conversions, additions and subdivided buildings, which the permit survey never counts;
        annexed land that already had homes on it; or permits the office did not report. For how
        much housing exists here, trust the count.</div>`;
    } else if (b.flag === 'fell') {
      inner += `<div class="flag"><strong>The count grew far less than permits suggest</strong> — by
        ${share}. Usually demolitions, or homes lost or left empty, which permits never subtract.
        For how much housing exists here, trust the count.</div>`;
    }
  }
  return `<div class="built detail-table" id="built">
      <p class="chart-title">Permits vs. what the ${y1 + 1} Census counted</p>
      ${inner}
      <p class="built-note">${ref} ${caveat}${
        b.gap === null || b.gap === undefined ? ''
          : ` Housing counts: ${y0} Decennial Census SF1 H1 and ${y1 + 1} Decennial Census DHC H1.`}</p>
    </div>`;
}
