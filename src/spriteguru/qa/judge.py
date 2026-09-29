"""VLM judge (14.2): GPT-6 Luna reads an annotated contact sheet plus the reference.

Its findings are advisory unless a CV metric corroborates them or the severity is 3, and it
never decides frame counts.
"""

from __future__ import annotations

import io
import json

import numpy as np
from PIL import Image, ImageDraw

from .. import prompts, registry
from ..providers.base import ProviderRequest
from ..providers.hub import ProviderHub
from ..report import _font, on_checker
from ..spec import Finding

ISSUE_TYPES = ["missing_item", "changed_item", "extra_limb", "missing_limb", "wrong_facing", "wrong_pose",
               "style_drift", "color_drift", "stray_marks", "other"]

SCHEMA = {
    "type": "object",
    "properties": {
        "frames": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "frame": {"type": "integer"},
                "issue_type": {"type": "string", "enum": ISSUE_TYPES},
                "severity": {"type": "integer", "enum": [1, 2, 3]},
                "description": {"type": "string"},
            },
            "required": ["frame", "issue_type", "severity", "description"],
            "additionalProperties": False,
        }},
        "summary": {"type": "string"},
    },
    "required": ["frames", "summary"],
    "additionalProperties": False,
}

# which CV metrics corroborate which judge issue types
CORROBORATES = {"wrong_facing": {"facing"}, "wrong_pose": {"pose", "gait", "registration", "effect_size"},
                "missing_item": {"identity", "pose"}, "changed_item": {"identity"}, "color_drift": {"identity"},
                "style_drift": {"identity", "sharpness"}, "extra_limb": {"pose", "identity"},
                "missing_limb": {"pose", "identity", "clipping"}, "stray_marks": {"extraneous_marks"}}
REMEDY = {"missing_item": "identity_fix", "changed_item": "identity_fix", "color_drift": "identity_fix",
          "style_drift": "identity_fix", "wrong_pose": "pose_fix", "extra_limb": "pose_fix",
          "missing_limb": "pose_fix", "wrong_facing": "frame_repair", "stray_marks": "frame_repair",
          "other": "frame_repair"}


def contact_sheet(frames: list[np.ndarray], max_h: int = 384) -> bytes:
    """Aligned frames in a row with large indices drawn in the gutters (set-of-mark)."""
    h, w = frames[0].shape[:2]
    s = min(1.0, max_h / h)
    tw, th = max(1, int(w * s)), max(1, int(h * s))
    gutter = max(36, th // 8)
    W = len(frames) * (tw + gutter) + gutter
    out = Image.new("RGB", (W, th + 2 * gutter), (255, 255, 255))
    d = ImageDraw.Draw(out)
    font = _font(gutter - 6)
    for i, f in enumerate(frames):
        tile = on_checker(f).resize((tw, th), Image.Resampling.LANCZOS)
        x = gutter + i * (tw + gutter)
        out.paste(tile, (x, gutter))
        d.text((x + tw // 2 - gutter // 4, 2), str(i + 1), fill=(200, 0, 0), font=font)
    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue()


async def judge(hub: ProviderHub, frames: list[np.ndarray], reference: np.ndarray | None, action: str, *,
                effort: str = "xhigh", job: str | None = None, disabled: set[str] | None = None,
                kind: str = "character", lines: list[str] | None = None) -> tuple[list[Finding], dict]:
    if not frames:
        return [], {}
    prompt = prompts.judge(len(frames), action, kind, lines)
    ref = reference if reference is not None else frames[0]
    buf = io.BytesIO()
    on_checker(ref).save(buf, format="PNG")
    model = registry.model_for("judge")
    req = ProviderRequest(op="respond", model=model, prompt=prompt.text, images=[buf.getvalue(), contact_sheet(frames)],
                          schema=SCHEMA, params={"reasoning_effort": effort, "schema_name": "judge",
                                                 "frames": len(frames)}, purpose="judge")
    cands = await hub.call(req, job=job)
    data = json.loads(cands[0].data)
    out = []
    for item in data.get("frames", []):
        kind = item.get("issue_type", "other")
        if disabled and kind in disabled:
            continue
        idx = int(item.get("frame", 0)) - 1
        if not (0 <= idx < len(frames)):
            continue
        sev = int(item.get("severity", 1))
        out.append(Finding(metric="semantics", level="fail" if sev >= 3 else "warn", severity=sev, frames=[idx],
                           message=f"judge: {kind.replace('_', ' ')} — {item.get('description', '')}"[:300],
                           remedy=REMEDY.get(kind, "frame_repair"), source="judge", issue=kind))
    return out, {"summary": data.get("summary", ""), "raw": data, **prompt.provenance()}


def corroborate(judge_findings: list[Finding], cv_findings: list[Finding]) -> None:
    for jf in judge_findings:
        kind = jf.issue or "other"
        metrics = CORROBORATES.get(kind, set())
        for cf in cv_findings:
            if cf.source == "cv" and cf.metric in metrics and (not cf.frames or set(cf.frames) & set(jf.frames)):
                jf.corroborated = True
                # two independent moderate signals on the same frame make a failure (K16)
                if jf.severity >= 2 and cf.severity >= 2:
                    jf.level = "fail"
                break


def calibrate(labels: list[dict], min_labels: int = 5, min_precision: float = 0.8) -> dict:
    """Precision per judge issue type from hand labels; types below 0.8 are switched off (14.2)."""
    per: dict[str, list[bool]] = {}
    for l in labels:
        if l.get("source") == "judge" and l.get("issue"):
            per.setdefault(l["issue"], []).append(bool(l["correct"]))
    stats = {k: {"labels": len(v), "precision": round(sum(v) / len(v), 3)} for k, v in per.items()}
    disabled = sorted(k for k, s in stats.items() if s["labels"] >= min_labels and s["precision"] < min_precision)
    return {"issues": stats, "disabled": disabled}
