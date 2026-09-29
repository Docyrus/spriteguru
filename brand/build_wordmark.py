"""Convert "SpriteGuru" set in Unbounded (weight 750, letter-spacing -0.03em)
into SVG outlines and compose the horizontal logo next to the mark. The committed SVGs are the
masters; rerun only if the wordmark changes.

    uvx --with brotli --with uharfbuzz --from fonttools python brand/build_wordmark.py [unbounded.woff2]

The font is Unbounded (SIL Open Font License), the variable latin file from @fontsource-variable/unbounded.
"""
import io, sys
from pathlib import Path
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont
from fontTools.pens.svgPathPen import SVGPathPen
from fontTools.pens.boundsPen import BoundsPen
from fontTools.pens.transformPen import TransformPen
import uharfbuzz as hb

WOFF2 = sys.argv[1] if len(sys.argv) > 1 else str(
    Path(__file__).resolve().parents[1] / "studio/node_modules/@fontsource-variable/unbounded/files/unbounded-latin-wght-normal.woff2"
)
OUT = Path(__file__).resolve().parent / "svg"
TEXT, WGHT, TRACK = "SpriteGuru", 750, -0.03

font = TTFont(WOFF2)
font.flavor = None
inst = instantiateVariableFont(font, {"wght": WGHT})
buf = io.BytesIO(); inst.save(buf); data = buf.getvalue()
upem = inst["head"].unitsPerEm

face = hb.Face(data); hbfont = hb.Font(face)
b = hb.Buffer(); b.add_str(TEXT); b.guess_segment_properties()
hb.shape(hbfont, b, {"kern": True, "liga": True})
gs = inst.getGlyphSet(); order = inst.getGlyphOrder()

# Site geometry: mark 32 px, text 19 px, gap 10 px (Logo component).
MARK, SIZE, GAP = 32.0, 19.0, 10.0
scale = SIZE / upem
pen_x = 0.0
parts = []
bounds = [1e9, 1e9, -1e9, -1e9]
for info, pos in zip(b.glyph_infos, b.glyph_positions):
    name = order[info.codepoint]
    sp = SVGPathPen(gs)
    # font units, y up; flip later with a group transform
    tp = TransformPen(sp, (1, 0, 0, 1, pen_x + pos.x_offset, pos.y_offset))
    gs[name].draw(tp)
    bp = BoundsPen(gs); gs[name].draw(TransformPen(bp, (1, 0, 0, 1, pen_x + pos.x_offset, pos.y_offset)))
    if bp.bounds:
        x0, y0, x1, y1 = bp.bounds
        bounds = [min(bounds[0], x0), min(bounds[1], y0), max(bounds[2], x1), max(bounds[3], y1)]
    parts.append(sp.getCommands())
    pen_x += pos.x_advance + TRACK * upem

d = " ".join(p for p in parts if p)
x0, y0, x1, y1 = bounds
text_w = (x1 - x0) * scale
text_h = (y1 - y0) * scale
# Centre the capital height on the mark (the descender of "p" hangs below, as in type).
cap = inst["OS/2"].sCapHeight
tx = MARK + GAP - x0 * scale
ty = MARK / 2 + cap / 2 * scale
W = MARK + GAP + text_w

S_PATH = "M24 22h49v16H41v11h31v25H23V58h33V47H24z"
def mark():
    return ('<g transform="scale(.3333333333)">'
            '<rect x="3" y="3" width="90" height="90" rx="22" fill="#F5F6F8"/>'
            f'<path d="{S_PATH}" fill="#15181D"/>'
            '<rect x="57" y="58" width="15" height="16" fill="#2C6BD0"/></g>')

def svg(ink):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W:.2f} {MARK:g}" role="img" aria-label="SpriteGuru">'
            f'{mark()}'
            f'<path fill="{ink}" transform="translate({tx:.3f} {ty:.3f}) scale({scale:.6f} {-scale:.6f})" d="{d}"/></svg>\n')

def wordmark(ink):
    h = MARK
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {text_w:.2f} {text_h:.2f}" role="img" aria-label="SpriteGuru">'
            f'<path fill="{ink}" transform="translate({-x0*scale:.3f} {y1*scale:.3f}) scale({scale:.6f} {-scale:.6f})" d="{d}"/></svg>\n')

(OUT / "logo-horizontal.svg").write_text(svg("#15181D"))
(OUT / "logo-horizontal-on-dark.svg").write_text(svg("#FFFFFF"))
(OUT / "wordmark.svg").write_text(wordmark("#000000"))
(OUT / "wordmark-on-dark.svg").write_text(wordmark("#FFFFFF"))
print("ok", round(W, 2), "x", MARK, "text", round(text_w, 1), round(text_h, 1))
