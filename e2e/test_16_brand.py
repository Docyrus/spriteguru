"""The brand kit in the app (docs/failure-modes.md B1–B7): the Split Cells master geometry and crisp
16 and 32 px rasters; the studio header shows the brand mark, with the dark-theme variant, crisp and
with its clear space; the favicon is the mark; and the launcher finds the brand icon for the window
and the Dock, or runs without one."""

from __future__ import annotations

import io
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from urllib.parse import quote

import numpy as np
from PIL import Image

from conftest import ROOT, free_port
from test_13_projects import _start, _stop

BRAND = ROOT / "brand"
SPLIT_CELLS = "M24 22h49v16H41v11h31v25H23V58h33V47H24z"
PAGE_ASSET = """async (url) => { const r = await fetch(url); return [r.headers.get('content-type') || '', await r.text()]; }"""


def _same_drawing(svg_text: str, name: str) -> bool:
    """The same SVG, whatever the encoding (the build inlines the mark as a data URL with its quotes changed)."""
    canon = lambda t: ET.canonicalize(xml_data=t, strip_text=True)  # noqa: E731
    return canon(svg_text) == canon((BRAND / "svg" / name).read_text())


def _reference(browser, name: str) -> np.ndarray:
    """The brand SVG drawn by the same browser at 32 px on whole pixels, with its alpha."""
    page = browser.new_page(viewport={"width": 32, "height": 32})
    svg = (BRAND / "svg" / name).read_text()
    page.set_content('<body style="margin:0;background:transparent">'
                     f'<img src="data:image/svg+xml,{quote(svg)}" width="32" height="32" style="display:block"></body>')
    page.wait_for_function("() => document.images[0].complete && document.images[0].naturalWidth > 0")
    png = page.screenshot(omit_background=True)
    page.close()
    return np.asarray(Image.open(io.BytesIO(png)).convert("RGBA"), dtype=int)


def _pixel_diff(png: bytes, ref: np.ndarray) -> int:
    """Largest channel difference from the reference raster over its opaque pixels (the rounded
    corners blend with the header behind them): 0 when the header draws the mark at 32 px on whole
    pixels, large when it is blurred, shifted, scaled or recoloured."""
    got = np.asarray(Image.open(io.BytesIO(png)).convert("RGB"), dtype=int)
    if got.shape != (32, 32, 3):
        return 255
    solid = ref[..., 3] == 255
    return int(np.abs(got - ref[..., :3])[solid].max())


def test_brand(rec, work, run_dir):
    S = "brand"
    from playwright.sync_api import expect, sync_playwright

    mark_svg = (BRAND / "svg" / "logo-mark.svg").read_text()
    assert 'viewBox="0 0 96 96"' in mark_svg
    assert '<rect x="3" y="3" width="90" height="90" rx="22" fill="#F5F6F8"' in mark_svg
    assert f'd="{SPLIT_CELLS}" fill="#15181D"' in mark_svg
    assert '<rect x="57" y="58" width="15" height="16" fill="#2C6BD0"' in mark_svg
    assert "SpriteGuru" in mark_svg
    assert "FFD20A" not in mark_svg
    for size in (16, 32):
        image = Image.open(BRAND / "png" / f"mark-{size}.png").convert("RGB")
        pixels = np.asarray(image)
        assert image.size == (size, size)
        assert np.any(np.all(pixels == (21, 24, 29), axis=2))
        assert np.any(np.all(pixels == (44, 107, 208), axis=2))

    launcher_spec = (ROOT / "packaging" / "launcher.spec").read_text()
    assert "SpriteGuru.icns" in launcher_spec and "SpriteGuru.ico" in launcher_spec
    assert "com.spriteguru.studio" in launcher_spec

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
            ref = {name: _reference(browser, name) for name in ("logo-mark.svg", "logo-mark-on-dark.svg")}
            diff = _pixel_diff(mark.screenshot(), ref["logo-mark.svg"])
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
            diff = _pixel_diff(mark.screenshot(), ref["logo-mark-on-dark.svg"])
            rec.check(S, "the dark theme shows logo-mark-on-dark.svg, the same light tile",
                      loaded and _same_drawing(svg, "logo-mark-on-dark.svg") and diff <= 2, diff, ["B2"])
            shot(page, "02-header-dark", clip={"x": 0, "y": 0, "width": 700, "height": 120})
            page.get_by_test_id("topbar-theme-toggle").click()
            expect(mark).to_have_attribute("data-variant", "light", timeout=5000)
            loaded, svg = drawn()
            diff = _pixel_diff(mark.screenshot(), ref["logo-mark.svg"])
            rec.check(S, "switching back to the light theme brings back logo-mark.svg",
                      loaded and _same_drawing(svg, "logo-mark.svg") and diff <= 2, diff, ["B2"])

            page.set_viewport_size({"width": 820, "height": 700})
            page.wait_for_function("() => getComputedStyle(document.querySelector('.brand')).width === '60px'")
            narrow = geometry()
            diff = _pixel_diff(mark.screenshot(), ref["logo-mark.svg"])
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
    assert all(check["pass"] for check in rec.checks if check["scenario"] == S)
