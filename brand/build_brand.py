"""Build the SpritePlay brand kit (icons and logo files) from one definition of the mark.

    uv run python brand/build_brand.py            # from the repo root; needs Pillow (in the project)

The mark is a 5 x 5 pixel "S" on a rounded black tile; its top-right terminal pixel is lit in
volt yellow. Geometry is on a 32-unit grid: tile 32 x 32, corner radius 8, the S grid starts at
unit 6 and each pixel is 4 units, so small sizes stay crisp (16 px: 2 px per S pixel).

Outputs, all in brand/:
  svg/        logo-mark.svg, logo-mark-on-dark.svg, app-icon-macos.svg
  png/        mark-<size>.png (full-bleed tile), app-icon-macos-<size>.png (Apple icon grid)
  SpritePlay.icns   macOS app icon (iconutil)
  SpritePlay.ico    Windows app icon (16-256)
The wordmark SVGs (svg/logo-horizontal*.svg) are text converted to outlines from Unbounded and are
committed as files; they are not rebuilt here.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent

INK = "#000000"
PAPER = "#FFFFFF"
VOLT = "#FFD20A"

GRID = 32
RADIUS = 8
ORIGIN = 6
UNIT = 4
S_PIXELS = [(1, 0), (2, 0), (3, 0), (0, 1), (1, 2), (2, 2), (3, 2), (4, 3), (0, 4), (1, 4), (2, 4), (3, 4)]
SPARK = (4, 0)

# Apple's macOS icon grid: the icon body is 824 x 824 on a 1024 canvas (100 px margin), corners ~185.
MAC_CANVAS, MAC_BODY, MAC_RADIUS = 1024, 824, 185

PNG_SIZES = [16, 24, 32, 48, 64, 128, 256, 512, 1024]
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]
ICONSET = [(16, 1), (16, 2), (32, 1), (32, 2), (128, 1), (128, 2), (256, 1), (256, 2), (512, 1), (512, 2)]


def mark_svg(tile: str, glyph: str, spark: str) -> str:
    rects = "".join(
        f'<rect x="{ORIGIN + c * UNIT}" y="{ORIGIN + r * UNIT}" width="{UNIT}" height="{UNIT}"/>' for c, r in S_PIXELS
    )
    sx, sy = ORIGIN + SPARK[0] * UNIT, ORIGIN + SPARK[1] * UNIT
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {GRID} {GRID}" role="img" aria-label="SpritePlay">'
        f'<rect width="{GRID}" height="{GRID}" rx="{RADIUS}" fill="{tile}"/>'
        f'<g fill="{glyph}">{rects}</g>'
        f'<rect x="{sx}" y="{sy}" width="{UNIT}" height="{UNIT}" fill="{spark}"/></svg>\n'
    )


def mac_icon_svg() -> str:
    m = (MAC_CANVAS - MAC_BODY) / 2
    k = MAC_BODY / GRID
    rects = "".join(
        f'<rect x="{m + (ORIGIN + c * UNIT) * k:g}" y="{m + (ORIGIN + r * UNIT) * k:g}" width="{UNIT * k:g}" height="{UNIT * k:g}"/>'
        for c, r in S_PIXELS
    )
    sx, sy = m + (ORIGIN + SPARK[0] * UNIT) * k, m + (ORIGIN + SPARK[1] * UNIT) * k
    e = 4  # half the edge stroke, so the stroke sits inside the body
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {MAC_CANVAS} {MAC_CANVAS}" role="img" aria-label="SpritePlay">'
        '<defs><filter id="s" x="-10%" y="-10%" width="120%" height="125%">'
        '<feDropShadow dx="0" dy="10" stdDeviation="14" flood-color="#000" flood-opacity="0.35"/></filter></defs>'
        f'<rect x="{m:g}" y="{m:g}" width="{MAC_BODY}" height="{MAC_BODY}" rx="{MAC_RADIUS}" fill="{INK}" filter="url(#s)"/>'
        f'<rect x="{m + e:g}" y="{m + e:g}" width="{MAC_BODY - 2 * e}" height="{MAC_BODY - 2 * e}" rx="{MAC_RADIUS - e}" '
        f'fill="none" stroke="{PAPER}" stroke-opacity="0.14" stroke-width="{2 * e}"/>'
        f'<g fill="{PAPER}">{rects}</g>'
        f'<rect x="{sx:g}" y="{sy:g}" width="{UNIT * k:g}" height="{UNIT * k:g}" fill="{VOLT}"/></svg>\n'
    )


def render(
    size: int, *, margin: float = 0.0, radius: float = RADIUS / GRID, tile=INK, glyph=PAPER, spark=VOLT, dock: bool = False
) -> Image.Image:
    """Draw the mark at `size` px. `margin` and `radius` are fractions of the canvas / body.
    `dock` adds the macOS treatment: a soft shadow in the margin and a faint light edge, so the
    black tile separates from a dark Dock."""
    ss = 8 if size < 256 else 2
    big = size * ss
    img = Image.new("RGBA", (big, big), (0, 0, 0, 0))
    body = big * (1 - 2 * margin)
    off = big * margin
    box = [off, off, off + body - 1, off + body - 1]
    if dock and size >= 32:
        shadow = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        dy = body * 0.012
        ImageDraw.Draw(shadow).rounded_rectangle([box[0], box[1] + dy, box[2], box[3] + dy], radius=body * radius, fill=(0, 0, 0, 90))
        img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(body * 0.017)))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle(box, radius=body * radius, fill=tile)
    if dock:
        w = max(1, round(body * 0.0097))
        edge = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        ImageDraw.Draw(edge).rounded_rectangle(box, radius=body * radius, outline=(255, 255, 255, 36), width=w)
        img = Image.alpha_composite(img, edge)
        d = ImageDraw.Draw(img)
    k = body / GRID
    for c, r in S_PIXELS + [SPARK]:
        x0, y0 = off + (ORIGIN + c * UNIT) * k, off + (ORIGIN + r * UNIT) * k
        d.rectangle([round(x0), round(y0), round(x0 + UNIT * k) - 1, round(y0 + UNIT * k) - 1], fill=spark if (c, r) == SPARK else glyph)
    return img.resize((size, size), Image.LANCZOS)


def render_mac(size: int) -> Image.Image:
    return render(size, margin=(MAC_CANVAS - MAC_BODY) / 2 / MAC_CANVAS, radius=MAC_RADIUS / MAC_BODY, dock=True)


def main() -> None:
    svg_dir, png_dir = HERE / "svg", HERE / "png"
    svg_dir.mkdir(exist_ok=True)
    png_dir.mkdir(exist_ok=True)

    (svg_dir / "logo-mark.svg").write_text(mark_svg(INK, PAPER, VOLT))
    (svg_dir / "logo-mark-on-dark.svg").write_text(mark_svg(PAPER, INK, VOLT))
    (svg_dir / "app-icon-macos.svg").write_text(mac_icon_svg())

    for s in PNG_SIZES:
        render(s).save(png_dir / f"mark-{s}.png")
        render(s, tile=PAPER, glyph=INK).save(png_dir / f"mark-on-dark-{s}.png")
        render_mac(s).save(png_dir / f"app-icon-macos-{s}.png")

    base = render(256)
    base.save(HERE / "SpritePlay.ico", sizes=[(s, s) for s in ICO_SIZES], bitmap_format="png")

    if shutil.which("iconutil"):
        with tempfile.TemporaryDirectory() as tmp:
            iconset = Path(tmp) / "SpritePlay.iconset"
            iconset.mkdir()
            for pt, scale in ICONSET:
                name = f"icon_{pt}x{pt}{'@2x' if scale == 2 else ''}.png"
                render_mac(pt * scale).save(iconset / name)
            subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(HERE / "SpritePlay.icns")], check=True)
    else:
        print("iconutil not found (macOS only): SpritePlay.icns not rebuilt")
    print(f"brand kit written to {HERE}")


if __name__ == "__main__":
    main()
