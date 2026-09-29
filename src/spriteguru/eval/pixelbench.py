"""Pixel reconstruction benchmark (12.6, D5 exit criterion): our per-frame drifting lattice and joint
palette against unfake (runs-based/edge-aware scale detection, dominant-colour downscaling) on
sheets with known logical pixels. Score: exact-match rate of reconstructed logical pixels."""

from __future__ import annotations

import html
import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
from PIL import Image

from .. import report as report_mod
from ..pipeline import layout as layout_mod
from ..pipeline import matte as matte_mod
from ..pipeline import pixel as pixel_mod
from ..pipeline.register import Crop
from . import fixtures
from .golden import pixel_match

BENCH = [
    {"id": "pitch4.4", "pitch": 4.4, "drift": 0.0, "aa": 0.0, "noise": 2},
    {"id": "pitch5.6-drift", "pitch": 5.6, "drift": 0.15, "aa": 0.4, "noise": 2},
    {"id": "pitch7.1-drift-aa", "pitch": 7.1, "drift": 0.22, "aa": 0.6, "noise": 3, "per_frame_pitch": 0.08},
    {"id": "pitch9.3-heavy", "pitch": 9.3, "drift": 0.3, "aa": 0.8, "noise": 4, "per_frame_pitch": 0.1,
     "cell_gradient": 12},
]


def _case(cfg: dict) -> fixtures.Case:
    ex = {k: cfg[k] for k in ("pitch", "drift", "aa", "per_frame_pitch", "cell_gradient") if k in cfg}
    return fixtures.Case(f"bench-{cfg['id']}", ["X1", "X3", "X4", "X5", "X6", "X7"], style="pixel", strip=False,
                         guided=True, extras=ex, degrade={"noise": cfg["noise"]}, seed=3)


def _crops(img: Image.Image, truth: dict, spec):
    rgba = matte_mod.ingest(img)
    m = matte_mod.key_matte(rgba, pixel=True)
    cw, ch = truth["cell"]
    cells = [((i % truth["cols"]) * cw, (i // truth["cols"]) * ch, (i % truth["cols"] + 1) * cw,
              (i // truth["cols"] + 1) * ch) for i in range(len(truth["frames"]))]
    lay = layout_mod.detect(m.mask, layout_mod.Prior(n=len(cells), rows=truth["rows"], cols=truth["cols"], cells=cells))
    crops = []
    for f in lay.frames:
        x0, y0, x1, y1 = f.box
        c = m.rgba[y0:y1, x0:x1].copy()
        c[~(lay.labels[y0:y1, x0:x1] == f.label)] = 0
        crops.append(Crop(c, (x0, y0), f.region))
    return crops


def _unfake(crop: np.ndarray, colors: int, tmp: Path, i: int) -> np.ndarray | None:
    src, dst = tmp / f"in{i}.png", tmp / f"out{i}.png"
    Image.fromarray(crop, "RGBA").save(src)
    p = subprocess.run(["uvx", "--from", "unfake", "unfake", str(src), "-o", str(dst), "-c", str(colors), "--quiet"],
                       capture_output=True, text=True, timeout=300)
    if p.returncode != 0 or not dst.is_file():
        return None
    return np.asarray(Image.open(dst).convert("RGBA")).copy()


def run(out: Path) -> dict:
    out.mkdir(parents=True, exist_ok=True)
    has_unfake = shutil.which("uvx") is not None
    rows = []
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for cfg in BENCH:
            case = _case(cfg)
            img, data = fixtures.build(case)
            truth = data["truth"]
            spec = fixtures.spec_for(case)
            crops = _crops(img, truth, spec)
            ours, info, _ = pixel_mod.reconstruct(crops, spec)
            logical = [f["logical"] for f in truth["frames"]]
            s_ours = [pixel_match(o.rgba, t) for o, t in zip(ours, logical)]
            s_unf = []
            if has_unfake:
                for i, (c, t) in enumerate(zip(crops, logical)):
                    u = _unfake(c.rgba, spec.style.palette_size, tmp, i)
                    s_unf.append(pixel_match(u, t) if u is not None else 0.0)
            rows.append({"case": cfg["id"], "config": cfg, "ours": round(float(np.mean(s_ours)), 4),
                         "unfake": round(float(np.mean(s_unf)), 4) if s_unf else None,
                         "ours_frames": [round(v, 4) for v in s_ours], "unfake_frames": [round(v, 4) for v in s_unf],
                         "pitch_cv": info["pitch_cv"], "colors": info["colors_after"]})
    mean_ours = round(float(np.mean([r["ours"] for r in rows])), 4)
    mean_unf = round(float(np.mean([r["unfake"] for r in rows if r["unfake"] is not None])), 4) if has_unfake else None
    doc = {"summary": {"cases": len(rows), "ours": mean_ours, "unfake": mean_unf,
                       "beats_unfake": mean_unf is not None and mean_ours > mean_unf}, "results": rows}
    (out / "results.json").write_text(json.dumps(doc, indent=2))
    trs = "".join(f"<tr><td>{html.escape(r['case'])}</td><td>{r['ours']:.3f}</td><td>{r['unfake']}</td>"
                  f"<td>{r['pitch_cv']}</td><td>{r['colors']}</td></tr>" for r in rows)
    body = (f"<h1>Pixel reconstruction benchmark</h1><div class='card'>exact logical-pixel match · ours "
            f"<b>{mean_ours:.3f}</b> · unfake <b>{mean_unf}</b></div><div class='card'><table><tr><th>Case</th>"
            f"<th>Ours</th><th>unfake</th><th>Pitch CV</th><th>Colours</th></tr>{trs}</table></div>")
    (out / "index.html").write_text(report_mod.page("Pixel benchmark", body))
    return doc
