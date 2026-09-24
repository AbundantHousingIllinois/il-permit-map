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
                    const g = b.querySelector('.built-gap');
                    const il = b.querySelector('#built-il-gap');
                    const none = b.querySelector('.built-none');
                    return { gap: g ? g.innerText : null, il: il ? il.innerText : null,
                             none: none ? none.innerText : null,
                             text: b.innerText };
                }""")

            nap = open_built("1751622")          # Naperville
            R.out(f"    Naperville: gap {nap['gap']!r}, Illinois {nap['il']!r}")
            il_want = ("\u2212" if meta["built"]["il_gap"] < 0 else "+") \
                + f"{abs(meta['built']['il_gap']):,}"
            good = nap["gap"] == "\u2212733" and nap["il"] == il_want \
                and "not a verdict" in nap["text"]
            R.out(f"      figure, Illinois reference and caveat present: {good}")
            ok = ok and good

            cic = open_built("1714351")          # Cicero
            R.out(f"    Cicero: {(cic['none'] or '')[:110]!r}")
            good = (cic["gap"] is None and bool(cic["none"]) and "2010" in cic["none"]
                    and "25,836" in cic["text"])
            R.out(f"      no figure, the reason names the years, and the census counts "
                  f"are still shown: {good}")
            ok = ok and good
            page.evaluate("() => window.app.closeDetail()")
            page.wait_for_timeout(300)

            top = page.evaluate("() => { const t = document.getElementById('top-caveat');"
                                " return t ? t.innerText : ''; }")
            good = "floor" in top and "census" in top.lower()
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
            checks["search by name finds the district"] = v == {"chamber": "senate", "district": 28}
            p4.goto(f"{base}/districts.html#house-54", wait_until="load")
            p4.wait_for_function("window.districtsApp && window.districtsApp.ready === true",
                                 timeout=READY_TIMEOUT_MS)
            got = p4.evaluate("window.districtsApp.panel()")
            R.out(f"    #house-54 restores: {got['member']!r}; first place {got['rows'][:1]}")
            checks["a House link restores its district"] = (
                "Arlington Heights" in got["rows"] and got["member"].startswith("Rep."))
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
            R.out(f"  No 5+ unit / nothing above a duplex : "
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
                "no-5+-unit count matches meta.json":
                    got["zero5p"].replace(",", "") == str(meta["n_zero_mf"]),
                "nothing-above-a-duplex count matches meta.json":
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
