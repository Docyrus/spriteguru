"""Build SpriteGuru's Split Cells brand assets from one geometry definition."""

from __future__ import annotations

import math
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

HERE = Path(__file__).resolve().parent

GRID = 96
TILE = "#F5F6F8"
INK = "#15181D"
BLUE = "#2C6BD0"
S_PATH = "M24 22h49v16H41v11h31v25H23V58h33V47H24z"
# The same S as S_PATH, split into its cells (x0, y0, x1, y1): top bar, left column, the thin
# centre band, right column, bottom bar. Rasters draw these so every edge can land on a pixel.
S_CELLS = [(24, 22, 73, 38), (24, 38, 41, 47), (41, 47, 56, 49), (56, 49, 72, 58), (23, 58, 72, 74)]
ACCENT_BOX = (57, 58, 72, 74)

PNG_SIZES = [16, 24, 32, 48, 64, 128, 256, 512, 1024]
ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]
ICONSET = [(16, 1), (16, 2), (32, 1), (32, 2), (128, 1), (128, 2),
           (256, 1), (256, 2), (512, 1), (512, 2)]


def mark_svg() -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 96 96" role="img" aria-label="SpriteGuru">'
        f'<rect x="3" y="3" width="90" height="90" rx="22" fill="{TILE}"/>'
        f'<path d="{S_PATH}" fill="{INK}"/>'
        f'<rect x="57" y="58" width="15" height="16" fill="{BLUE}"/>'
        '</svg>\n'
    )


def mac_icon_svg() -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 1024 1024" role="img" aria-label="SpriteGuru">'
        '<defs><filter id="shadow" x="-10%" y="-10%" width="120%" height="125%">'
        '<feDropShadow dx="0" dy="10" stdDeviation="14" flood-color="#000000" flood-opacity="0.28"/>'
        '</filter></defs>'
        f'<g transform="translate(100 100) scale({824 / 96:g})" filter="url(#shadow)">'
        f'<rect x="3" y="3" width="90" height="90" rx="22" fill="{TILE}"/>'
        f'<path d="{S_PATH}" fill="{INK}"/>'
        f'<rect x="57" y="58" width="15" height="16" fill="{BLUE}"/>'
        '</g></svg>\n'
    )


def render(size: int, *, dock: bool = False) -> Image.Image:
    """Draw the mark at `size` px with the S and the blue cell snapped to whole pixels, so 16 and
    32 px icons stay crisp. Below ~48 px the 2-unit centre band would round away and split the S in
    two, so every cell keeps at least one pixel. `dock` adds the macOS icon grid margin and shadow."""
    supersample = 8 if size < 256 else 2
    canvas = size * supersample
    margin = size * 100 / 1024 if dock else 0.0
    scale = (size - 2 * margin) / GRID

    def px(unit: float) -> int:
        return math.floor(margin + unit * scale + 0.5)

    def snap(units: set[int]) -> dict[int, int]:
        # Round each edge to a pixel, but never let a gap of 2+ units (a cell) round to nothing;
        # 1-unit offsets (the bottom bar's overhang, the blue cell's inset) may merge.
        out: dict[int, int] = {}
        for prev, unit in zip([None, *sorted(units)], sorted(units)):
            out[unit] = px(unit) if prev is None else max(px(unit), out[prev] + (unit - prev >= 2))
        return out

    cells = S_CELLS + [ACCENT_BOX]
    xs = snap({x for cell in cells for x in cell[0::2]})
    ys = snap({y for cell in cells for y in cell[1::2]})

    def box(x0: int, y0: int, x1: int, y1: int) -> list[int]:
        return [x0 * supersample, y0 * supersample, x1 * supersample - 1, y1 * supersample - 1]

    inset = px(3)
    tile = box(inset, inset, size - inset, size - inset)
    radius = 22 * scale * supersample
    image = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
    if dock and size >= 32:
        shadow = Image.new("RGBA", (canvas, canvas), (0, 0, 0, 0))
        drop = round(size * 0.01) * supersample
        ImageDraw.Draw(shadow).rounded_rectangle([tile[0], tile[1] + drop, tile[2], tile[3] + drop],
                                                 radius=radius, fill=(0, 0, 0, 76))
        image = Image.alpha_composite(image, shadow.filter(ImageFilter.GaussianBlur(canvas * 0.014)))

    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle(tile, radius=radius, fill=TILE)
    for x0, y0, x1, y1 in S_CELLS:
        draw.rectangle(box(xs[x0], ys[y0], xs[x1], ys[y1]), fill=INK)
    x0, y0, x1, y1 = ACCENT_BOX
    draw.rectangle(box(xs[x0], ys[y0], xs[x1], ys[y1]), fill=BLUE)
    # BOX keeps whole-pixel cells exact; LANCZOS would ring and grey them at small sizes.
    return image.resize((size, size), Image.Resampling.BOX)


def main() -> None:
    svg_dir, png_dir = HERE / "svg", HERE / "png"
    svg_dir.mkdir(exist_ok=True)
    png_dir.mkdir(exist_ok=True)

    master = mark_svg()
    (svg_dir / "logo-mark.svg").write_text(master)
    (svg_dir / "logo-mark-on-dark.svg").write_text(master)
    (svg_dir / "app-icon-macos.svg").write_text(mac_icon_svg())

    for size in PNG_SIZES:
        render(size).save(png_dir / f"mark-{size}.png")
        render(size).save(png_dir / f"mark-on-dark-{size}.png")
        render(size, dock=True).save(png_dir / f"app-icon-macos-{size}.png")

    icons = [render(size) for size in ICO_SIZES]
    icons[-1].save(HERE / "SpriteGuru.ico", sizes=[(s, s) for s in ICO_SIZES], bitmap_format="png",
                   append_images=icons[:-1])

    if shutil.which("iconutil"):
        with tempfile.TemporaryDirectory() as temp:
            iconset = Path(temp) / "SpriteGuru.iconset"
            iconset.mkdir()
            for points, scale in ICONSET:
                suffix = "@2x" if scale == 2 else ""
                render(points * scale, dock=True).save(iconset / f"icon_{points}x{points}{suffix}.png")
            subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(HERE / "SpriteGuru.icns")], check=True)
    else:
        print("iconutil not found (macOS only): SpriteGuru.icns not rebuilt")
    print(f"brand kit written to {HERE}")


if __name__ == "__main__":
    main()
