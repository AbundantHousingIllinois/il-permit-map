/* Illinois Housing Permits Dashboard -- shared by index.html and districts.html.
 *
 * The palette, the diverging ladder, the number formats and the coverage-gap
 * hatch are defined once here, so a town is coloured identically on the map and
 * on the legislator view. Loaded as a classic script before each page's own.
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

/* The diverging ladder around a midpoint m, the Illinois reference rate. */
function divergingStopsAt(m) {
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

/* ---------------------------------------------------------------- formats */

/* Text from a third-party file (legislator names, offices) goes through this
 * before it is written into HTML. */
function esc(s) {
  return String(s).replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

const fmtInt = n => (n === null || n === undefined) ? '—' : n.toLocaleString('en-US');
/* Signed, with a true minus sign: a difference has a direction, and "-733" set in
 * a hyphen reads as a dash. */
const fmtSigned = n => (n === null || n === undefined) ? '—'
  : (n > 0 ? '+' : n < 0 ? '\u2212' : '') + Math.abs(n).toLocaleString('en-US');
/* [2010, 2011, 2012, 2015] -> "2010–2012 and 2015". Mirrors build.py's year_ranges. */
function yearRanges(years) {
  const runs = [];
  for (const y of years) {
    const last = runs[runs.length - 1];
    if (last && y === last[1] + 1) last[1] = y; else runs.push([y, y]);
  }
  const parts = runs.map(([a, b]) => a === b ? String(a) : `${a}–${b}`);
  return parts.length <= 1 ? (parts[0] || '')
    : parts.slice(0, -1).join(', ') + ' and ' + parts[parts.length - 1];
}
const fmtPct = (n, d) => (n === null || n === undefined)
  ? '—' : n.toLocaleString('en-US', { minimumFractionDigits: d === undefined ? 1 : d, maximumFractionDigits: d === undefined ? 1 : d }) + '%';

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
