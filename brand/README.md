# SpriteGuru brand kit

The SpriteGuru logo, ready for the desktop app: app icons, the mark and the horizontal logo.

## The mark: Split Cells

A compact modular "S" on a light rounded tile. The S is built from cells (a top bar, a left
column, a thin centre band, a right column and a bottom bar) and its lower-right terminal cell is
blue: the one cell that has "moved", since a sprite reads as motion one cell at a time.

Geometry, on a 96-unit grid: tile 90 × 90 at (3, 3) with corner radius 22; the S is the path
`M24 22h49v16H41v11h31v25H23V58h33V47H24z`; the blue cell is 15 × 16 at (57, 58).

| Colour | Hex | Use |
| --- | --- | --- |
| Tile | `#F5F6F8` | the rounded tile, on light and dark surfaces alike |
| Ink | `#15181D` | the S, and the wordmark on light surfaces |
| Blue | `#2C6BD0` | the terminal cell only |

Keep clear space of at least a quarter of the tile's width on every side. Don't recolour the tile,
move or recolour the blue cell, round the S cells, add outlines or effects (the macOS icon's shadow
is the exception, see below), or stretch the mark.

## Files

| File | For |
| --- | --- |
| `SpriteGuru.icns` | macOS app icon (the `.app` bundle, PyInstaller `BUNDLE(icon=...)`) |
| `SpriteGuru.ico` | Windows app icon, 16 to 256 px (PyInstaller `EXE(icon=...)`) |
| `svg/app-icon-macos.svg` | macOS icon master: Apple's grid (824 px body on a 1024 canvas) with a soft shadow |
| `png/app-icon-macos-<size>.png` | the same, 16 to 1024 px |
| `svg/logo-mark.svg` | the mark (in-app header, favicon, pywebview window icon, Linux icons) |
| `svg/logo-mark-on-dark.svg` | the mark for dark surfaces (the same light tile, so it looks identical on both themes) |
| `png/mark-<size>.png`, `png/mark-on-dark-<size>.png` | the mark, 16 to 1024 px |
| `svg/logo-horizontal.svg`, `svg/logo-horizontal-on-dark.svg` | mark and wordmark side by side (about screen, splash, docs) |
| `png/logo-horizontal*-<height>h.png` | the same at 64, 128 and 256 px tall |
| `svg/wordmark.svg`, `svg/wordmark-on-dark.svg` | the wordmark alone |

The wordmark is "SpriteGuru" in Unbounded at weight 750 with −0.03 em letter spacing, converted to
outlines, so no font needs to be installed. In the lockup the mark and the text's capital height
share a centre line, with a gap of 10/32 of the mark's height.

The PNG, ICNS and ICO rasters snap the S and the blue cell to whole pixels, so 16 and 32 px icons
stay crisp; below about 48 px every cell keeps at least one pixel, so the S never splits in two.

## Rebuilding

```bash
uv run python brand/build_brand.py     # icons, marks, .icns (needs macOS iconutil) and .ico
uv run python brand/render_logos.py    # PNGs of the horizontal logo and wordmark
uvx --with brotli --with uharfbuzz --from fonttools python brand/build_wordmark.py   # only if the wordmark changes
```

`build_wordmark.py` reads Unbounded from `studio/node_modules/@fontsource-variable/unbounded`, so
run `npm ci` in `studio/` first. The mark's geometry lives in `build_brand.py`.
