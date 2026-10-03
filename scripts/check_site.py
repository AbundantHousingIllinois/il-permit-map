#!/usr/bin/env python3
"""SPEC.md §7 browser checks.  Exit 0 only if every check passes.

Serves the built ``docs/`` over HTTP (the page fetches JSON shards, which
``file://`` will not allow) and drives it with headless Chromium.

Readable failure is a requirement: a missing build, a missing browser binary or
a page that never finishes loading is reported as a FAIL with its reason rather
than a traceback.
"""

from __future__ import annotations

import functools
import http.server
import json
import re
import socket
import socketserver
import subprocess
import sys
import time
import threading
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bps_layout as L

MIN_FEATURES = 1000          # SPEC.md §7 site check 2
# Headless Chromium has no GPU, so MapLibre's WebGL context comes from
# SwiftShader. Without this flag Chromium refuses the context and the page logs a
# WebGL error, which would fail site check 1 for a reason that has nothing to do
# with the site.
CHROMIUM_ARGS = ["--enable-unsafe-swiftshader", "--use-gl=angle",
                 "--use-angle=swiftshader"]
READY_TIMEOUT_MS = 45_000
W = 78

# The page's one external dependency is the pinned MapLibre build. Headless
# Chromium on a machine behind a TLS-inspecting proxy cannot validate that
# request even when everything else can, and the whole run then dies on a
# 45-second timeout that says nothing useful. So the pinned URLs are fetched
# here -- through Python, which does read the machine's CA configuration --
# cached, and served to the browser from the cache. The page itself is
# unchanged and still references the CDN; what is being removed is the browser
# checks' dependence on the *test machine's* ability to reach it. Which path
# was used is printed either way, and a URL that 404s still fails the run.
VENDOR_DIR = L.ROOT / "data" / "raw" / "vendor"
CDN_HOST_PREFIX = "https://cdnjs.cloudflare.com/"


class Report:
    def __init__(self) -> None:
        self.results: list[tuple[str, str, bool]] = []

    def out(self, text: str = "") -> None:
        print(text, flush=True)

    def head(self, num, title) -> None:
        self.out("")
        self.out("-" * W)
        self.out(f"Site check {num}: {title}")
        self.out("-" * W)

    def run(self, num, title, fn) -> bool:
        self.head(num, title)
        try:
            ok = bool(fn())
        except Exception:
            self.out("  ERROR while running this check:")
            for line in traceback.format_exc().splitlines():
                self.out(f"    {line}")
            ok = False
        self.results.append((num, title, ok))
        self.out(f"  => {'PASS' if ok else 'FAIL'}")
        return ok


R = Report()


# --------------------------------------------------------------------------
# Static server
# --------------------------------------------------------------------------

class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):   # keep the report readable
        pass


def serve(directory: Path):
    handler = functools.partial(QuietHandler, directory=str(directory))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    httpd = socketserver.ThreadingTCPServer(("127.0.0.1", port), handler)
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{port}"


# --------------------------------------------------------------------------

def preflight() -> tuple[bool, dict]:
    """Confirm the build exists before spending time on a browser."""
    R.out("=" * W)
    R.out("SPEC.md §7 -- browser checks")
    R.out("=" * W)
    needed = [
        L.DOCS / "index.html",
        L.DOCS / "app.js",
        L.DOCS / "style.css",
        L.DOCS_DATA / "places.geojson",
        L.DOCS_DATA / "state.geojson",
        L.DOCS / "districts.html",
        L.DOCS / "districts.js",
        L.DOCS / "shared.js",
        L.DOCS_DATA / "districts.json",
        L.DOCS_DATA / "meta.json",
    ]
    missing = [p for p in needed if not p.exists()]
    for p in needed:
        R.out(f"  {'ok ' if p.exists() else 'MISSING'} {L.rel(p)}")
    if missing:
        R.out("")
        R.out("Cannot run the browser checks: the site is not built.")
        R.out("Run `uv run scripts/build.py` first.")
        return False, {}
    meta = json.loads((L.DOCS_DATA / "meta.json").read_text(encoding="utf-8"))
    gj = json.loads((L.DOCS_DATA / "places.geojson").read_text(encoding="utf-8"))
    return True, {"meta": meta, "geojson": gj}


def pick_click_target(gj: dict) -> dict | None:
    """Choose a place from the BUILT DATA (SPEC.md forbids hardcoding a FIPS code).

    Must be a `reporting` place with a real percent, a real unit count and a
    population at or above the default filter threshold, otherwise the default
    view dims it and a click is not a fair test.
    """
    cands = []
    for f in gj.get("features", []):
        p = f.get("properties") or {}
        if p.get("coverage") != "reporting":
            continue
        if p.get("pct_growth") is None or p.get("units_total_2010") is None:
            continue
        if (p.get("pop2020") or 0) < 5000:
            continue
        if p.get("lon") is None or p.get("lat") is None:
            continue
        cands.append(p)
    if not cands:
        return None
    # Largest population: the biggest polygon-at-zoom target, so the click is
    # least likely to land on a neighbour.
    cands.sort(key=lambda p: -(p.get("pop2020") or 0))
    return cands[0]


DETAIL_TIMEOUT_MS = 20_000


def wait_for_detail(page) -> bool:
    """Wait until the detail panel has finished rendering its metrics.

    Returns False on timeout and lets the caller report what it actually found,
    so a panel that never renders is still a failure.
    """
    try:
        page.wait_for_function(
            "() => { const d = window.app && window.app.detail && window.app.detail();"
            " return !!(d && d.open && d.name && d.percentText); }",
            timeout=DETAIL_TIMEOUT_MS,
        )
        return True
    except Exception:
        R.out(f"  (detail panel did not finish rendering within "
              f"{DETAIL_TIMEOUT_MS // 1000}s)")
        return False


TRANSIENT = ("connection closed", "connection.init", "target closed",
             "browser has been closed")


def _is_transient(exc: Exception) -> bool:
    s = str(exc).lower()
    return any(m in s for m in TRANSIENT)


def ensure_browser(attempts: int = 3):
    """Install the Chromium build Playwright needs, if it is not there yet.

    The Playwright driver occasionally drops its connection on start-up on this
    machine; that is an environment flake, not a fact about the site, so the
    probe is retried before concluding anything.
    """
    from playwright.sync_api import sync_playwright
    last = None
    for i in range(attempts):
        try:
            with sync_playwright() as pw:
                b = pw.chromium.launch(args=CHROMIUM_ARGS)
                b.close()
            return True
        except Exception as exc:
            last = exc
            if _is_transient(exc) and i + 1 < attempts:
                R.out(f"  Chromium launch attempt {i + 1} failed transiently "
                      f"({exc}); retrying.")
                time.sleep(3)
                continue
            break
    exc = last
    if exc is not None:
        if "Executable doesn" not in str(exc) and "install" not in str(exc).lower():
            R.out(f"  Could not launch Chromium: {exc}")
            return False
    R.out("  Chromium not present; running `playwright install chromium` ...")
    proc = subprocess.run(
        [sys.executable, "-m", "playwright", "install", "chromium"],
        capture_output=True, text=True,
    )
    if proc.returncode != 0:
        R.out("  playwright install failed:")
        for line in (proc.stdout + proc.stderr).splitlines()[-15:]:
            R.out(f"    {line}")
        return False
    return True


def cdn_urls_in_page() -> list[str]:
    """Every external asset docs/index.html references, read from the file.

    Read rather than hard-coded so this can never drift from the page: a version
    bump in index.html is picked up here without anyone remembering to.
    """
    import re
    html = (L.DOCS / "index.html").read_text(encoding="utf-8")
    # <script src> and <link href> only. An <a href> to abundanthousingillinois.org
    # is a link the reader may follow, not an asset the page loads, and mirroring
    # it would both waste a fetch and intercept the click.
    urls = re.findall(r'<script[^>]+src="(https://[^"]+)"', html)
    urls += re.findall(r'<link[^>]+href="(https://[^"]+)"', html)
    return sorted(set(urls))


CONTENT_TYPES = {".js": "application/javascript", ".css": "text/css"}


def read_midpoint(pg):
    """The rendered Illinois midpoint, as a number.

    Read from its own element. Scraping the first number out of the sentence
    stopped working the moment the sentence could say "3-4 unit", which is
    exactly the case this has to measure.
    """
    return pg.evaluate(
        "(() => { const el = document.querySelector('#legend-midpoint-value');"
        " const t = (el || document.querySelector('#legend-midpoint')).innerText;"
        " const m = t.match(/-?\\d+(?:\\.\\d+)?/); "
        " return m ? parseFloat(m[0]) : null; })()")


def mirror_cdn(urls: list[str]) -> tuple[dict[str, bytes], list[str]]:
    """Fetch each URL once into data/raw/vendor/ and return {url: bytes}."""
    import requests

    VENDOR_DIR.mkdir(parents=True, exist_ok=True)
    got: dict[str, bytes] = {}
    notes: list[str] = []
    for url in urls:
        cache = VENDOR_DIR / url.rsplit("/", 1)[-1]
        try:
            resp = requests.get(url, timeout=60)
            resp.raise_for_status()
            cache.write_bytes(resp.content)
            got[url] = resp.content
            notes.append(f"  fetched {url}  ({len(resp.content):,} bytes)")
        except Exception as exc:
            if cache.exists():
                got[url] = cache.read_bytes()
                notes.append(f"  {url}\n    unreachable now ({exc.__class__.__name__}); "
                             f"using the cached copy at {L.rel(cache)}")
            else:
                notes.append(f"  {url}\n    FAILED and no cached copy: {exc}")
    return got, notes


def run_checks(sync_playwright, base, built, target, console_errors):
    """Drive the built site once.  Raises on a transient driver failure so
    main() can retry before any check has recorded a result."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=CHROMIUM_ARGS)
        # One context for the whole run: the asset routes below are registered on
        # it, so check 7's second page inherits them instead of going to the CDN
        # on its own and timing out.
        context = browser.new_context(viewport={"width": 420, "height": 900})
        page = context.new_page()

        page.on("console", lambda m: console_errors.append(f"console.{m.type}: {m.text}")
                if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(f"pageerror: {e}"))
        page.on("requestfailed",
                lambda r: console_errors.append(f"requestfailed: {r.url} {r.failure}"))

        urls = cdn_urls_in_page()
        R.out("")
        R.out(f"  External assets referenced by docs/index.html: {len(urls)}")
        mirrored, notes = mirror_cdn(urls)
        for line in notes:
            R.out(line)
        missing = [u for u in urls if u not in mirrored]
        if missing:
            R.out("  Could not obtain: " + ", ".join(missing))
            R.out("  Letting the browser request those directly instead.")
        for url, body in mirrored.items():
            ext = "." + url.rsplit(".", 1)[-1]
            context.route(url, functools.partial(
                lambda route, _b, _t: route.fulfill(
                    status=200, body=_b, content_type=_t),
                _b=body, _t=CONTENT_TYPES.get(ext, "application/octet-stream")))
        if mirrored:
            R.out(f"  Serving {len(mirrored)} of them to the browser from the local "
                  "cache; the page is unmodified.")

        page.goto(f"{base}/index.html", wait_until="load")
        try:
            page.wait_for_function("window.app && window.app.ready === true",
                                   timeout=READY_TIMEOUT_MS)
        except Exception as exc:
            R.out("")
            R.out(f"  The page never reached app.ready within "
                  f"{READY_TIMEOUT_MS / 1000:.0f}s: {exc.__class__.__name__}")
            for e in console_errors[:20]:
                R.out(f"    {e}")
            raise
        page.wait_for_timeout(1200)   # let the first paint settle

        # Captured now, before any check moves the camera, for check 10.
        opening = page.evaluate("""() => {
            const m = window.map, b = m.getBounds();
            return {
                layers: m.getStyle().layers.map(l => l.id),
                view: [[b.getWest(), b.getSouth()], [b.getEast(), b.getNorth()]],
                stateRendered: m.queryRenderedFeatures({ layers: ['state-fill'] }).length,
                landSwatch: getComputedStyle(
                    document.getElementById('legend-land-swatch')).backgroundColor,
                gapSwatch: getComputedStyle(
                    document.getElementById('legend-gap-swatch')).backgroundImage
            };
        }""")

        # ---------------- 1 ----------------
        def s1():
            R.out(f"  Console errors, page errors and failed requests: {len(console_errors)}")
            for e in console_errors[:20]:
                R.out(f"    {e}")
            if not console_errors:
                R.out("  None.")
            return not console_errors
        R.run(1, "The page loads with zero console errors", s1)

        # ---------------- 2 ----------------
        def s2():
            canvas = page.evaluate(
                "(() => { const c = document.querySelector('.maplibregl-canvas');"
                " return c ? {w: c.clientWidth, h: c.clientHeight} : null; })()"
            )
            n = page.evaluate("window.app.featureCount()")
            R.out(f"  Map canvas: {canvas}")
            R.out(f"  Polygon features in the source data the page loaded: {n:,}")
            R.out(f"  Required: >= {MIN_FEATURES:,}")
            return bool(canvas) and canvas["w"] > 0 and canvas["h"] > 0 and n >= MIN_FEATURES
        R.run(2, f"Map canvas renders and >= {MIN_FEATURES:,} polygon features are loaded", s2)

        # ---------------- 3 ----------------
        def s3():
            txt = page.inner_text("#legend-midpoint")
            val = read_midpoint(page)
            expected = built["meta"].get("il_pct_growth")
            R.out(f"  Legend midpoint element text : {txt!r}")
            R.out(f"  Numeric value parsed from it : {val!r}")
            R.out(f"  meta.json il_pct_growth      : {expected!r}")
            if val is None:
                R.out("  No number in the legend midpoint.")
                return False
            close = abs(val - float(expected)) <= 0.051
            R.out(f"  Matches meta.json to one decimal place: {close}")
            return close
        R.run(3, "The legend displays a numeric Illinois midpoint value", s3)

        # ---------------- 4 ----------------
        def s4():
            geoid = target["geoid"]
            # Centre the map on this place's interior point, then issue a real
            # click at that pixel. The geoid is read from the built data above.
            page.evaluate(
                "([lon, lat]) => { window.map.jumpTo({center: [lon, lat], zoom: 11}); }",
                [target["lon"], target["lat"]],
            )
            # A reader scrolls the map into view before tapping it. Without this the
            # click can land below a phone-height viewport once the header grows
            # (the top-of-page caveat pushed Chicago to y=931 of 900).
            page.evaluate("() => document.getElementById('map')"
                          ".scrollIntoView({block: 'center'})")
            page.wait_for_timeout(1500)
            box = page.evaluate(
                "(() => { const c = document.querySelector('.maplibregl-canvas');"
                " const r = c.getBoundingClientRect();"
                " const p = window.map.project(window.map.getCenter());"
                " return {x: r.left + p.x, y: r.top + p.y}; })()"
            )
            R.out(f"  Clicking {geoid} ({target.get('name')}) at viewport {box}")
            page.mouse.click(box["x"], box["y"])
            # The panel loads its shard over HTTP, so wait for it to finish
            # rendering rather than guessing at a sleep. A panel that never
            # populates still fails: this only bounds how long we wait.
            wait_for_detail(page)
            got = page.evaluate("window.app.detail()")
            R.out(f"  Detail panel open      : {got.get('open')}")
            R.out(f"  Selected geoid         : {got.get('geoid')!r}")
            R.out(f"  Name shown             : {got.get('name')!r}")
            R.out(f"  Percent text shown     : {got.get('percentText')!r}")
            R.out(f"  Unit-count text shown  : {got.get('unitsText')!r}")
            checks = {
                "panel is open": bool(got.get("open")),
                "selected geoid is the clicked place": got.get("geoid") == geoid,
                "name is non-empty": bool((got.get("name") or "").strip()),
                "a percent is displayed": "%" in (got.get("percentText") or ""),
                "an absolute unit count is displayed":
                    any(ch.isdigit() for ch in (got.get("unitsText") or "")),
            }
            for k, v in checks.items():
                R.out(f"    {'ok  ' if v else 'FAIL'} {k}")
            return all(checks.values())
        R.run(4, "Clicking a place from the built data opens the detail panel with "
                 "name, percent and unit count", s4)

        # ---------------- 5 ----------------
        def s5():
            page.evaluate("window.app.closeDetail()")
            before = page.inner_text("#legend")
            page.click("[data-metric='units_total']")
            page.wait_for_timeout(800)
            after = page.inner_text("#legend")
            R.out("  Legend before (percent growth):")
            for line in before.splitlines():
                R.out(f"    {line}")
            R.out("  Legend after (total units):")
            for line in after.splitlines():
                R.out(f"    {line}")
            changed = before.strip() != after.strip()
            R.out(f"  Legend text changed: {changed}")
            page.click("[data-metric='pct_growth']")
            page.wait_for_timeout(500)
            return changed
        R.run(5, 'Toggling to "Total units" changes the rendered legend', s5)

        # ---------------- 6 ----------------
        def s6():
            before = page.evaluate("window.app.highlightedCount()")
            page.click("#toggle-zero-mf")
            page.wait_for_timeout(800)
            after = page.evaluate("window.app.highlightedCount()")
            on = page.evaluate("window.app.state().zeroMf")
            R.out(f"  Highlighted features before : {before:,}")
            R.out(f"  'No 5+ unit buildings' switch on : {on}")
            R.out(f"  Highlighted features after  : {after:,}")
            page.click("#toggle-zero-mf")
            page.wait_for_timeout(400)
            R.out(f"  Reduced: {after < before}   (and still non-empty: {after > 0})")
            return bool(on) and after < before
        R.run(6, '"No 5+ unit buildings" reduces the count of highlighted features', s6)

        # ---------------- 7 ----------------
        def s7():
            geoid = target["geoid"]
            page.evaluate("(g) => window.app.selectPlace(g)", geoid)
            page.wait_for_timeout(700)
            h = page.evaluate("location.hash")
            R.out(f"  Hash produced by selecting {geoid}: {h}")
            if geoid not in h:
                R.out("  The hash does not encode the selected place.")
                return False
            p2 = context.new_page()
            errs: list[str] = []
            p2.on("pageerror", lambda e: errs.append(str(e)))
            p2.goto(f"{base}/index.html{h}", wait_until="load")
            p2.wait_for_function("window.app && window.app.ready === true",
                                 timeout=READY_TIMEOUT_MS)
            wait_for_detail(p2)
            got = p2.evaluate("window.app.detail()")
            st = p2.evaluate("window.app.state()")
            R.out(f"  Fresh load with that hash -> selected {got.get('geoid')!r}, "
                  f"panel open {got.get('open')}, name {got.get('name')!r}")
            R.out(f"  Restored control state: {st}")
            if errs:
                for e in errs:
                    R.out(f"    pageerror: {e}")
            p2.close()
            return (got.get("geoid") == geoid and bool(got.get("open"))
                    and bool((got.get("name") or "").strip()) and not errs)
        R.run(7, "A URL hash with a selected place restores that selection on load", s7)

        # ---------------- 8 (added, disclosed) ----------------
        def s8():
            R.out("  Not in SPEC.md §7. The map's neutral colour break is 'the Illinois")
            R.out("  average', and with the structure-type filter on that has to be")
            R.out("  Illinois's average FOR THAT TYPE. It was pinned to the all-types")
            R.out("  figure, so filtering to 5+ unit compared every town to the wrong")
            R.out("  number. This asserts the rendered midpoint follows the filter.")
            page.evaluate("() => { location.hash = '#metric=pct_growth&type=all&pop=5000'; }")
            page.wait_for_timeout(500)
            ok = True
            for t in ["all", "sf", "du", "mf34", "mf5p"]:
                page.evaluate("(t) => { location.hash = "
                              "`#metric=pct_growth&type=${t}&pop=5000`; }", t)
                page.wait_for_timeout(350)
                val = read_midpoint(page)
                want = (built["meta"]["il_pct_growth"] if t == "all"
                        else built["meta"]["il_pct_growth_by_type"][t])
                close = val is not None and abs(val - float(want)) <= 0.011
                R.out(f"    type={t:<5} legend shows {val!r}   meta says {want!r}   "
                      f"{'ok' if close else 'MISMATCH'}")
                ok = ok and close
            distinct = page.evaluate(
                "() => new Set(Object.values(window.app.meta().units_stops)"
                ".map(v => v.join(','))).size")
            R.out(f"    distinct total-units legend ladders across the types: {distinct}")
            if distinct < 2:
                R.out("    Every type shares one ladder, which is the flattened ramp.")
                ok = False
            page.evaluate("() => { location.hash = "
                          "'#metric=pct_growth&type=all&pop=5000'; }")
            page.wait_for_timeout(350)
            return ok
        R.run(8, "The Illinois colour break follows the structure-type filter", s8)

        # ---------------- 9 (added, disclosed) ----------------
        def s9():
            R.out("  Not in SPEC.md §7. The chart runs from 2000 but the metric starts")
            R.out("  in 2010, and nothing said so. This asserts the rendered chart")
            R.out("  actually marks the two eras apart, and that a place whose permit")
            R.out("  office skipped years is marked as such rather than shown as zero.")
            geoid = "1714351"   # Cicero: 0 of 12 months in six metric years
            page.evaluate("(g) => window.app.selectPlace(g)", geoid)
            page.wait_for_function(
                "() => document.querySelector('#detail .chart-holder svg')",
                timeout=15_000)
            page.wait_for_timeout(400)
            svg = page.evaluate(
                "() => document.querySelector('#detail .chart-holder svg').outerHTML")
            faded = svg.count('fill-opacity="0.4"')
            dashed = svg.count('stroke-dasharray')
            R.out(f"    faded pre-{built['meta']['metric_start']} band paths : {faded}")
            R.out(f"    dashed strokes (era outline + boundary rule): {dashed}")
            note = page.evaluate(
                "() => { const n = document.querySelector('#detail .chart-note');"
                " return n ? n.innerText : ''; }")
            R.out(f"    chart note: {note[:150]!r}")
            body = page.inner_text("#detail-body")
            names_years = "2010" in body and "2023" in body
            R.out("    the detail panel names the years the office did not report: "
                  f"{names_years}")
            page.evaluate("() => window.app.closeDetail()")
            page.wait_for_timeout(300)
            return (faded >= len(["sf", "du", "mf34", "mf5p"]) and dashed >= 2
                    and bool(note) and names_years)
        R.run(9, "The chart separates the pre-2010 context from the metric window, "
                 "and marks unreported years", s9)

        # ---------------- 10 (added, disclosed) ----------------
        def s10():
            R.out("  Not in SPEC.md §7. Added with the state silhouette (CLAUDE.md")
            R.out("  deviation 25). Unincorporated land has to be filled beneath the")
            R.out("  places, the state edge drawn above the county lines, the opening")
            R.out("  view has to come from meta.state_bbox, and the legend has to say")
            R.out("  what the new fill means.")
            ids = opening["layers"]
            R.out(f"    layer order: {' > '.join(ids)}")
            idx = {k: (ids.index(k) if k in ids else -1) for k in (
                "bg", "state-fill", "places-gap", "places-fill",
                "counties-line", "state-line", "places-selected")}
            order = (-1 not in idx.values()
                     and idx["bg"] < idx["state-fill"] < idx["places-gap"]
                     < idx["places-fill"] < idx["counties-line"]
                     < idx["state-line"] < idx["places-selected"])
            R.out(f"    silhouette under the places, outline over the counties and "
                  f"under the selection: {order}")
            (bw, bs), (be, bn) = built["meta"]["state_bbox"]
            (vw, vs), (ve, vn) = opening["view"]
            fits = vw <= bw and vs <= bs and ve >= be and vn >= bn
            R.out(f"    meta.state_bbox {built['meta']['state_bbox']}")
            R.out(f"    opening view    {[[round(vw, 3), round(vs, 3)], [round(ve, 3), round(vn, 3)]]}"
                  f"  contains the state: {fits}")
            drawn = opening["stateRendered"] > 0
            R.out(f"    state-fill rendered features in the opening view: "
                  f"{opening['stateRendered']}")
            swatch = opening["landSwatch"]
            distinct = (swatch not in ("", "rgba(0, 0, 0, 0)")
                        and "gradient" in opening["gapSwatch"])
            R.out(f"    legend swatches: unincorporated {swatch!r}, "
                  f"no-permit-office hatch present: {'gradient' in opening['gapSwatch']}")
            return order and fits and drawn and distinct
        R.run(10, "Unincorporated land is filled beneath the places, and the "
                  "opening view is the state's own extent", s10)

        # ---------------- 11 (added, disclosed) ----------------
        def s11():
            R.out("  Not in SPEC.md §7. Added with the permits-vs-built comparison")
            R.out("  (CLAUDE.md deviation 27). A place with a fully reported decade")
            R.out("  shows the figure and its parts; a place without one says why")
            R.out("  instead of showing a number; and the table's new column puts")
            R.out("  blanks last, never at the top of a ranking.")
            meta = built["meta"]
            ok = True

            def open_built(geoid):
                page.evaluate("(g) => window.app.selectPlace(g)", geoid)
                page.wait_for_function(
                    "(g) => window.app.detail().geoid === g"
                    " && document.querySelector('#detail #built')", arg=geoid,
                    timeout=15_000)
                return page.evaluate("""() => {
                    const b = document.querySelector('#detail #built');
                    const q = s => { const e = b.querySelector(s); return e ? e.innerText : null; };
                    return { gap: q('.built-gap'), net: q('.built-net'),
                             permits: q('.built-permits'), il: q('#built-il-gap'),
                             none: q('.built-none'), rows: b.querySelectorAll('tbody tr').length,
                             text: b.innerText,
                             panel: document.querySelector('#detail').innerText };
                }""")

            nap = open_built("1751622")          # Naperville
            census = page.evaluate("() => (document.querySelector('#detail .census-net') || {}).innerText")
            R.out(f"    Naperville: change {nap['net']!r}, permitted {nap['permits']!r}, "
                  f"gap {nap['gap']!r}, Illinois {nap['il']!r}, "
                  f"census change in the headline {census!r}")
            ok = ok and census == "+3,078"
            il_want = ("\u2212" if meta["built"]["il_gap"] < 0 else "+") \
                + f"{abs(meta['built']['il_gap']):,}"
            # The table is the two parts only; the difference is read out in prose.
            good = (nap["rows"] == 2 and nap["net"] == "+3,078"
                    and nap["permits"] == f"{3078 + 733:,}"
                    and nap["gap"] == "733 units less than" and nap["il"] == il_want
                    and "proves nothing" in nap["text"])
            R.out(f"      two-row table, difference, Illinois reference and caveat present: {good}")
            ok = ok and good

            cic = open_built("1714351")          # Cicero
            R.out(f"    Cicero: {(cic['none'] or '')[:110]!r}")
            good = (cic["gap"] is None and bool(cic["none"]) and "2010" in cic["none"]
                    and cic["net"] is not None and "25,836" in cic["panel"])
            R.out(f"      no figure, the reason names the years, and the census counts "
                  f"are still shown: {good}")
            ok = ok and good
            page.evaluate("() => window.app.closeDetail()")
            page.wait_for_timeout(300)

            top = page.evaluate("() => { const t = document.getElementById('top-caveat');"
                                " return t ? t.innerText : ''; }")
            good = "approved" in top and "census" in top.lower()
            R.out(f"    top-of-page caveat present: {good}")
            ok = ok and good

            for d in ("asc", "desc"):
                page.evaluate("(d) => { location.hash = "
                              "`#metric=pct_growth&type=all&pop=5000&sort=built_gap:${d}`; }", d)
                page.wait_for_timeout(500)
                col = page.evaluate("""() => {
                    const heads = [...document.querySelectorAll('#table-head-row th')]
                        .map(t => t.textContent);
                    const i = heads.findIndex(h => /2020 count vs permits/.test(h));
                    const cells = [...document.querySelectorAll('#table-body tr')]
                        .map(tr => tr.children[i] ? tr.children[i].innerText : '');
                    return { i, first: cells[0], last: cells[cells.length - 1],
                             n: cells.length, blank: cells.filter(c => c === '\u2014').length };
                }""")
                good = (col["i"] >= 0 and col["first"] not in ("\u2014", "")
                        and (col["blank"] == 0 or col["last"] == "\u2014"))
                R.out(f"    sort {d:<4}: column {col['i']}, first {col['first']!r}, "
                      f"last {col['last']!r}, {col['blank']} blank of {col['n']}  "
                      f"{'ok' if good else 'BLANKS NOT LAST'}")
                ok = ok and good
            page.evaluate("() => { location.hash = "
                          "'#metric=pct_growth&type=all&pop=5000'; }")
            page.wait_for_timeout(350)
            return ok
        R.run(11, "Permits vs built shows its parts where the decade was fully "
                  "reported, and says why where it was not", s11)

        # ---------------- 12 (added, disclosed) ----------------
        def s12():
            R.out("  Not in SPEC.md §7. Added with the legislator view (CLAUDE.md")
            R.out("  deviation 31). The page must load clean, restore a district from its")
            R.out("  link, switch chambers, find a legislator by name, and the map's")
            R.out("  detail panel must link each place to its districts.")
            errs: list[str] = []
            p4 = context.new_page()
            p4.on("console", lambda m: errs.append(f"console.{m.type}: {m.text}")
                  if m.type == "error" else None)
            p4.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
            p4.on("requestfailed",
                  lambda r: errs.append(f"requestfailed: {r.url} {r.failure}"))
            p4.goto(f"{base}/districts.html#senate-28", wait_until="load")
            p4.wait_for_function("window.districtsApp && window.districtsApp.ready === true",
                                 timeout=READY_TIMEOUT_MS)
            p4.wait_for_timeout(600)
            got = p4.evaluate("window.districtsApp.panel()")
            R.out(f"    #senate-28 restores: {got['member']!r}, {len(got['rows'])} places, "
                  f"estimate {got['estimate']!r}")
            checks = {
                "link restores Senate 28 and its member": "Laura Murphy" in got["member"],
                "Senate 28 lists Park Ridge, Des Plaines and Schaumburg":
                    {"Park Ridge", "Des Plaines", "Schaumburg"} <= set(got["rows"]),
                "estimate shown": any(ch.isdigit() for ch in got["estimate"]),
                "census count change shown": any(ch.isdigit() for ch in got["netChange"]),
            }
            shown = p4.evaluate("() => window.map.queryRenderedFeatures("
                                "{layers: ['senate-labels']}).map(f => f.properties.district)")
            R.out(f"    district numbers drawn with Senate 28 selected: {sorted(shown)}")
            checks["district numbers are drawn, including the selected one"] = 28 in shown
            p4.click("[data-chamber=house]")
            p4.wait_for_timeout(400)
            vis = p4.evaluate("() => ['senate-line', 'house-line'].map(l =>"
                              " window.map.getLayoutProperty(l, 'visibility'))")
            R.out(f"    after the House toggle, senate/house outline visibility: {vis}")
            checks["the toggle swaps the district outlines"] = vis == ["none", "visible"]
            p4.fill("#district-search", "Laura Murphy")
            p4.press("#district-search", "Enter")
            p4.wait_for_timeout(600)
            v = p4.evaluate("window.districtsApp.view()")
            R.out(f"    searching 'Laura Murphy' selects: {v}")
            checks["search by name finds the district"] = (
                v["chamber"] == "senate" and v["district"] == 28)
            p4.goto(f"{base}/districts.html#house-54", wait_until="load")
            p4.wait_for_function("window.districtsApp && window.districtsApp.ready === true",
                                 timeout=READY_TIMEOUT_MS)
            got = p4.evaluate("window.districtsApp.panel()")
            R.out(f"    #house-54 restores: {got['member']!r}; first place {got['rows'][:1]}")
            checks["a House link restores its district"] = (
                "Arlington Heights" in got["rows"] and got["member"].startswith("Rep."))
            # A municipality in the district table opens beside it, on this page.
            p4.click("#district-rows tr[data-geoid='1702154'] td:first-child a")
            p4.wait_for_function("() => document.querySelector('#detail .pct')"
                                 " && window.districtsApp.place().geoid === '1702154'",
                                 timeout=15_000)
            pl = p4.evaluate("window.districtsApp.place()")
            url = p4.url.rsplit("/", 1)[-1]
            R.out(f"    clicking Arlington Heights in the table: {url}, panel "
                  f"{pl['name']!r} {pl['percentText']!r}")
            checks["a table municipality opens beside the table, not on the main map"] = (
                url.startswith("districts.html#house-54&place=1702154")
                and pl["open"] and "Arlington Heights" in pl["name"]
                and any(ch.isdigit() for ch in pl["percentText"]))
            # A fresh page, so this is a load and not a same-document hash change.
            p4.close()
            p4 = context.new_page()
            p4.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
            p4.goto(f"{base}/districts.html#senate-28&place=1757875", wait_until="load")
            p4.wait_for_function("window.districtsApp && window.districtsApp.ready === true",
                                 timeout=READY_TIMEOUT_MS)
            pl = p4.evaluate("window.districtsApp.place()")
            R.out(f"    #senate-28&place=1757875 restores: {pl['name']!r}")
            checks["a link with a place restores its panel"] = (
                pl["open"] and "Park Ridge" in pl["name"])
            p4.close()

            page.evaluate("(g) => window.app.selectPlace(g)", "1702154")
            page.wait_for_function(
                "() => window.app.detail().geoid === '1702154'"
                " && document.querySelector('#detail .detail-districts')", timeout=15_000)
            links = page.evaluate("() => [...document.querySelectorAll('#detail .detail-districts a')]"
                                  ".map(a => a.getAttribute('href'))")
            page.evaluate("() => window.app.closeDetail()")
            R.out(f"    Arlington Heights' panel links: {links}")
            checks["a place's panel links to each of its districts"] = sorted(links) == sorted(
                ["districts.html#senate-27", "districts.html#house-54", "districts.html#house-53"])
            R.out(f"    console errors / failed requests on the district page: {len(errs)}")
            for e in errs[:10]:
                R.out(f"      {e}")
            checks["district page has no console errors"] = not errs
            for k, val in checks.items():
                R.out(f"    {'ok  ' if val else 'FAIL'} {k}")
            return all(checks.values())
        R.run(12, "The legislator view restores a district from its link, switches "
                  "chambers and finds a legislator; places link to their districts", s12)

        # ---------------- 13 (added, disclosed) ----------------
        def s13():
            R.out("  Not in SPEC.md §7. Added with the IHDA 2023 AHPAA data (CLAUDE.md")
            R.out("  deviation 35). The non-exempt switch must work and show only")
            R.out("  non-exempt places, both tables must carry status and share, a")
            R.out("  non-exempt panel must say so, and a place IHDA did not score must")
            R.out("  say that rather than 'exempt'.")
            meta = built["meta"]
            checks = {"meta.ahpaa enabled": bool(meta["ahpaa"]["enabled"])}
            page.evaluate("() => { location.hash = '#metric=pct_growth&type=all&pop=0&ahpaa=1'; }")
            page.wait_for_timeout(600)
            st = page.evaluate("""() => ({
                disabled: document.getElementById('toggle-ahpaa').disabled,
                on: window.app.state().ahpaa,
                geoids: window.app.highlightedGeoids(),
                heads: [...document.querySelectorAll('#table-head-row th')].map(t => t.textContent),
                rows: document.querySelectorAll('#table-body tr').length,
                tagged: document.querySelectorAll('#table-body tr .ahpaa-tag').length,
                tagLinks: [...document.querySelectorAll('#table-body tr .ahpaa-tag')]
                    .filter(a => a.getAttribute('href') === 'about.html#ahpaa').length
            })""")
            non = {f["properties"]["geoid"] for f in built["geojson"]["features"]
                   if (f["properties"].get("ahpaa_status") or "").lower() == "non-exempt"}
            R.out(f"    #ahpaa=1: switch on {st['on']}, {len(st['geoids'])} highlighted of "
                  f"{len(non)} non-exempt; table columns {st['heads'][-2:]}")
            checks["the switch is enabled and restored from the link"] = (
                not st["disabled"] and st["on"])
            checks["only non-exempt places are highlighted"] = (
                0 < len(st["geoids"]) <= len(non) and set(st["geoids"]) <= non)
            R.out(f"    table rows {st['rows']}, with the non-exempt tag {st['tagged']}")
            checks["the table tags every non-exempt row and carries the share"] = (
                st["rows"] > 0 and st["tagged"] == st["rows"]
                and any(h.startswith("Affordable share") for h in st["heads"]))
            checks["every table tag links to the explainer"] = st["tagLinks"] == st["tagged"]
            # Under 25% affordable, over 2,000 people (CLAUDE.md deviation 37).
            page.evaluate("() => { location.hash = '#metric=pct_growth&type=all&pop=0&under25=1'; }")
            page.wait_for_timeout(600)
            u = page.evaluate("""() => ({
                on: window.app.state().under25,
                geoids: window.app.highlightedGeoids(),
                hintLink: (document.querySelector('#ahpaa-hint a') || {}).getAttribute
                    ? document.querySelector('#ahpaa-hint a').getAttribute('href') : null
            })""")
            u25 = {f["properties"]["geoid"] for f in built["geojson"]["features"]
                   if f["properties"].get("under25") is True}
            R.out(f"    #under25=1: switch on {u['on']}, {len(u['geoids'])} highlighted of "
                  f"{len(u25)} (meta {meta['ahpaa'].get('n_under25')}); hint links to {u['hintLink']!r}")
            checks["the under-25% switch is restored from the link and highlights only those places"] = (
                u["on"] and 0 < len(u["geoids"]) <= len(u25) and set(u["geoids"]) <= u25)
            checks["the AHPAA hint links to the explainer"] = u["hintLink"] == "about.html#ahpaa"
            page.evaluate("() => { location.hash = '#metric=pct_growth&type=all&pop=5000'; }")
            page.wait_for_timeout(400)

            def panel(geoid):
                page.evaluate("(g) => window.app.selectPlace(g)", geoid)
                page.wait_for_function(
                    "(g) => window.app.detail().geoid === g"
                    " && document.querySelector('#detail .detail-ahpaa')", arg=geoid,
                    timeout=15_000)
                return page.evaluate("() => document.querySelector('#detail .detail-ahpaa').innerText")
            wil = panel("1782075")            # Wilmette, non-exempt
            cah = panel("1710373")            # Cahokia Heights, formed after IHDA's years
            tim = panel("1775360")            # Timberlane, non-exempt with a population note
            link = page.evaluate(
                "() => (document.querySelector('#detail .detail-ahpaa a') || {}).href || ''")
            page.evaluate("() => window.app.closeDetail()")
            R.out(f"    Wilmette: {wil[:90]!r}")
            R.out(f"    Cahokia Heights: {cah[:90]!r}")
            checks["a non-exempt panel shows its share and the tag"] = (
                "non-exempt" in wil.lower() and "%" in wil)
            R.out(f"    Timberlane: {tim[:140]!r}")
            checks["Timberlane stays non-exempt and explains the 906 Census count"] = (
                "non-exempt" in tim.lower() and "906" in tim)
            checks["the panel links AHPAA to the explainer"] = link.endswith("about.html#ahpaa")
            checks["a place IHDA did not score says so, not 'exempt'"] = (
                "not in" in cah and "exempt" not in cah.lower())

            p5 = context.new_page()
            p5.goto(f"{base}/districts.html#senate-9", wait_until="load")
            p5.wait_for_function("window.districtsApp && window.districtsApp.ready === true",
                                 timeout=READY_TIMEOUT_MS)
            d = p5.evaluate("""() => ({
                heads: [...document.querySelectorAll('.district-table th')].map(t => t.textContent),
                tagged: [...document.querySelectorAll('#district-rows tr')]
                    .filter(tr => tr.querySelector('.ahpaa-tag'))
                    .map(tr => tr.querySelector('td a').textContent)
            })""")
            R.out(f"    Senate 9 table, tagged non-exempt: {sorted(d['tagged'])}")
            checks["the district table tags non-exempt towns and carries the share"] = (
                any(h.startswith("Affordable share") for h in d["heads"])
                and {"Wilmette", "Winnetka", "Kenilworth"} <= set(d["tagged"]))

            # Sorting the district table: by % growth, highest first, blanks last.
            p5.click(".th-sort[data-sort='pct_growth']")
            p5.wait_for_timeout(200)
            srt = p5.evaluate("""() => {
                const heads = [...document.querySelectorAll('.district-table th')].map(t => t.textContent);
                const i = heads.findIndex(h => h.startsWith('% growth'));
                const v = [...document.querySelectorAll('#district-rows tr')].map(tr => {
                    const t = tr.children[i].innerText; const n = parseFloat(t);
                    return isNaN(n) ? null : n; });
                return { v, aria: document.querySelector('.district-table th[aria-sort]').innerText };
            }""")
            nums = [x for x in srt["v"] if x is not None]
            first_blank = next((k for k, x in enumerate(srt["v"]) if x is None), len(srt["v"]))
            good = (nums == sorted(nums, reverse=True)
                    and all(x is None for x in srt["v"][first_blank:]))
            R.out(f"    sorted by % growth: {srt['v'][:5]}... marked {srt['aria']!r}: {good}")
            checks["the district table sorts, blanks last"] = good
            p5.close()
            for k, val in checks.items():
                R.out(f"    {'ok  ' if val else 'FAIL'} {k}")
            return all(checks.values())
        R.run(13, "AHPAA: the non-exempt and under-25% switches, the status and share "
                  "columns, links to the explainer, and an unscored place shown as unscored", s13)

        # ---------------- 14 (added, disclosed) ----------------
        def s14():
            R.out("  Not in SPEC.md §7. Added with Austin's table and search requests")
            R.out("  (CLAUDE.md deviation 36): searching a name opens that place; the")
            R.out("  multifamily flag is a plain column, not an orange tag; and the")
            R.out("  totals and population say which years they cover.")
            checks = {}
            page.fill("#place-search", "Naperville")
            page.press("#place-search", "Enter")
            page.wait_for_function("() => window.app.detail().geoid === '1751622'"
                                   " && document.querySelector('#detail .detail-table')",
                                   timeout=15_000)
            got = page.evaluate("""() => ({
                name: window.app.detail().name,
                total: [...document.querySelectorAll('#detail .detail-table strong')]
                    .map(s => s.textContent).find(t => t.startsWith('All types')) || '',
                heads: [...document.querySelectorAll('#table-head-row th')].map(t => t.textContent),
                orange: document.querySelectorAll('.zero-mf-tag').length
            })""")
            page.evaluate("() => window.app.closeDetail()")
            page.evaluate("() => { document.getElementById('place-search').value = ''; }")
            R.out(f"    search 'Naperville' opens {got['name']!r}; total row {got['total']!r}")
            R.out(f"    table columns: {got['heads']}")
            checks["search by name opens the place"] = "Naperville" in got["name"]
            checks["the all-types total names its years"] = bool(
                re.match(r"All types, \d{4}–\d{4}$", got["total"]))
            checks["population names its year"] = "Population (2020)" in got["heads"]
            checks["the multifamily flag is a column, and no orange tag remains"] = (
                any(h.startswith("Multifamily since") for h in got["heads"])
                and got["orange"] == 0)
            for k, val in checks.items():
                R.out(f"    {'ok  ' if val else 'FAIL'} {k}")
            return all(checks.values())
        R.run(14, "Search finds a municipality; the multifamily flag is a plain column; "
                  "totals and population carry their years", s14)

        # ---------------- 15 (added, disclosed) ----------------
        def s15():
            R.out("  Not in SPEC.md §7. Added with the link previews (CLAUDE.md")
            R.out("  deviation 38): a shared link is a town's or district's own page,")
            R.out("  which must land a reader on the right view, carrying any filters")
            R.out("  in its hash, and \"Copy link\" must hand that page out.")
            errs: list[str] = []
            context.grant_permissions(["clipboard-read", "clipboard-write"], origin=base)
            p6 = context.new_page()
            p6.on("console", lambda m: errs.append(f"console.{m.type}: {m.text}")
                  if m.type == "error" else None)
            p6.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
            p6.on("requestfailed",
                  lambda r: errs.append(f"requestfailed: {r.url} {r.failure}"))
            slug = next(f["properties"]["slug"] for f in built["geojson"]["features"]
                        if f["properties"]["geoid"] == "1751622")
            checks = {}

            p6.goto(f"{base}/town/{slug}/", wait_until="load")
            p6.wait_for_function("window.app && window.app.ready === true",
                                 timeout=READY_TIMEOUT_MS)
            p6.wait_for_function("() => window.app.detail().geoid === '1751622'"
                                 " && document.querySelector('#detail .copy-link')",
                                 timeout=15_000)
            at = p6.evaluate("() => location.pathname + location.hash")
            R.out(f"    /town/{slug}/ lands on {at}")
            checks["a town's page opens that town on the map"] = (
                at.startswith("/index.html#place=1751622"))
            p6.click("#detail .copy-link")
            p6.wait_for_timeout(300)
            url = p6.evaluate("() => document.querySelector('#detail .copy-link').dataset.url")
            clip = p6.evaluate("() => navigator.clipboard.readText()")
            R.out(f"    Copy link gives {url}")
            checks["Copy link gives the town's page, with the view's hash"] = (
                url.startswith(f"{base}/town/{slug}/#place=1751622") and clip == url)

            p6.goto(f"{base}/town/{slug}/#place=1751622&metric=units_total&type=mf5p",
                    wait_until="load")
            p6.wait_for_function("window.app && window.app.ready === true",
                                 timeout=READY_TIMEOUT_MS)
            h = p6.evaluate("() => location.hash")
            R.out(f"    with the sender's filters, lands on hash {h}")
            checks["the sender's filters travel with the link"] = (
                "metric=units_total" in h and "type=mf5p" in h and "place=1751622" in h)

            p6.goto(f"{base}/senate/28/", wait_until="load")
            p6.wait_for_function("window.districtsApp && window.districtsApp.ready === true",
                                 timeout=READY_TIMEOUT_MS)
            p6.wait_for_timeout(400)
            at = p6.evaluate("() => location.pathname + location.hash")
            member = p6.evaluate("window.districtsApp.panel()")["member"]
            R.out(f"    /senate/28/ lands on {at}: {member!r}")
            checks["a district's page opens that district"] = (
                at.startswith("/districts.html#senate-28") and "Laura Murphy" in member)
            p6.click("#district-body .copy-link")
            p6.wait_for_timeout(300)
            url = p6.evaluate("() => document.querySelector('#district-body .copy-link').dataset.url")
            R.out(f"    Copy link gives {url}")
            checks["Copy link gives the district's page"] = url == f"{base}/senate/28/"

            R.out(f"    console errors / failed requests: {len(errs)}")
            for e in errs[:10]:
                R.out(f"      {e}")
            checks["no console errors"] = not errs
            p6.close()
            for k, val in checks.items():
                R.out(f"    {'ok  ' if val else 'FAIL'} {k}")
            return all(checks.values())
        R.run(15, "Link previews: a town's or district's page opens it on the map, and "
                  "Copy link hands that page out", s15)

        # ---------------- 16 (added, disclosed) ----------------
        def s16():
            R.out("  Not in SPEC.md §7. Added with the printable district sheet (CLAUDE.md")
            R.out("  deviation 40): the button fills one Letter page with the member, the")
            R.out("  two towns the build chose, a map and a QR code, and only that prints.")
            errs: list[str] = []
            p7 = context.new_page()
            p7.on("console", lambda m: errs.append(f"console.{m.type}: {m.text}")
                  if m.type == "error" else None)
            p7.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
            p7.on("requestfailed",
                  lambda r: errs.append(f"requestfailed: {r.url} {r.failure}"))
            p7.goto(f"{base}/districts.html#house-69", wait_until="load")
            p7.wait_for_function("window.districtsApp && window.districtsApp.ready === true",
                                 timeout=READY_TIMEOUT_MS)
            p7.evaluate("() => { window.__printed = 0; window.print = () => { window.__printed++; }; }")
            p7.click("#print-sheet-btn")
            p7.wait_for_function("() => window.__printed === 1", timeout=15_000)
            got = p7.evaluate("""() => {
                const el = document.getElementById('print-sheet');
                return {
                    text: el.innerText,
                    towns: [...el.querySelectorAll('.sheet-town h2')].map(h => h.textContent),
                    imgs: [...el.querySelectorAll('img')].map(i => i.naturalWidth > 0),
                    marks: el.querySelectorAll('.sheet-map circle').length,
                    on: document.body.classList.contains('print-sheet-on')
                };
            }""")
            p7.emulate_media(media="print")
            vis = p7.evaluate("""() => ['print-sheet', 'district-panel', 'map'].map(id => {
                const e = document.getElementById(id);
                return !!e && getComputedStyle(e).display !== 'none'
                    && !!(e.offsetWidth || e.offsetHeight);
            })""")
            foot = p7.evaluate("""() => { const f = document.querySelector('#print-sheet .sheet-foot');
                return !!f && getComputedStyle(f).display !== 'none' && f.innerText.includes('Sources:'); }""")
            pdf = p7.pdf(format="Letter", prefer_css_page_size=True, print_background=True)
            pages = len(re.findall(rb"/Type\s*/Page[^s]", pdf))
            # A smaller printable area than Letter at the sheet's own margins: A4
            # with 3/4-inch margins, roughly what an iPhone gives. Printing fires
            # afterprint, which takes the sheet down, so put it back first.
            p7.evaluate("() => document.body.classList.add('print-sheet-on')")
            small = p7.pdf(format="A4", print_background=True,
                           margin={k: "0.75in" for k in ("top", "bottom", "left", "right")})
            pages_small = len(re.findall(rb"/Type\s*/Page[^s]", small))
            p7.emulate_media(media="screen")
            R.out(f"    House 69 sheet towns: {got['towns']}; images loaded {got['imgs']}; "
                  f"map markers {got['marks']}")
            R.out(f"    in print: sheet / panel / map visible = {vis};  PDF pages {pages} "
                  f"(Letter), {pages_small} (A4, 3/4-inch margins)")
            checks = {
                "the button opens the print dialog once": True,
                "the member is on the sheet": "Joe Sosnowski" in got["text"],
                "Sosnowski's sheet shows the two best towns": got["towns"] == ["Huntley", "Harvard"],
                "the logo and QR code load": len(got["imgs"]) == 2 and all(got["imgs"]),
                "both towns are marked on the map": got["marks"] == 2,
                "only the sheet prints": vis == [True, False, False],
                "the caveat and sources print with it": foot,
                "it fits on one Letter page": pages == 1,
                "it still fits one page with a smaller printable area": pages_small == 1,
                "no console errors": not errs,
            }
            p7.close()
            for k, val in checks.items():
                R.out(f"    {'ok  ' if val else 'FAIL'} {k}")
            return all(checks.values())
        R.run(16, "The printable district sheet: the chosen towns, a map and a QR code, "
                  "and only the sheet prints, on one page", s16)

        # ---------------- B ----------------
        def sB():
            R.out("  Not in SPEC.md §7. about.html was added at the user's request,")
            R.out("  after the spec was written, and it restates the caveats a reader")
            R.out("  needs before quoting the map -- so it is verified, not assumed.")
            R.out("  Its figures are read from the same meta.json the map uses, so this")
            R.out("  also catches the page drifting out of date with the build.")
            errs: list[str] = []
            p3 = context.new_page()
            p3.on("console", lambda m: errs.append(f"console.{m.type}: {m.text}")
                  if m.type == "error" else None)
            p3.on("pageerror", lambda e: errs.append(f"pageerror: {e}"))
            p3.on("requestfailed",
                  lambda r: errs.append(f"requestfailed: {r.url} {r.failure}"))
            p3.goto(f"{base}/about.html", wait_until="load")
            p3.wait_for_timeout(1500)
            got = p3.evaluate(
                "(() => ({"
                " il: document.getElementById('s-il').innerText,"
                " us: document.getElementById('s-us').innerText,"
                " reporting: document.getElementById('s-reporting').innerText,"
                " gap: document.getElementById('s-gap').innerText,"
                " join: document.getElementById('s-join').innerText,"
                " ahpaa: document.getElementById('s-ahpaa').innerText,"
                " ahpaaNe: document.getElementById('s-ahpaa-ne').innerText,"
                " ahpaaU25: document.getElementById('s-ahpaa-u25').innerText,"
                " zero5p: document.getElementById('s-zero2').innerText,"
                " zero3p: document.getElementById('s-zero3p').innerText,"
                " mfull: document.getElementById('s-months-full').innerText,"
                " mnone: document.getElementById('s-months-none').innerText,"
                " builtIl: document.getElementById('s-built-il').innerText,"
                " builtN: document.getElementById('s-built-n').innerText,"
                " builtRose: document.getElementById('s-built-rose').innerText,"
                " builtFell: document.getElementById('s-built-fell').innerText,"
                " sources: document.querySelectorAll('#sources-list li').length,"
                " back: !!document.querySelector('a[href=\"index.html\"]')"
                "}))()"
            )
            R.out(f"  Console errors / failed requests : {len(errs)}")
            for e in errs[:10]:
                R.out(f"    {e}")
            R.out(f"  Illinois figure rendered  : {got['il']!r}")
            R.out(f"  U.S. figure rendered      : {got['us']!r}")
            R.out(f"  Coverage reporting / gap  : {got['reporting']!r} / {got['gap']!r}")
            R.out(f"  Join rate rendered        : {got['join']!r}")
            R.out(f"  AHPAA state sentence      : {got['ahpaa']!r}")
            R.out(f"  No 5+ unit / nothing above a 2-flat : "
                  f"{got['zero5p']!r} / {got['zero3p']!r}")
            R.out(f"  Full-month / no-month places       : "
                  f"{got['mfull']!r} / {got['mnone']!r}")
            R.out(f"  Permits vs 2020, IL / n  : {got['builtIl']!r} / {got['builtN']!r}")
            R.out(f"  Source list entries       : {got['sources']}")
            R.out(f"  Links back to the map     : {got['back']}")
            meta = built["meta"]
            checks = {
                "no console errors": not errs,
                "Illinois figure matches meta.json":
                    got["il"].startswith(f"{meta['il_pct_growth']:.1f}"),
                "U.S. figure matches meta.json":
                    got["us"].startswith(f"{meta['us_pct_growth']:.1f}"),
                "coverage counts filled in":
                    got["reporting"] not in ("—", "") and got["gap"] not in ("—", ""),
                "join rate filled in": got["join"] not in ("—", ""),
                "AHPAA state described": bool(got["ahpaa"].strip()),
                "AHPAA non-exempt count matches meta.json":
                    got["ahpaaNe"].replace(",", "") == str(meta["ahpaa"].get("n_non_exempt", "—")),
                "under-25% count matches meta.json":
                    got["ahpaaU25"].replace(",", "") == str(meta["ahpaa"].get("n_under25", "—")),
                "no-5+-unit count matches meta.json":
                    got["zero5p"].replace(",", "") == str(meta["n_zero_mf"]),
                "nothing-above-a-2-flat count matches meta.json":
                    got["zero3p"].replace(",", "") == str(meta["n_zero_mf3p"]),
                "months-reported counts match meta.json":
                    got["mfull"].replace(",", "")
                    == str(meta["months_counts"].get("full", 0))
                    and got["mnone"].replace(",", "")
                    == str(meta["months_counts"].get("none", 0)),
                "permits-vs-2020 figures match meta.json":
                    got["builtIl"].replace(",", "").replace("\u2212", "-").lstrip("+")
                    == str(meta["built"]["il_gap"])
                    and got["builtN"].replace(",", "") == str(meta["built"]["n_places"])
                    and got["builtRose"].replace(",", "") == str(meta["built"]["n_flag_rose"])
                    and got["builtFell"].replace(",", "") == str(meta["built"]["n_flag_fell"]),
                "sources listed": got["sources"] > 0,
                "links back to the map": bool(got["back"]),
            }
            for k, v in checks.items():
                R.out(f"    {'ok  ' if v else 'FAIL'} {k}")
            p3.close()
            return all(checks.values())
        R.run("B", "about.html loads clean and shows the build's own figures", sB)

        browser.close()

def main() -> int:
    ok, built = preflight()
    if not ok:
        return 1

    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        R.out("")
        R.out("Playwright is not importable. It is declared in pyproject.toml, so")
        R.out("`uv sync` should provide it.")
        return 1

    R.out("")
    if not ensure_browser():
        R.out("Cannot run the browser checks without Chromium.")
        return 1

    httpd, base = serve(L.DOCS)
    R.out(f"  serving {L.rel(L.DOCS)} at {base}")
    target = pick_click_target(built["geojson"])
    if target is None:
        R.out("  No suitable click target in the built data (need a `reporting` place")
        R.out("  with pct_growth, a unit count, a population >= 5,000 and an interior point).")
        httpd.shutdown()
        return 1
    R.out(f"  click target chosen from built data: {target['geoid']} {target.get('name')} "
          f"(pop {target.get('pop2020'):,})")

    console_errors: list[str] = []

    attempt = 0
    while True:
        attempt += 1
        try:
            run_checks(sync_playwright, base, built, target, console_errors)
            break
        except Exception as exc:
            if _is_transient(exc) and not R.results and attempt < 3:
                R.out(f"  Browser session failed transiently ({exc}); retrying.")
                console_errors.clear()
                time.sleep(3)
                continue
            httpd.shutdown()
            R.out("  ERROR while running the browser checks:")
            for line in traceback.format_exc().splitlines():
                R.out(f"    {line}")
            return 1
    httpd.shutdown()

    R.out("")
    R.out("=" * W)
    R.out("SUMMARY")
    R.out("=" * W)
    failed = [r for r in R.results if not r[2]]
    for num, title, okk in R.results:
        R.out(f"  [{'PASS' if okk else 'FAIL'}] {num}  {title}")
    R.out("")
    if failed:
        R.out(f"{len(failed)} of {len(R.results)} site checks FAILED: "
              f"{', '.join(str(n) for n, _, _ in failed)}")
        return 1
    R.out(f"All {len(R.results)} site checks PASSED.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
