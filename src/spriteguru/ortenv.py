"""onnxruntime set-up shared by every model session (embeddings, the ML matte through rembg).

RT1: onnxruntime's bundled telemetry uploads over HTTP from a background thread; a response landing
while the process exits aborts it (libc++abi recursive_mutex), and SpritePlay must not contact
anyone but the user's own providers. Telemetry is switched off before the first session exists."""

from __future__ import annotations

_done = False


def prepare() -> None:
    global _done
    if _done:
        return
    import onnxruntime as ort

    try:
        ort.disable_telemetry_events()
    except Exception:  # an older build without the switch has no telemetry to disable
        pass
    _done = True
