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


def run_checks(sync_playwright, base, built, target, console_errors):
    """Drive the built site once.  Raises on a transient driver failure so
    main() can retry before any check has recorded a result."""
    with sync_playwright() as pw:
        browser = pw.chromium.launch(args=CHROMIUM_ARGS)
        page = browser.new_page(viewport={"width": 420, "height": 900})

        page.on("console", lambda m: console_errors.append(f"console.{m.type}: {m.text}")
                if m.type == "error" else None)
        page.on("pageerror", lambda e: console_errors.append(f"pageerror: {e}"))
        page.on("requestfailed",
                lambda r: console_errors.append(f"requestfailed: {r.url} {r.failure}"))

        page.goto(f"{base}/index.html", wait_until="load")
        page.wait_for_function("window.app && window.app.ready === true",
                               timeout=READY_TIMEOUT_MS)
        page.wait_for_timeout(1200)   # let the first paint settle

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
            val = page.evaluate(
                "(() => { const t = document.querySelector('#legend-midpoint').innerText;"
                " const m = t.match(/-?\\d+(?:\\.\\d+)?/); return m ? parseFloat(m[0]) : null; })()"
            )
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
            R.out(f"  'Zero multifamily' switch on : {on}")
            R.out(f"  Highlighted features after  : {after:,}")
            page.click("#toggle-zero-mf")
            page.wait_for_timeout(400)
            R.out(f"  Reduced: {after < before}   (and still non-empty: {after > 0})")
            return bool(on) and after < before
        R.run(6, '"Zero multifamily" reduces the count of highlighted features', s6)

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
            p2 = browser.new_page(viewport={"width": 420, "height": 900})
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

        # ---------------- B ----------------
        def sB():
            R.out("  Not in SPEC.md §7. about.html was added at the user's request,")
            R.out("  after the spec was written, and it restates the caveats a reader")
            R.out("  needs before quoting the map -- so it is verified, not assumed.")
            R.out("  Its figures are read from the same meta.json the map uses, so this")
            R.out("  also catches the page drifting out of date with the build.")
            errs: list[str] = []
            p3 = browser.new_page(viewport={"width": 420, "height": 900})
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
