#!/usr/bin/env python3
"""Link previews: a page and a card for every municipality and district.

A link-preview scraper (Slack, iMessage, Facebook, LinkedIn) reads the HTML at
a URL and never runs its JavaScript, and it never sees the part of the URL
after ``#``. ``index.html#place=1760352`` therefore previews as the generic
map. So every municipality and every district gets a real page:

  docs/town/<slug>/index.html    Plano -> docs/town/plano/
  docs/senate/<n>/index.html     Senate District 28 -> docs/senate/28/
  docs/house/<n>/index.html

Each carries Open Graph and Twitter tags with Austin Busch's title and
description (``L.PREVIEW_TITLE``, ``L.PREVIEW_DESCRIPTION``) and a 1200x630
``card.png`` beside it, and sends a reader on to the map with
``location.replace`` before anything is drawn. The page's "Copy link" button
hands out these addresses. ``docs/preview.png`` and ``docs/preview-districts.png``
are the cards for the three real pages.

The cards draw the map as the page does: places coloured on the same diverging
ladder, coverage gaps hatched, the state silhouette beneath and county lines
over. The palette is read from ``docs/shared.js`` and the map colours from
``docs/style.css``, so there is still one definition of each.

Offline, like the rest of the build: reads ``docs/data/`` (written by build.py)
and the fonts fetch_fonts.py caches. Rendering is deterministic, and a file is
written only when its bytes change, so a rebuild with unchanged data touches
nothing (a whole-directory rewrite is what made the cloud file provider leave
conflict copies beside the shards; CLAUDE.md deviation 29).

Run on its own with ``uv run scripts/previews.py``; build.py also calls it.
"""

from __future__ import annotations

import hashlib
import html
import json
import math
import os
import re
import shutil
import sys
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import bps_layout as L

W, H = L.PREVIEW_SIZE
SS = 2                      # drawn at twice the size, then downsampled: antialiasing
CARD = "card.png"
QR = "qr.svg"               # districts only: the printable sheet's QR code
LOGO_WEB = L.DOCS / "ahil-logo.png"   # the printable sheet's logo

# AHIL brand colours (ahil-brand skill). Text only; the map's colours come from
# the page's own CSS and palette, read below.
ORANGE, BLUE = "#E87722", "#004B87"
INK, INK2, INK3 = "#1E1E1E", "#3C3C3C", "#6b6b68"
WHITE = "#FFFFFF"
# The county line colour is set in app.js, not style.css (light mode).
COUNTY_LINE = "#b3b4af"


# --------------------------------------------------------------------------
# The page's own colour definitions
# --------------------------------------------------------------------------

def page_colors() -> dict:
    """The diverging ladder, hatch and map colours, read from the page's files.

    Fails loudly if a pattern stops matching, so a change to shared.js or
    style.css cannot leave the cards on a stale palette.
    """
    js = (L.DOCS / "shared.js").read_text(encoding="utf-8")
    m = re.search(r"const DIVERGING = \{\s*low: \[([^\]]+)\],\s*mid: '(#[0-9A-Fa-f]{6})',"
                  r"\s*high: \[([^\]]+)\]", js)
    if not m:
        raise RuntimeError("previews.py: DIVERGING not found in docs/shared.js")
    div = {"low": re.findall(r"'(#[0-9A-Fa-f]{6})'", m[1]), "mid": m[2],
           "high": re.findall(r"'(#[0-9A-Fa-f]{6})'", m[3])}
    body = re.search(r"function divergingStopsAt\(m\) \{(.*?)\n\}", js, re.S)
    stops = []
    for mult, part, idx in re.findall(
            r"\[(0|m(?: \* [\d.]+)?), DIVERGING\.(low|mid|high)(?:\[(\d)\])?\]",
            body[1] if body else ""):
        k = 0.0 if mult == "0" else 1.0 if mult == "m" else float(mult.split("*")[1])
        stops.append((k, div[part] if part == "mid" else div[part][int(idx)]))
    if len(stops) != 7:
        raise RuntimeError(f"previews.py: expected 7 diverging stops in shared.js, "
                           f"found {len(stops)}")
    hatch = re.search(r"g\.fillStyle = darkMode\(\) \? '#\w+' : '(#[0-9a-fA-F]{6})'.*?"
                      r"g\.strokeStyle = darkMode\(\) \? '#\w+' : '(#[0-9a-fA-F]{6})'",
                      js, re.S)
    css = (L.DOCS / "style.css").read_text(encoding="utf-8")
    root = re.search(r":root \{(.*?)\n\}", css, re.S)[1]
    var = dict(re.findall(r"--([\w-]+):\s*(#[0-9A-Fa-f]{6})", root))
    if not hatch or not all(k in var for k in
                            ("map-bg", "map-land", "state-line", "rule-strong")):
        raise RuntimeError("previews.py: hatch or map colours not found in "
                           "docs/shared.js / docs/style.css")
    return {"stops": stops, "hatch_bg": hatch[1], "hatch_fg": hatch[2],
            "map_bg": var["map-bg"], "land": var["map-land"],
            "state_line": var["state-line"], "place_line": var["rule-strong"]}


def _rgb(h: str) -> tuple[int, int, int]:
    return tuple(int(h[i:i + 2], 16) for i in (1, 3, 5))


def diverging(v: float | None, mid: float, stops) -> tuple[int, int, int]:
    """MapLibre's ['interpolate', ['linear'], ['to-number', value, 0], ...]."""
    v = 0.0 if v is None else v
    pts = [(k * mid, _rgb(c)) for k, c in stops]
    if v <= pts[0][0]:
        return pts[0][1]
    for (a, ca), (b, cb) in zip(pts, pts[1:]):
        if v <= b:
            t = (v - a) / (b - a) if b > a else 0
            return tuple(round(x + (y - x) * t) for x, y in zip(ca, cb))
    return pts[-1][1]


# --------------------------------------------------------------------------
# Geometry
# --------------------------------------------------------------------------

COS_LAT = math.cos(math.radians(40.0))   # Illinois' middle; fine for a preview


def _project(ring):
    return [(x * COS_LAT, -y) for x, y in ring]


def _polys(geom) -> list[list[list[tuple[float, float]]]]:
    """[[exterior, hole, ...], ...] in projected units."""
    if geom["type"] == "Polygon":
        polys = [geom["coordinates"]]
    elif geom["type"] == "MultiPolygon":
        polys = geom["coordinates"]
    else:
        return []
    return [[_project(r) for r in p] for p in polys]


def _bbox(polys):
    xs = [x for p in polys for x, _ in p[0]]
    ys = [y for p in polys for _, y in p[0]]
    return (min(xs), min(ys), max(xs), max(ys))


def _area(polys):
    a = 0.0
    for p in polys:
        r = p[0]
        a += abs(sum(x0 * y1 - x1 * y0 for (x0, y0), (x1, y1) in zip(r, r[1:] + r[:1]))) / 2
    return a


def _load_layer(name: str):
    gj = json.loads((L.DOCS_DATA / name).read_text(encoding="utf-8"))
    out = []
    for f in gj["features"]:
        polys = _polys(f["geometry"])
        if polys:
            out.append({"props": f["properties"], "polys": polys, "bbox": _bbox(polys),
                        "area": _area(polys)})
    return out


# --------------------------------------------------------------------------
# Drawing (runs in worker processes)
# --------------------------------------------------------------------------

_W: dict = {}   # worker state, filled once per process by _init


def _init() -> None:
    from PIL import Image, ImageFont

    _W["colors"] = page_colors()
    _W["meta"] = json.loads((L.DOCS_DATA / "meta.json").read_text(encoding="utf-8"))
    places = _load_layer("places.geojson")
    places.sort(key=lambda f: -f["area"])      # an enclave town draws over its host
    _W["places"] = places
    _W["counties"] = _load_layer("counties.geojson")
    _W["state"] = _load_layer("state.geojson")
    _W["districts"] = {ch: {f["props"]["district"]: f for f in _load_layer(f"{ch}.geojson")}
                       for ch in L.CHAMBERS}
    _W["font"] = {k: str(L.FONT_DIR / v) for k, v in L.FONTS.items()}
    _W["fonts"] = {}
    logo = Image.open(L.AHIL_LOGO).convert("RGB")
    # The supplied logo sits in a lot of white; crop to the mark itself.
    from PIL import ImageChops
    box = ImageChops.difference(logo, Image.new("RGB", logo.size, WHITE)).getbbox()
    _W["logo"] = logo.crop(box)
    _W["ImageFont"] = ImageFont


def _font(weight: str, px: int):
    key = (weight, px)
    if key not in _W["fonts"]:
        _W["fonts"][key] = _W["ImageFont"].truetype(_W["font"][weight], px * SS)
    return _W["fonts"][key]


def _hatch_tile(size: int):
    """shared.js hatchImage(), drawn at ``size`` px."""
    from PIL import Image, ImageDraw
    c = _W["colors"]
    t = Image.new("RGB", (size, size), c["hatch_bg"])
    d = ImageDraw.Draw(t)
    s = size
    w = max(1, round(1.6 * s / 8))
    d.line([(-2, s + 2), (s + 2, -2)], fill=c["hatch_fg"], width=w)
    d.line([(s / 2 - 2, s + s / 2 + 2), (s + s / 2 + 2, s / 2 - 2)], fill=c["hatch_fg"], width=w)
    d.line([(-s / 2 - 2, s / 2 + 2), (s / 2 + 2, -s / 2 - 2)], fill=c["hatch_fg"], width=w)
    return t


def _fill(img, xf, polys, fill) -> None:
    """Fill a polygon set, holes and all, with a colour or a same-sized image."""
    from PIL import Image, ImageDraw
    pts = [[xf(x, y) for x, y in r] for p in polys for r in p]
    xs = [x for r in pts for x, _ in r]
    ys = [y for r in pts for _, y in r]
    x0, y0 = max(0, int(min(xs)) - 1), max(0, int(min(ys)) - 1)
    x1, y1 = min(img.width, int(max(xs)) + 2), min(img.height, int(max(ys)) + 2)
    if x1 <= x0 or y1 <= y0:
        return
    mask = Image.new("L", (x1 - x0, y1 - y0), 0)
    md = ImageDraw.Draw(mask)
    for p in polys:
        md.polygon([(x - x0, y - y0) for x, y in (xf(*q) for q in p[0])], fill=255)
        for hole in p[1:]:
            md.polygon([(x - x0, y - y0) for x, y in (xf(*q) for q in hole)], fill=0)
    if isinstance(fill, str):
        fill = _rgb(fill)
    if isinstance(fill, tuple):
        img.paste(fill, (x0, y0, x1, y1), mask)
    else:
        img.paste(fill.crop((x0, y0, x1, y1)), (x0, y0), mask)


def _outline(draw, xf, polys, fill, width) -> None:
    for p in polys:
        for r in p:
            pts = [xf(x, y) for x, y in r]
            draw.line(pts + pts[:1], fill=fill, width=width, joint="curve")


def _intersects(a, b) -> bool:
    return not (a[2] < b[0] or a[0] > b[2] or a[3] < b[1] or a[1] > b[3])


def _draw_map(size, view, subject=None, locator=False):
    """The map panel: a (w, h) image at SS of ``view`` (projected bbox)."""
    from PIL import Image, ImageDraw
    c, meta = _W["colors"], _W["meta"]
    w, h = size
    vx0, vy0, vx1, vy1 = view
    # Fit the view, keeping its aspect, centred in the panel.
    k = min(w / (vx1 - vx0), h / (vy1 - vy0))
    ox = (w - (vx1 - vx0) * k) / 2 - vx0 * k
    oy = (h - (vy1 - vy0) * k) / 2 - vy0 * k
    xf = lambda x, y: (x * k + ox, y * k + oy)
    shown = (-ox / k, -oy / k, (w - ox) / k, (h - oy) / k)

    img = Image.new("RGB", (w, h), c["map_bg"])
    draw = ImageDraw.Draw(img)
    for f in _W["state"]:
        _fill(img, xf, f["polys"], c["land"])
    hatch = Image.new("RGB", (w, h))
    tile = _hatch_tile(8 * SS)
    for yy in range(0, h, tile.height):
        for xx in range(0, w, tile.width):
            hatch.paste(tile, (xx, yy))
    statewide = (shown[2] - shown[0]) > 3
    for f in _W["places"]:
        if not _intersects(f["bbox"], shown):
            continue
        p = f["props"]
        if p["coverage"] == "reporting":
            _fill(img, xf, f["polys"], diverging(p["pct_growth"], meta["il_pct_growth"],
                                                 c["stops"]))
        else:
            _fill(img, xf, f["polys"], hatch)
    lw = 1 if statewide else SS
    for f in _W["places"]:
        if _intersects(f["bbox"], shown):
            _outline(draw, xf, f["polys"], c["place_line"], lw)
    for f in _W["counties"]:
        if _intersects(f["bbox"], shown):
            _outline(draw, xf, f["polys"], COUNTY_LINE, lw if statewide else round(1.4 * SS))
    for f in _W["state"]:
        _outline(draw, xf, f["polys"], c["state_line"], round(1.2 * SS))
    if subject is not None:
        # The page's selection mark, for a town and a district alike: orange over
        # a pale casing, so it reads against either wing of the ladder.
        _outline(draw, xf, subject["polys"], WHITE, 7 * SS)
        _outline(draw, xf, subject["polys"], ORANGE, 4 * SS)
    if locator:
        _draw_locator(img, shown)
    return img


def _draw_locator(img, shown) -> None:
    """A small Illinois in the panel's corner with the view marked on it."""
    from PIL import ImageDraw
    c = _W["colors"]
    st = _W["state"][0]
    sx0, sy0, sx1, sy1 = st["bbox"]
    lh = 150 * SS
    k = lh / (sy1 - sy0)
    lw = (sx1 - sx0) * k
    pad = 14 * SS
    bx0, by0 = pad, pad
    box = (bx0, by0, bx0 + lw + 2 * pad, by0 + lh + 2 * pad)
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle(box, radius=10 * SS, fill=WHITE, outline=c["place_line"], width=SS)
    xf = lambda x, y: (bx0 + pad + (x - sx0) * k, by0 + pad + (y - sy0) * k)
    _fill(img, xf, st["polys"], c["land"])
    _outline(draw, xf, st["polys"], c["state_line"], SS)
    x0, y0 = xf(max(shown[0], sx0), max(shown[1], sy0))
    x1, y1 = xf(min(shown[2], sx1), min(shown[3], sy1))
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    r = max(7 * SS, (x1 - x0) / 2, (y1 - y0) / 2)
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), outline=ORANGE, width=3 * SS)


def _wrap(text: str, font, width: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for wd in words:
        t = f"{cur} {wd}".strip()
        if font.getlength(t) <= width or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = wd
    if cur:
        lines.append(cur)
    return lines


def _card(job: dict) -> bytes:
    """Render one 1200x630 card and return its PNG bytes."""
    import io
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (W * SS, H * SS), WHITE)
    draw = ImageDraw.Draw(img)
    s = lambda v: round(v * SS)

    # Orange top bar, as on every AHIL handout.
    draw.rectangle((0, 0, W * SS, s(10)), fill=ORANGE)

    # Map panel on the right.
    mx0, my0, mx1, my1 = 640, 34, 1170, 600
    panel = _draw_map((s(mx1 - mx0), s(my1 - my0)), job["view"],
                      job.get("subject"), job.get("locator", False))
    mask = Image.new("L", panel.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, panel.width - 1, panel.height - 1),
                                           radius=s(14), fill=255)
    img.paste(panel, (s(mx0), s(my0)), mask)
    draw.rounded_rectangle((s(mx0), s(my0), s(mx1) - 1, s(my1) - 1), radius=s(14),
                           outline=_W["colors"]["place_line"], width=SS)

    # Logo, top left.
    logo = _W["logo"]
    lh = s(58)
    logo_r = logo.resize((round(logo.width * lh / logo.height), lh), Image.LANCZOS)
    img.paste(logo_r, (s(56), s(40)))

    left, right = 56, 600
    width = s(right - left)
    # The title, as large as fits in three lines.
    for px in (50, 46, 42, 38, 34, 30):
        tf = _font("bold", px)
        lines = _wrap(job["title"], tf, width)
        if len(lines) <= 3:
            break
    y = s(128)
    for ln in lines:
        draw.text((s(left), y), ln, font=tf, fill=INK)
        y += round(tf.size * 1.18)

    # The headline figure and what it means.
    y += s(18)
    big = _font("bold", job.get("big_px", 76))
    # Ink, not orange: on the map orange means "below Illinois".
    draw.text((s(left), y), job["big"], font=big, fill=INK)
    y += round(big.size * 1.2)
    for ln in job["lines"]:
        f = _font(ln.get("weight", "regular"), ln.get("px", 23))
        for part in _wrap(ln["text"], f, width):
            draw.text((s(left), y), part, font=f, fill=ln.get("color", INK2))
            y += round(f.size * 1.32)

    foot = _font("regular", 17)
    fy = H - 44 - 24 * (len(job["foot"]) - 1)
    for i, ln in enumerate(job["foot"]):
        draw.text((s(left), s(fy + 24 * i)), ln, font=foot, fill=INK3)

    # A box filter averages each 2x2 block exactly; Lanczos rings at every edge,
    # which costs colours and bytes. 64 octree colours keep 99% of pixels within
    # 6 levels of the full-colour card at about 30 KB, against 66 KB at 256.
    out = img.resize((W, H), Image.BOX)
    out = out.quantize(colors=64, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
    buf = io.BytesIO()
    out.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _render(job: dict) -> tuple[str, bytes]:
    if not _W:
        _init()
    # Resolve geometry by reference in the worker, so the job stays small.
    if job.get("geoid"):
        job["subject"] = next(f for f in _W["places"] if f["props"]["geoid"] == job["geoid"])
    elif job.get("district"):
        ch, d = job["district"]
        job["subject"] = _W["districts"][ch][d]
    sub = job.get("subject")
    if sub is not None:
        job["view"] = _view_around(sub["bbox"], job["kind"])
        job["locator"] = True
    else:
        job["view"] = _W["state"][0]["bbox"]
    return job["out"], _card(job)


def _view_around(b, kind: str):
    """A view centred on ``b``, wide enough to show the neighbours."""
    cx, cy = (b[0] + b[2]) / 2, (b[1] + b[3]) / 2
    pad = 2.6 if kind == "town" else 1.3
    minspan = 0.16 if kind == "town" else 0.12
    aspect = (1170 - 640) / (600 - 34)
    sw = max((b[2] - b[0]) * pad, minspan)
    sh = max((b[3] - b[1]) * pad, minspan)
    # Match the panel's shape, so the view fills it.
    if sw / sh < aspect:
        sw = sh * aspect
    else:
        sh = sw / aspect
    return (cx - sw / 2, cy - sh / 2, cx + sw / 2, cy + sh / 2)


# --------------------------------------------------------------------------
# Text: titles, figures, pages
# --------------------------------------------------------------------------

def fmt_int(n) -> str:
    return f"{n:,}"


def fmt_pct(v) -> str:
    return f"{v:,.1f}%"


def roughly(n: int) -> str:
    """districts.js roughly(): nearest 10 above 1,000."""
    step = 10 if n >= 1000 else 1
    return fmt_int(round(n / step) * step)


def _domain() -> str:
    return re.sub(r"^https?://|/$", "", L.SITE_URL)


def town_job(p: dict, meta: dict) -> dict:
    name = p["name"]
    ms, ym = meta["metric_start"], meta["ymax"]
    il = fmt_pct(meta["il_pct_growth"])
    first = p.get("first_metric_year") or ms
    if p["coverage"] != "reporting":
        big, lines, big_px = "No permit data", [
            {"text": "No permit office here reports to the U.S. Census, so there is "
                     "no permit record to count. That is not the same as no housing "
                     "being built."},
            {"text": f"Illinois as a whole: {il} of its 2010 housing stock, "
                     f"permitted {ms}–{ym}.", "color": INK3, "px": 20}], 56
    elif p["pct_growth"] is None:
        big, lines, big_px = f"{fmt_int(p['units_total_2010'])} units", [
            {"text": f"permitted {first}–{ym}. No 2010 housing count is "
                     "published, so no percentage."},
            {"text": f"Illinois: {il} of its 2010 housing stock.", "color": INK3,
             "px": 20}], 64
    else:
        big, lines, big_px = fmt_pct(p["pct_growth"]), [
            {"text": f"of its 2010 housing stock, permitted {first}–{ym} "
                     f"({fmt_int(p['units_total_2010'])} units)"},
            {"text": f"Illinois: {il}", "weight": "semibold", "color": BLUE}], 76
    county = p.get("county") or ""
    return {"title": L.PREVIEW_TITLE.format(name), "big": big, "big_px": big_px,
            "lines": lines, "kind": "town", "geoid": p["geoid"],
            "foot": [f"{p['namelsad']}{', ' + county if county else ''}", _domain()],
            "alt": f"{name} on the Illinois housing permits map."}


def district_job(ch: str, row: dict, meta: dict) -> dict:
    label = f"{L.CHAMBERS[ch]['label']} District {row['district']}"
    m = row.get("member") or {}
    if m.get("vacant") or not m.get("name"):
        who = "Seat listed as vacant" if m.get("vacant") else "Member not listed"
    else:
        party = {"Democratic": "D", "Republican": "R"}.get(m.get("party"), m.get("party") or "")
        who = f"{L.CHAMBERS[ch]['title']} {m['name']}" + (f" ({party})" if party else "")
    return {"title": L.PREVIEW_TITLE.format(label), "big": roughly(row["est_units_2010"]),
            "lines": [
                {"text": f"units permitted {meta['metric_start']}–{meta['ymax']} in "
                         f"this district's {fmt_int(row['n_places'])} municipalities "
                         "(estimate)"},
                {"text": who, "weight": "semibold", "color": BLUE}],
            "kind": "district", "district": (ch, row["district"]),
            "foot": [f"Illinois {label}", _domain()],
            "alt": f"{label} on the Illinois housing permits map."}


def default_job(meta: dict, what: str) -> dict:
    ms, ym = meta["metric_start"], meta["ymax"]
    return {"title": L.PREVIEW_TITLE.format(what), "big": fmt_pct(meta["il_pct_growth"]),
            "lines": [
                {"text": f"Illinois has permitted new housing equal to this share of its "
                         f"2010 stock, {ms}–{ym}."},
                {"text": f"Every municipality, mapped: {fmt_int(meta['n_places'])}",
                 "weight": "semibold", "color": BLUE}],
            "kind": None, "foot": [_domain()],
            "alt": "The Illinois housing permits map."}


def stub_html(*, title: str, url: str, image: str, alt: str, target: str,
              hash_ok: str, default_hash: str, depth: int) -> str:
    """A preview page: tags for the scraper, an immediate redirect for a person.

    The redirect is script, not <meta http-equiv=refresh>: some scrapers follow a
    refresh and would then read the destination's generic tags instead. A hash on
    the shared link (the sender's filters) is kept if it names the same thing.
    """
    up = "../" * depth
    e = lambda s: html.escape(s, quote=True)
    desc = L.PREVIEW_DESCRIPTION
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(title)}</title>
<meta name="description" content="{e(desc)}">
<link rel="canonical" href="{e(url)}">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Abundant Housing Illinois">
<meta property="og:title" content="{e(title)}">
<meta property="og:description" content="{e(desc)}">
<meta property="og:url" content="{e(url)}">
<meta property="og:image" content="{e(image)}">
<meta property="og:image:width" content="{W}">
<meta property="og:image:height" content="{H}">
<meta property="og:image:alt" content="{e(alt)}">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{e(title)}">
<meta name="twitter:description" content="{e(desc)}">
<meta name="twitter:image" content="{e(image)}">
<link rel="icon" href="{up}favicon.svg" type="image/svg+xml">
<script>
  var h = location.hash;
  location.replace('{up}{target}' + ({hash_ok}.test(h) ? h : '{default_hash}'));
</script>
</head>
<body>
<p><a href="{up}{target}{default_hash}">{e(title)} Open the map &rarr;</a></p>
</body>
</html>
"""


def default_tags(title: str, image_name: str, url: str, alt: str, image_hash: str) -> str:
    """The Open Graph block for index.html, districts.html and about.html."""
    e = lambda s: html.escape(s, quote=True)
    img = f"{L.SITE_URL}{image_name}?v={image_hash}"
    return (f'<meta property="og:type" content="website">\n'
            f'<meta property="og:site_name" content="Abundant Housing Illinois">\n'
            f'<meta property="og:title" content="{e(title)}">\n'
            f'<meta property="og:description" content="{e(L.PREVIEW_DESCRIPTION)}">\n'
            f'<meta property="og:url" content="{e(url)}">\n'
            f'<meta property="og:image" content="{e(img)}">\n'
            f'<meta property="og:image:width" content="{W}">\n'
            f'<meta property="og:image:height" content="{H}">\n'
            f'<meta property="og:image:alt" content="{e(alt)}">\n'
            f'<meta name="twitter:card" content="summary_large_image">\n'
            f'<meta name="twitter:title" content="{e(title)}">\n'
            f'<meta name="twitter:description" content="{e(L.PREVIEW_DESCRIPTION)}">\n'
            f'<meta name="twitter:image" content="{e(img)}">')


OG_BEGIN = "<!-- link preview: written by scripts/previews.py -->"
OG_END = "<!-- /link preview -->"


def _splice(page: Path, block: str) -> str:
    text = page.read_text(encoding="utf-8")
    if OG_BEGIN in text:
        text = re.sub(re.escape(OG_BEGIN) + r".*?" + re.escape(OG_END),
                      lambda _: f"{OG_BEGIN}\n{block}\n{OG_END}", text, flags=re.S)
    else:
        text = text.replace("</title>\n", f"</title>\n{OG_BEGIN}\n{block}\n{OG_END}\n", 1)
    return text


# --------------------------------------------------------------------------
# Writing
# --------------------------------------------------------------------------

def _write_if_changed(path: Path, data: bytes) -> bool:
    try:
        if path.read_bytes() == data:
            return False
    except FileNotFoundError:
        pass
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return True


def qr_svg(url: str) -> bytes:
    """A QR code for ``url`` as a small SVG, drawn offline by segno."""
    import io
    import segno
    buf = io.BytesIO()
    segno.make(url, error="m").save(buf, kind="svg", scale=4, border=2, dark=INK,
                                    xmldecl=False, title=url)
    return buf.getvalue()


def logo_png() -> bytes:
    """The AHIL logo cropped to the mark, 480 px wide, for the printable sheet."""
    import io
    from PIL import Image, ImageChops
    logo = Image.open(L.AHIL_LOGO).convert("RGB")
    logo = logo.crop(ImageChops.difference(logo, Image.new("RGB", logo.size, WHITE)).getbbox())
    logo = logo.resize((480, round(logo.height * 480 / logo.width)), Image.LANCZOS)
    buf = io.BytesIO()
    logo.quantize(colors=32, method=Image.Quantize.FASTOCTREE,
                  dither=Image.Dither.NONE).save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def _short_hash(data: bytes) -> str:
    return hashlib.sha1(data).hexdigest()[:10]


def build(workers: int | None = None) -> dict:
    """Write every preview page and card. Returns counts for the build log."""
    for name in L.FONTS.values():
        if not (L.FONT_DIR / name).exists():
            raise SystemExit(f"Missing {L.rel(L.FONT_DIR / name)}. "
                             "Run: uv run scripts/fetch_fonts.py")
    page_colors()   # fail here, in the parent, if the palette cannot be read
    meta = json.loads((L.DOCS_DATA / "meta.json").read_text(encoding="utf-8"))
    places = json.loads((L.DOCS_DATA / "places.geojson").read_text(encoding="utf-8"))
    dist = json.loads((L.DOCS_DATA / "districts.json").read_text(encoding="utf-8"))

    jobs = []
    for f in places["features"]:
        p = f["properties"]
        j = town_job(p, meta)
        j["out"] = str(L.PREVIEW_TOWN_DIR / p["slug"] / CARD)
        j["page"] = {"rel": f"town/{p['slug']}/", "target": "index.html",
                     "hash_ok": rf"/(^#|&)place={p['geoid']}(&|$)/",
                     "default_hash": f"#place={p['geoid']}"}
        jobs.append(j)
    for ch in L.CHAMBERS:
        for row in dist["chambers"][ch]["districts"]:
            d = row["district"]
            j = district_job(ch, row, meta)
            j["out"] = str(L.preview_dir(ch) / str(d) / CARD)
            j["page"] = {"rel": f"{ch}/{d}/", "target": "districts.html",
                         "hash_ok": rf"/^#{ch}-{d}(&|$)/", "default_hash": f"#{ch}-{d}"}
            jobs.append(j)
    defaults = [
        (default_job(meta, "your town"), L.PREVIEW_DEFAULT_PNG,
         [("index.html", L.SITE_URL), ("about.html", L.SITE_URL + "about.html")]),
        (default_job(meta, "your district"), L.DOCS / "preview-districts.png",
         [("districts.html", L.SITE_URL + "districts.html")]),
    ]
    for j, out, _ in defaults:
        j["out"] = str(out)

    render = [{k: v for k, v in j.items() if k != "page"} for j in jobs] + \
             [j for j, _, _ in defaults]
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    with ProcessPoolExecutor(max_workers=workers, initializer=_init) as pool:
        pngs = dict(pool.map(_render, render, chunksize=16))

    files: list[tuple[Path, bytes]] = []
    for j in jobs:
        png = pngs[j["out"]]
        pg = j["page"]
        url = L.SITE_URL + pg["rel"]
        files.append((Path(j["out"]), png))
        files.append((Path(j["out"]).parent / "index.html", stub_html(
            title=j["title"], url=url, image=f"{url}{CARD}?v={_short_hash(png)}",
            alt=j["alt"], target=pg["target"], hash_ok=pg["hash_ok"],
            default_hash=pg["default_hash"], depth=pg["rel"].count("/")).encode()))
    for j, out, pages in defaults:
        png = pngs[j["out"]]
        files.append((out, png))
        for page, url in pages:
            files.append((L.DOCS / page, _splice(L.DOCS / page, default_tags(
                j["title"], out.name, url, j["alt"], _short_hash(png))).encode()))

    # The district sheet's QR code points at the district's preview page, so a
    # phone that scans the printout gets the same page a shared link does.
    for j in jobs:
        if j["kind"] == "district":
            files.append((Path(j["out"]).parent / QR, qr_svg(L.SITE_URL + j["page"]["rel"])))
    files.append((LOGO_WEB, logo_png()))

    with ThreadPoolExecutor(max_workers=32) as pool:
        changed = sum(pool.map(lambda fb: _write_if_changed(*fb), files))

    # Sweep: a directory for a place that left TIGER, a renamed slug, or a
    # conflict copy the cloud file provider left behind ("card 2.png").
    strays = 0
    roots = {L.PREVIEW_TOWN_DIR: {Path(j["out"]).parent.name for j in jobs if j["kind"] == "town"}}
    for ch in L.CHAMBERS:
        roots[L.preview_dir(ch)] = {str(j["district"][1]) for j in jobs
                                    if j["kind"] == "district" and j["district"][0] == ch}
    for root, want in roots.items():
        for p in root.iterdir():
            if p.name not in want:
                shutil.rmtree(p) if p.is_dir() else p.unlink()
                strays += 1
            elif p.is_dir():
                for q in p.iterdir():
                    if q.name not in ("index.html", CARD, QR):
                        shutil.rmtree(q) if q.is_dir() else q.unlink()
                        strays += 1
    return {"pages": len(jobs), "towns": sum(j["kind"] == "town" for j in jobs),
            "districts": sum(j["kind"] == "district" for j in jobs),
            "changed": changed, "strays": strays,
            "bytes": sum(len(b) for p, b in files if p.suffix == ".png")}


def main() -> int:
    r = build()
    print(f"  link previews: {r['towns']:,} towns, {r['districts']} districts, "
          f"{r['bytes'] / 1e6:.1f} MB of cards; {r['changed']:,} files changed"
          + (f", {r['strays']} strays removed" if r["strays"] else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
