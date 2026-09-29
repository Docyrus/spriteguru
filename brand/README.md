# SpritePlay brand kit

The SpritePlay logo, ready for the desktop app: app icons, the mark and
the horizontal logo.

## The mark

A 5 × 5 pixel "S" on a rounded black tile. The top-right terminal pixel is lit in volt yellow:
the one pixel that has "moved", since a sprite reads as motion one pixel at a time.

Geometry, on a 32-unit grid: tile 32 × 32 with corner radius 8; the S grid starts at unit 6 and
each pixel is 4 units. At 16 px every S pixel is exactly 2 px, so small sizes stay crisp.

| Colour | Hex | Use |
| --- | --- | --- |
| Ink | `#000000` | tile (on light), wordmark |
| Paper | `#FFFFFF` | S pixels (on light), tile and wordmark on dark |
| Volt | `#FFD20A` | the spark pixel only |

Keep clear space of at least a quarter of the tile's width on every side. Don't recolour the tile,
move or recolour the spark, round the S pixels, add outlines or effects (the macOS icon's edge and
shadow are the exception, see below), or stretch the mark.

## Files

| File | For |
| --- | --- |
| `SpritePlay.icns` | macOS app icon (the `.app` bundle, PyInstaller `BUNDLE(icon=...)`) |
| `SpritePlay.ico` | Windows app icon, 16 to 256 px (PyInstaller `EXE(icon=...)`) |
| `svg/app-icon-macos.svg` | macOS icon master: Apple's grid (824 px body on a 1024 canvas), with a faint light edge and soft shadow so the black tile separates from a dark Dock |
| `png/app-icon-macos-<size>.png` | the same, 16 to 1024 px |
| `svg/logo-mark.svg` | the mark, full bleed (in-app header, favicon, pywebview window icon, Linux icons) |
| `svg/logo-mark-on-dark.svg` | the mark for dark surfaces (white tile, black S) |
| `png/mark-<size>.png`, `png/mark-on-dark-<size>.png` | the mark, 16 to 1024 px |
| `svg/logo-horizontal.svg`, `svg/logo-horizontal-on-dark.svg` | mark and wordmark side by side (about screen, splash, docs) |
| `png/logo-horizontal*-<height>h.png` | the same at 64, 128 and 256 px tall |
| `svg/wordmark.svg`, `svg/wordmark-on-dark.svg` | the wordmark alone |

The wordmark is "SpritePlay" in Unbounded at weight 750 with −0.03 em letter spacing, converted to
outlines, so no font needs to be installed. In the lockup the mark and the text's capital height
share a centre line, with a gap of 10/32 of the mark's height.

For the studio: the header can use `svg/logo-mark.svg` at 28–32 px next to the product name, and
`studio/index.html` can use it as the favicon. On the graphite (dark) theme use the `-on-dark`
files.

## Rebuilding

```bash
uv run python brand/build_brand.py     # icons, marks, .icns (needs macOS iconutil) and .ico
uv run python brand/render_logos.py    # PNGs of the horizontal logo and wordmark
uvx --with brotli --with uharfbuzz --from fonttools python brand/build_wordmark.py   # only if the wordmark changes
```

The mark's geometry lives in `build_brand.py`; the website draws the same mark in
`spriteguru-web/src/components/brand/logo.tsx`.
