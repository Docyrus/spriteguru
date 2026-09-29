"""The brand kit in the app (docs/failure-modes.md B1–B7): the studio header shows the brand mark,
with the dark-theme variant, crisp and with its clear space; the favicon is the mark; and the
launcher finds the brand icon for the window and the Dock, or runs without one."""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image

from conftest import ROOT, free_port
from test_13_projects import _start, _stop

BRAND = ROOT / "brand"
PAGE_ASSET = """async (url) => { const r = await fetch(url); return [r.headers.get('content-type') || '', await r.text()]; }"""


def _same_drawing(svg_text: str, name: str) -> bool:
    """The same SVG, whatever the encoding (the build inlines the mark as a data URL with its quotes changed)."""
    canon = lambda t: ET.canonicalize(xml_data=t, strip_text=True)  # noqa: E731
    return canon(svg_text) == canon((BRAND / "svg" / name).read_text())


def _pixel_diff(png: bytes, name: str) -> int:
    """Largest channel difference from an exact 32 px raster of the brand SVG's own rectangles, over
    the tile outside its rounded corners: 0 when the mark is drawn at 32 px on whole pixels (every S
    pixel exactly 4 px), large when it is blurred, shifted, scaled or recoloured. (The kit's PNGs are
    downsampled with soft edges, so they are no reference for crispness.)"""
    ns = {"s": "http://www.w3.org/2000/svg"}
    root = ET.parse(BRAND / "svg" / name).getroot()
    rgb = lambda h: [int(h[i:i + 2], 16) for i in (1, 3, 5)]  # noqa: E731
    want = np.zeros((32, 32, 3), dtype=int)
    tile = root.find("s:rect", ns)
    want[:, :] = rgb(tile.get("fill"))
    for parent in [root, *root.findall("s:g", ns)]:
        for r in parent.findall("s:rect", ns):
            if r is not tile:
                x, y, w, h = (int(float(r.get(k))) for k in ("x", "y", "width", "height"))
                want[y:y + h, x:x + w] = rgb(r.get("fill") or parent.get("fill"))
    got = np.asarray(Image.open(io.BytesIO(png)).convert("RGB"), dtype=int)
    if got.shape != want.shape:
        return 255
    inner = np.ones((32, 32), dtype=bool)
    for ys in (slice(0, 8), slice(24, 32)):
        for xs in (slice(0, 8), slice(24, 32)):
            inner[ys, xs] = False  # the rounded corners blend with the header behind them
    return int(np.abs(got - want)[inner].max())


def test_brand(rec, work, run_dir):
    S = "brand"
    from playwright.sync_api import expect, sync_playwright

    lib = work / S / "library"
    lib.mkdir(parents=True)
    port = free_port()
    srv = _start(port, {"SPRITEGURU_LIBRARY": str(lib)})
    shots = run_dir / "outputs" / S
    shots.mkdir(parents=True, exist_ok=True)

    def shot(page, name, clip=None):
        p = shots / f"{name}.png"
        page.screenshot(path=str(p), clip=clip)
        rec.files.append({"scenario": S, "path": p.relative_to(run_dir).as_posix(), "sha256": None, "in_digest": False})

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch()
            page = browser.new_page(viewport={"width": 1400, "height": 900}, color_scheme="light")
            errors = []
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(f"http://127.0.0.1:{port}/?token=t0k")
            expect(page.get_by_test_id("projects-screen")).to_be_visible(timeout=15000)
            mark = page.get_by_test_id("topbar-brand-mark")
            expect(mark).to_have_attribute("data-variant", "light", timeout=10000)

            def drawn():
                # after a theme switch currentSrc names the new file only once it has decoded
                src = mark.evaluate("el => el.decode().then(() => (el.naturalWidth > 0 ? el.currentSrc : ''), () => '')")
                return bool(src), page.evaluate(PAGE_ASSET, src)[1] if src else ""

            loaded, svg = drawn()
            rec.check(S, "the header mark is the brand kit's logo-mark.svg and it loads",
                      loaded and _same_drawing(svg, "logo-mark.svg"), loaded, ["B1"])
            diff = _pixel_diff(mark.screenshot(), "logo-mark.svg")
            rec.check(S, "the header mark renders crisp, pixel for pixel like the brand SVG at 32 px",
                      diff <= 2, diff, ["B1", "B3"])

            def geometry():
                m = mark.bounding_box()
                b = page.locator(".brand").bounding_box()
                t = page.get_by_test_id("topbar").bounding_box()
                # clear space to the brand box's right border and the header's bottom border (1 px each)
                clear = [m["x"] - b["x"], b["x"] + b["width"] - 1 - (m["x"] + m["width"]),
                         m["y"] - t["y"], t["y"] + t["height"] - 1 - (m["y"] + m["height"])]
                return {"size": [m["width"], m["height"]], "at": [m["x"], m["y"]], "clear": clear,
                        "in_corner": m["x"] < b["x"] + b["width"] and m["y"] < t["y"] + t["height"]}

            def good(g):
                return (g["size"] == [32, 32] and all(float(v).is_integer() for v in g["at"])
                        and min(g["clear"]) >= 8 and g["in_corner"])

            wide = geometry()
            rec.check(S, "the mark is a 32 px square on whole pixels in the left corner, a quarter of its width clear",
                      good(wide), wide, ["B3"])
            shot(page, "01-header-light", clip={"x": 0, "y": 0, "width": 700, "height": 120})

            page.get_by_test_id("topbar-theme-toggle").click()
            expect(mark).to_have_attribute("data-variant", "on-dark", timeout=5000)
            loaded, svg = drawn()
            diff = _pixel_diff(mark.screenshot(), "logo-mark-on-dark.svg")
            rec.check(S, "the dark theme shows logo-mark-on-dark.svg, the white tile",
                      loaded and _same_drawing(svg, "logo-mark-on-dark.svg") and diff <= 2, diff, ["B2"])
            shot(page, "02-header-dark", clip={"x": 0, "y": 0, "width": 700, "height": 120})
            page.get_by_test_id("topbar-theme-toggle").click()
            expect(mark).to_have_attribute("data-variant", "light", timeout=5000)
            loaded, svg = drawn()
            diff = _pixel_diff(mark.screenshot(), "logo-mark.svg")
            rec.check(S, "switching back to the light theme brings back the black tile",
                      loaded and _same_drawing(svg, "logo-mark.svg") and diff <= 2, diff, ["B2"])

            page.set_viewport_size({"width": 820, "height": 700})
            page.wait_for_function("() => getComputedStyle(document.querySelector('.brand')).width === '60px'")
            narrow = geometry()
            diff = _pixel_diff(mark.screenshot(), "logo-mark.svg")
            rec.check(S, "at a narrow width (60 px rail) the mark keeps its size, grid and clear space",
                      good(narrow) and diff <= 2, {**narrow, "diff": diff}, ["B3"])
            shot(page, "03-header-narrow", clip={"x": 0, "y": 0, "width": 500, "height": 120})

            href = page.evaluate("() => document.querySelector('link[rel=icon]').href")
            ctype, svg = page.evaluate(PAGE_ASSET, href)
            rec.check(S, "the favicon is the brand mark",
                      ctype.startswith("image/svg+xml") and _same_drawing(svg, "logo-mark.svg"), ctype, ["B4"])
            rec.check(S, "no page errors", not errors, errors[:3])
            browser.close()
    finally:
        _stop(srv)

    # the launcher's icon for the window and the Dock, found in the repo's brand kit when run from source
    want = BRAND / "png" / ("app-icon-macos-512.png" if sys.platform == "darwin" else "mark-256.png")
    out = subprocess.run([sys.executable, "-c", "from spriteguru.launcher import app_icon; print(app_icon())"],
                         cwd=ROOT, capture_output=True, text=True).stdout.strip()
    rec.check(S, "run from source, the launcher hands pywebview the brand icon for this platform",
              out == str(want), out.replace(str(ROOT), "<repo>"), ["B6"])

    # the same module outside the repo: frozen with the icon in its bundle, then with no brand files at all
    lone = work / S / "installed" / "site" / "spriteguru"
    lone.mkdir(parents=True)
    shutil.copy(ROOT / "src" / "spriteguru" / "launcher.py", lone / "launcher.py")
    bundle = work / S / "bundle"
    (bundle / "brand" / "png").mkdir(parents=True)
    shutil.copy(want, bundle / "brand" / "png" / want.name)
    probe = ("import sys; sys.path.insert(0, sys.argv[1]); sys.frozen = True; sys._MEIPASS = sys.argv[2]\n"
             "import launcher; print(launcher.app_icon())")
    found = subprocess.run([sys.executable, "-c", probe, str(lone), str(bundle)], capture_output=True, text=True)
    shutil.rmtree(bundle)
    none = subprocess.run([sys.executable, "-c", probe, str(lone), str(bundle)], capture_output=True, text=True)
    got = [found.stdout.strip() == str(bundle / "brand" / "png" / want.name), none.stdout.strip(),
           found.returncode, none.returncode]
    rec.check(S, "a frozen build uses the icon it carries; with no brand files the icon is skipped, no error",
              got == [True, "None", 0, 0], got, ["B6", "B7"])
