"""Score from findings (14.3): Q = clamp(100 − Σ w_f s_f, 0, 100); any fail blocks auto-accept."""

from __future__ import annotations

from ..spec import Finding

WEIGHTS = {
    "frame_count": 30, "layout_confidence": 4, "clipping": 10, "extraneous_marks": 2, "background": 8,
    "key_contamination": 8, "scale": 6, "baseline": 4, "facing": 8, "identity": 10, "pose": 7, "order": 3,
    "duplicates": 8, "gaps": 8, "loop_seam": 12, "gait": 25, "idle_motion": 6, "sharpness": 4,
    "video_compression": 4, "pixel_fidelity": 6, "semantics": 10, "vector_rig": 20, "motion_spec": 20,
    "registration": 3, "frames_touching": 4, "palette": 3,
}
ACCEPT_Q = 85.0


def finding_cost(f: Finding) -> float:
    if f.level == "info":
        return 0.0
    w = WEIGHTS.get(f.metric, 5)
    if f.source == "judge" and not (f.corroborated or f.severity >= 3):
        w *= 0.25  # advisory unless corroborated or severity 3
    if f.auto_fixed:
        w *= 0.25
    count = max(1, len(f.frames)) if f.frames else 1
    return w * f.severity * (1 + 0.25 * (count - 1))


def score(findings: list[Finding]) -> tuple[float, bool]:
    q = 100.0 - sum(finding_cost(f) for f in findings)
    q = max(0.0, min(100.0, q))
    blocking = any(f.level == "fail" and not f.auto_fixed and (f.source == "cv" or f.corroborated or f.severity >= 3)
                   for f in findings)
    return round(q, 2), (q >= ACCEPT_Q and not blocking)


def top_findings(findings: list[Finding], k: int = 3) -> list[Finding]:
    return sorted(findings, key=finding_cost, reverse=True)[:k]
