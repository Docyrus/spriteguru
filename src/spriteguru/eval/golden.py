"""Golden-set runner for the analyzer: scores every fixture against its ground truth.

Checks cite failure-mode IDs from docs/failure-modes.md. Results are deterministic, so the
digest of results.json (timings excluded) is the repeatable artifact of a run.
"""

from __future__ import annotations

import hashlib
import html
import json
import time
from pathlib import Path

import numpy as np
from PIL import Image

from .. import report as report_mod
from ..color import to_oklab
from ..pipeline.analyze import GuideInfo, analyze
from ..pipeline.matte import ingest
from ..spec import SpriteSpec

IOU_MIN = 0.90


def _iou(a: np.ndarray, b: np.ndarray) -> float:
    u = np.logical_or(a, b).sum()
    return float(np.logical_and(a, b).sum() / u) if u else 0.0


def guide_info(truth: dict) -> GuideInfo | None:
    if not truth.get("guided"):
        return None
    cw, ch = truth["cell"]
    strip = truth["strip"]
    cells = []
    n = truth["spec"]["frames"]
    for i in range(n):
        r, c = divmod(i, truth["cols"])
        x0 = (c + (1 if strip else 0)) * cw
        cells.append((x0, r * ch, x0 + cw, (r + 1) * ch))
    exclude = [(0, 0, cw, truth["rows"] * ch)] if strip else []
    return GuideInfo(cells=cells, exclude=exclude, char_h=float(truth["char_h"]), rows=truth["rows"],
                     cols=truth["cols"])


def pixel_match(pred: np.ndarray, truth: np.ndarray, tol: float = 0.06) -> float:
    best = 0.0
    th, tw = truth.shape[:2]
    for dy in range(-3, 4):
        for dx in range(-3, 4):
            H, W = th + 8, tw + 8
            A = np.zeros((H, W, 4), np.uint8)
            B = np.zeros((H, W, 4), np.uint8)
            A[4:4 + th, 4:4 + tw] = truth
            py, px = 4 + dy, 4 + dx
            ph, pw = min(pred.shape[0], H - py), min(pred.shape[1], W - px)
            if ph <= 0 or pw <= 0 or py < 0 or px < 0:
                continue
            B[py:py + ph, px:px + pw] = pred[:ph, :pw]
            oa, ob = A[..., 3] > 127, B[..., 3] > 127
            union = oa | ob
            if not union.any():
                continue
            both = oa & ob
            d = np.linalg.norm(to_oklab(A[..., :3]) - to_oklab(B[..., :3]), axis=-1)
            ok = both & (d < tol)
            best = max(best, float(ok.sum() / union.sum()))
    return best


def run_case(case_dir: Path, out_dir: Path) -> dict:
    truth = json.loads((case_dir / "truth.json").read_text())
    spec = SpriteSpec.model_validate(truth["spec"])
    masks_npz = np.load(case_dir / "masks.npz")
    tmasks = [masks_npz[k] for k in sorted(masks_npz.files, key=lambda s: int(s.split("_")[1]))]
    sheet = ingest(case_dir / "sheet.png")
    t0 = time.perf_counter()
    an = analyze(sheet, spec, guide=guide_info(truth))
    elapsed = time.perf_counter() - t0
    rep = an.report
    lay = an.layout
    tf = truth["frames"]
    checks: list[dict] = []

    def check(name, ok, value=None, covers=None, detail=""):
        checks.append({"check": name, "pass": bool(ok), "value": value, "covers": covers or [], "detail": detail})

    # frame IoU against ground truth (each true frame vs its best detected frame)
    det_masks = [(lay.labels == f.label) & lay.clean_mask for f in lay.frames]
    match = []
    ious = []
    for i, tm in enumerate(tmasks):
        scores = [_iou(tm, dm) for dm in det_masks]
        j = int(np.argmax(scores)) if scores else -1
        match.append(j)
        ious.append(scores[j] if scores else 0.0)
    count_ok = len(lay.frames) == len(tf)
    exp_count = truth["expect"].get("count", len(tf))
    fid = [f.metric for f in rep.findings]
    if "frame_count" not in truth["expect"].get("findings", []):
        check("frame_count", len(lay.frames) == exp_count, len(lay.frames), ["L8", "L3", "L2"])
    iou_min = float(min(ious)) if ious else 0.0
    check("frame_iou_min>=0.90", iou_min >= IOU_MIN or truth["case"] == "touching" and iou_min >= 0.85,
          round(iou_min, 4), truth["covers"])

    # anchors on the common canvas: torso x and ground contact must coincide (in-place loops)
    al = an.aligned
    canvas_torso, canvas_ground, lifts = [], [], []
    if al is not None and spec.style.kind == "pixel":
        g_std = al.info.get("baseline_post_std", 1.0)
        check("ground_std<=1%", g_std <= 0.01, round(g_std, 4), ["R1", "R2"])
    elif al is not None and count_ok and all(j >= 0 for j in match):
        for t, j in zip(tf, match):
            off = an.crops[j].offset
            place = al.placements[j]
            s = al.scales[j]
            crop_w = an.crops[j].rgba.shape[1]
            x_local = (t["torso_x"] - off[0])
            if al.flipped[j]:
                x_local = crop_w - 1 - x_local
            cx = place[0] + x_local / s
            cy = place[1] + (t["ground_y"] - off[1]) / s
            canvas_torso.append(cx)
            canvas_ground.append((cy, t["airborne"], t["lift"]))
        sh = al.sprite_h
        grounded = [g for g, air, _ in canvas_ground if not air]
        g_std = float(np.std(grounded) / sh) if len(grounded) > 1 else 0.0
        check("ground_std<=1%", g_std <= 0.01, round(g_std, 4), ["R1", "R2"])
        if spec.loop and spec.motion == "in-place" and not truth["expect"].get("wrong_pose"):
            x_std = float(np.std(canvas_torso) / sh)
            check("torso_x_std<=2%", x_std <= 0.02, round(x_std, 4), ["R3", "R6"])
        if truth["expect"].get("lift"):
            g0 = float(np.median(grounded))
            errs = [abs((g0 - cy) - lift) / sh for cy, air, lift in canvas_ground if air]
            check("airborne_lift_err<=3%", max(errs) <= 0.03 if errs else False,
                  round(max(errs), 4) if errs else None, ["R4"])

    # order: playback sequence recovered
    if count_ok and rep.order:
        slot_of_frame = {j: tf[i]["playback"] for i, j in enumerate(match)}
        seq = [slot_of_frame.get(k, -1) for k in rep.order]
        n = len(seq)
        want = sorted(t["playback"] for t in tf)
        if spec.loop:
            ok = any(seq[r:] + seq[:r] == want for r in range(n))
        else:
            ok = seq == want
        if not truth["expect"].get("order_ambiguous"):
            check("playback_order", ok, seq, ["T1"])

    exp = truth["expect"]
    for m in exp.get("findings", []):
        check(f"finding:{m}", m in fid, [f.message for f in rep.findings if f.metric == m][:2], truth["covers"])
    if exp.get("no_fail"):
        fails = [f"{f.metric}: {f.message}" for f in rep.findings if f.level == "fail" and not f.auto_fixed]
        check("no_fail_findings", not fails, fails, truth["covers"])
    if exp.get("accept"):
        check("auto_accept", rep.accepted, rep.score, truth["covers"])
    if exp.get("shadow"):
        check("shadow_split", an.matte.signals.get("shadow_px", 0) > 0, an.matte.signals.get("shadow_px"), ["L11"])
    if exp.get("pockets"):
        check("pockets_cleared", an.matte.signals.get("pockets_cleared", 0) > 0,
              an.matte.signals.get("pockets_cleared"), ["M4"])
    if exp.get("pixel_match") is not None:
        rates = []
        for i, name in enumerate(truth.get("logical", [])):
            tl = np.asarray(Image.open(case_dir / name).convert("RGBA"))
            j = match[i] if i < len(match) else -1
            if j < 0 or j >= len(an.crops):
                rates.append(0.0)
                continue
            rates.append(pixel_match(an.crops[j].rgba, tl))
        mean = float(np.mean(rates)) if rates else 0.0
        check(f"pixel_exact_match>={exp['pixel_match']}", mean >= exp["pixel_match"], round(mean, 4),
              truth["covers"])
        check("pixel_pitch_cv<=10%", (an.pixel.get("pitch_cv") or 0) <= 0.10, an.pixel.get("pitch_cv"), ["X4"])

    passed = all(c["pass"] for c in checks)
    case_out = out_dir / truth["case"]
    boxes = [t["box"] for t in tf]
    report_mod.write_analysis(case_out, an, sheet, title=f"Golden: {truth['case']}",
                              pixel=spec.style.kind == "pixel", truth_boxes=boxes,
                              extra="<h2>Checks</h2><div class='card'>" + _checks_table(checks) + "</div>")
    return {"case": truth["case"], "covers": truth["covers"], "passed": passed, "checks": checks,
            "score": rep.score, "accepted": rep.accepted, "frames_detected": len(lay.frames),
            "frames_true": len(tf), "frame_iou": [round(v, 4) for v in ious],
            "hypothesis": lay.hypothesis, "confidence": round(lay.confidence, 4),
            "findings": [{"metric": f.metric, "level": f.level, "frames": f.frames, "auto_fixed": f.auto_fixed}
                         for f in rep.findings],
            "seconds": round(elapsed, 3)}


def _checks_table(checks: list[dict]) -> str:
    rows = "".join(f"<tr><td><span class='pill {'ok' if c['pass'] else 'fail'}'>{'pass' if c['pass'] else 'fail'}"
                   f"</span></td><td>{html.escape(c['check'])}</td><td><code>{html.escape(json.dumps(c['value']))}"
                   f"</code></td><td>{html.escape(', '.join(c['covers']))}</td></tr>" for c in checks)
    return f"<table><tr><th></th><th>Check</th><th>Value</th><th>Covers</th></tr>{rows}</table>"


def run(golden: Path, out_dir: Path, ids: list[str] | None = None, workers: int | None = None) -> dict:
    from ..parallel import pmap

    out_dir.mkdir(parents=True, exist_ok=True)
    cases = sorted(p for p in golden.iterdir() if (p / "truth.json").is_file() and (ids is None or p.name in ids))
    results = pmap(run_case, cases, [out_dir] * len(cases), workers=workers)
    covered = sorted({m for r in results if r["passed"] for m in r["covers"]})
    failing = sorted({m for r in results if not r["passed"] for m in r["covers"]})
    iou_all = [v for r in results for v in r["frame_iou"] if r["frames_detected"] == r["frames_true"]]
    summary = {"cases": len(results), "passed": sum(r["passed"] for r in results),
               "frame_iou_mean": round(float(np.mean(iou_all)), 4) if iou_all else None,
               "frame_iou_min": round(float(np.min(iou_all)), 4) if iou_all else None,
               "failure_modes_passing": covered, "failure_modes_failing": failing}
    stable = {"summary": summary, "results": [{k: v for k, v in r.items() if k != "seconds"} for r in results]}
    digest = hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()
    doc = {"digest": digest, **stable, "seconds": {r["case"]: r["seconds"] for r in results}}
    (out_dir / "results.json").write_text(json.dumps(doc, indent=2))
    (out_dir / "index.html").write_text(_index_html(doc))
    return doc


def _index_html(doc: dict) -> str:
    s = doc["summary"]
    rows = []
    for r in doc["results"]:
        pill = "<span class='pill ok'>pass</span>" if r["passed"] else "<span class='pill fail'>fail</span>"
        bad = [c["check"] for c in r["checks"] if not c["pass"]]
        rows.append(f"<tr><td>{pill}</td><td><a href='{r['case']}/report.html'>{html.escape(r['case'])}</a></td>"
                    f"<td>{html.escape(', '.join(r['covers']))}</td><td>{r['frames_detected']}/{r['frames_true']}</td>"
                    f"<td>{min(r['frame_iou']) if r['frame_iou'] else 0:.3f}</td><td>{r['score']:.0f}</td>"
                    f"<td>{html.escape(', '.join(bad))}</td></tr>")
    body = (f"<h1>Golden set: analyzer</h1><p class='muted'>digest <code>{doc['digest'][:16]}</code></p>"
            f"<div class='card'><b>{s['passed']}/{s['cases']}</b> cases pass · frame IoU mean {s['frame_iou_mean']} · "
            f"min {s['frame_iou_min']}<div class='muted'>failure modes covered: {', '.join(s['failure_modes_passing'])}"
            f"</div><div class='muted'>failing: {', '.join(s['failure_modes_failing']) or 'none'}</div></div>"
            "<div class='card'><table><tr><th></th><th>Case</th><th>Covers</th><th>Frames</th><th>Min IoU</th>"
            "<th>Q</th><th>Failed checks</th></tr>" + "".join(rows) + "</table></div>")
    return report_mod.page("Golden set", body)
