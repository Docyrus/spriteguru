"""Render the logo SVGs to transparent PNGs with Chromium (playwright is a project dependency).

    uv run python brand/render_logos.py
"""
from pathlib import Path
from playwright.sync_api import sync_playwright
SVG = Path(__file__).resolve().parent / "svg"
PNG = Path(__file__).resolve().parent / "png"
jobs = [("logo-horizontal", [64, 128, 256]), ("logo-horizontal-on-dark", [64, 128, 256]), ("wordmark", [48, 96]), ("wordmark-on-dark", [48, 96])]
with sync_playwright() as p:
    b = p.chromium.launch()
    for name, heights in jobs:
        src = (SVG / f"{name}.svg").read_text()
        vb = [float(v) for v in src.split('viewBox="')[1].split('"')[0].split()]
        for h in heights:
            w = round(vb[2] * h / vb[3])
            page = b.new_page(viewport={"width": w, "height": h})
            page.set_content(f'<html><body style="margin:0;background:transparent">{src.replace("<svg ", f"<svg width={w} height={h} ", 1)}</body></html>')
            page.screenshot(path=str(PNG / f"{name}-{h}h.png"), omit_background=True, clip={"x": 0, "y": 0, "width": w, "height": h})
            page.close()
    b.close()
print("rendered")
