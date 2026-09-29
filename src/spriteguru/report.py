"""HTML reports written next to every output (section 17): contact sheet with frame indices,
detected grid and anchors overlaid, findings with metric values, animated preview."""

from __future__ import annotations

import base64
import html
import io
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from .qa.score import finding_cost

PALETTE = [(230, 60, 60), (60, 140, 230), (240, 170, 30), (40, 180, 90), (170, 80, 200), (20, 170, 170),
           (230, 110, 170), (120, 120, 120)]


def _font(size: int):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:
        return ImageFont.load_default()


def checker(w: int, h: int, s: int = 8) -> Image.Image:
    yy, xx = np.mgrid[0:h, 0:w]
    c = (((yy // s) + (xx // s)) % 2).astype(np.uint8)
    arr = np.where(c[..., None] == 0, 236, 204).astype(np.uint8).repeat(3, -1)
    return Image.fromarray(arr, "RGB")


def on_checker(rgba: np.ndarray) -> Image.Image:
    img = Image.fromarray(rgba, "RGBA")
    base = checker(img.width, img.height).convert("RGBA")
    base.alpha_composite(img)
    return base.convert("RGB")


def overlay(sheet_rgb: np.ndarray, analysis, truth_boxes: list | None = None) -> Image.Image:
    """Sheet with detected regions, frame boxes, anchors and removed marks."""
    img = Image.fromarray(sheet_rgb[..., :3]).convert("RGB")
    d = ImageDraw.Draw(img)
    font = _font(max(12, img.height // 40))
    lay = analysis.layout
    for f in lay.frames:
        col = PALETTE[f.index % len(PALETTE)]
        d.rectangle(f.region, outline=col, width=2)
        d.rectangle(f.box, outline=(255, 255, 255), width=1)
        d.text((f.region[0] + 4, f.region[1] + 2), str(f.index + 1), fill=col, font=font)
    for fi in analysis.report.frames:
        ax, ay = fi.anchor
        d.line([(ax - 8, ay), (ax + 8, ay)], fill=(255, 0, 0), width=2)
        d.line([(ax, ay - 8), (ax, ay + 8)], fill=(255, 0, 0), width=2)
    for tb in truth_boxes or []:
        d.rectangle(tb, outline=(0, 0, 0), width=1)
    for y in lay.lines.get("h", []):
        d.line([(0, y), (img.width, y)], fill=(255, 255, 0), width=1)
    for x in lay.lines.get("v", []):
        d.line([(x, 0), (x, img.height)], fill=(255, 255, 0), width=1)
    return img


def contact_sheet(frames: list[np.ndarray], scale: int = 1, labels: list[str] | None = None,
                  badges: dict[int, str] | None = None) -> Image.Image:
    if not frames:
        return Image.new("RGB", (64, 64), (255, 255, 255))
    h, w = frames[0].shape[:2]
    gutter = 28
    W = len(frames) * (w * scale + gutter) + gutter
    H = h * scale + 2 * gutter
    out = Image.new("RGB", (W, H), (250, 250, 250))
    d = ImageDraw.Draw(out)
    font = _font(18)
    for i, f in enumerate(frames):
        tile = on_checker(f)
        if scale != 1:
            tile = tile.resize((w * scale, h * scale), Image.Resampling.NEAREST)
        x = gutter + i * (w * scale + gutter)
        out.paste(tile, (x, gutter))
        d.text((x, 4), labels[i] if labels else str(i + 1), fill=(20, 20, 20), font=font)
        if badges and i in badges:
            d.text((x, gutter + h * scale + 4), badges[i], fill=(200, 40, 40), font=font)
    return out


def on_black_additive(rgba: np.ndarray) -> Image.Image:
    """How an additive sprite looks in the game: colour × alpha added onto a dark scene."""
    a = rgba[..., 3:4].astype(np.float32) / 255.0
    return Image.fromarray(np.clip(rgba[..., :3].astype(np.float32) * a, 0, 255).astype(np.uint8), "RGB")


def gif_bytes(frames: list[np.ndarray], durations: list[int], *, pixel: bool = False, scale: int = 1,
              blend: str = "normal") -> bytes:
    imgs = []
    for f in frames:
        img = on_black_additive(f) if blend == "add" else on_checker(f)
        if scale != 1:
            img = img.resize((img.width * scale, img.height * scale), Image.Resampling.NEAREST)
        imgs.append(img.convert("P", palette=Image.Palette.ADAPTIVE, dither=Image.Dither.NONE if pixel
                                else Image.Dither.FLOYDSTEINBERG))
    buf = io.BytesIO()
    if imgs:
        imgs[0].save(buf, format="GIF", save_all=True, append_images=imgs[1:], duration=durations or 83, loop=0,
                     disposal=2)
    return buf.getvalue()


def data_uri(img: Image.Image | bytes, mime: str = "image/png") -> str:
    if isinstance(img, Image.Image):
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        img = buf.getvalue()
    return f"data:{mime};base64," + base64.b64encode(img).decode()


CSS = """
:root{--bg:#f6f5f2;--fg:#1d1d1f;--muted:#6b6b70;--card:#fff;--line:#e3e1dc;--fail:#c62828;--warn:#b26a00;--ok:#2e7d32}
@media (prefers-color-scheme:dark){:root{--bg:#161618;--fg:#ececef;--muted:#9a9aa2;--card:#202024;--line:#2f2f35}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.5 -apple-system,system-ui,Segoe UI,sans-serif}
main{max-width:1200px;margin:0 auto;padding:24px 16px}h1{font-size:22px;margin:0 0 4px}h2{font-size:16px;margin:28px 0 8px}
.muted{color:var(--muted)}.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;margin:10px 0;overflow-x:auto}
img{max-width:100%;height:auto;image-rendering:auto}img.px{image-rendering:pixelated}
table{border-collapse:collapse;width:100%}td,th{padding:6px 8px;border-bottom:1px solid var(--line);text-align:left;vertical-align:top}
.pill{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px;font-weight:600;color:#fff}
.fail{background:var(--fail)}.warn{background:var(--warn)}.info{background:#607d8b}.ok{background:var(--ok)}
.score{font-size:40px;font-weight:700}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px}
code{font-size:12px}
"""


def page(title: str, body: str) -> str:
    return (f"<!doctype html><html><head><meta charset='utf-8'><meta name='viewport' content='width=device-width,"
            f"initial-scale=1'><title>{html.escape(title)}</title><style>{CSS}</style></head><body><main>{body}"
            f"</main></body></html>")


def findings_table(findings) -> str:
    rows = []
    for f in sorted(findings, key=finding_cost, reverse=True):
        frames = ", ".join(str(i + 1) for i in f.frames) if f.frames else "sheet"
        val = "" if f.value is None else f"{f.value}"
        thr = "" if f.threshold is None else f" (threshold {f.threshold})"
        fixed = " · auto-fixed" if f.auto_fixed else ""
        rows.append(f"<tr><td><span class='pill {f.level}'>{f.level}</span></td><td><b>{html.escape(f.metric)}</b>"
                    f"<div class='muted'>{html.escape(f.source)}{fixed}</div></td><td>{html.escape(f.message)}</td>"
                    f"<td>{frames}</td><td><code>{html.escape(val)}{html.escape(thr)}</code></td>"
                    f"<td>{html.escape(f.remedy)}</td></tr>")
    if not rows:
        return "<p class='muted'>No findings.</p>"
    return ("<table><tr><th>Level</th><th>Metric</th><th>Finding</th><th>Frames</th><th>Value</th><th>Remedy</th>"
            "</tr>" + "".join(rows) + "</table>")


def analysis_html(analysis, sheet_rgb: np.ndarray, *, title: str, pixel: bool = False,
                  truth_boxes: list | None = None, extra: str = "") -> str:
    rep = analysis.report
    ov = overlay(sheet_rgb, analysis, truth_boxes)
    ov.thumbnail((1600, 1600))
    scale = 4 if pixel else 1
    cs = contact_sheet(analysis.frames, scale=scale) if analysis.frames else None
    gif = gif_bytes(analysis.frames, analysis.durations, pixel=pixel, scale=scale) if analysis.frames else b""
    status = "<span class='pill ok'>accepted</span>" if rep.accepted else "<span class='pill fail'>not accepted</span>"
    body = [f"<h1>{html.escape(title)}</h1><p class='muted'>{html.escape(rep.spec.action if rep.spec else '')} · "
            f"{len(analysis.frames)} frames · canvas {rep.canvas[0]}×{rep.canvas[1]} · pivot "
            f"({rep.pivot[0]:.3f}, {rep.pivot[1]:.3f})</p>",
            f"<div class='card'><span class='score'>{rep.score:.0f}</span> <span class='muted'>/ 100</span> {status}"
            f"<div class='muted'>layout: {html.escape(rep.layout.get('hypothesis', ''))} · confidence "
            f"{rep.layout.get('confidence', 0):.2f} · matte: {html.escape(str(rep.matte.get('path')))} · key "
            f"{html.escape(str(rep.matte.get('key')))}</div></div>",
            "<h2>Findings</h2><div class='card'>" + findings_table(rep.findings) + "</div>",
            "<h2>Detected layout</h2><div class='card'><img src='" + data_uri(ov) + "'></div>"]
    if cs is not None:
        body.append("<h2>Frames</h2><div class='card'><img class='px' src='" + data_uri(cs) + "'></div>")
        body.append("<h2>Preview</h2><div class='card'><img class='px' src='" + data_uri(gif, "image/gif") + "'></div>")
    body.append(extra)
    body.append("<h2>Report JSON</h2><div class='card'><details><summary>show</summary><pre>"
                + html.escape(json.dumps(rep.model_dump(mode="json"), indent=1)[:200000]) + "</pre></details></div>")
    return page(title, "".join(body))


def write_analysis(out_dir: Path, analysis, sheet_rgb: np.ndarray, *, title: str, pixel: bool = False,
                   truth_boxes: list | None = None, extra: str = "") -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "report.json").write_text(json.dumps(analysis.report.model_dump(mode="json"), indent=2))
    p = out_dir / "report.html"
    p.write_text(analysis_html(analysis, sheet_rgb, title=title, pixel=pixel, truth_boxes=truth_boxes, extra=extra))
    return p
