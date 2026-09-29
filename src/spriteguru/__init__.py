"""SpriteGuru engine: character description to game-ready animation assets."""

import os as _os

# RT1: onnxruntime starts its telemetry (a 1DS uploader to Microsoft) when it is imported. Its worker
# thread can abort the process at exit, and SpriteGuru talks only to the user's own providers. The
# switch is read at import, so it is set here, before any module can import onnxruntime.
_os.environ.setdefault("ORT_DISABLE_TELEMETRY", "1")

__version__ = "0.1.0"
