"""Identity and flip embeddings: DINOv2-small on ONNX Runtime's CPU provider (11.5, 14.1).

The model downloads once to ~/.cache/spriteguru/models. Without it (offline first run), a
deterministic edge-orientation descriptor stands in, and `status()` reports the fallback.
"""

from __future__ import annotations

import os
import threading
import urllib.request
from pathlib import Path

import cv2
import numpy as np

from .env import get as app_env, present as app_env_present

MODEL_URL = "https://huggingface.co/onnx-community/dinov2-small/resolve/main/onnx/model.onnx"
MODEL_DIR = Path(app_env("MODELS", str(Path.home() / ".cache" / "spriteguru" / "models")))
MODEL_PATH = MODEL_DIR / "dinov2-small.onnx"
_MEAN = np.array([0.485, 0.456, 0.406], np.float32)
_STD = np.array([0.229, 0.224, 0.225], np.float32)
_lock = threading.Lock()
_session = None
_failed = False


def status() -> dict:
    return {"model": "dinov2-small", "path": str(MODEL_PATH), "present": MODEL_PATH.is_file(),
            "backend": "onnx" if MODEL_PATH.is_file() and not _failed else "edge-descriptor"}


def ensure_model(download: bool = True) -> bool:
    if MODEL_PATH.is_file():
        return True
    if not download or app_env_present("OFFLINE"):
        return False
    try:
        MODEL_DIR.mkdir(parents=True, exist_ok=True)
        tmp = MODEL_PATH.with_suffix(".part")
        urllib.request.urlretrieve(MODEL_URL, tmp)
        tmp.replace(MODEL_PATH)
        return True
    except Exception:
        return False


def _get_session():
    global _session, _failed
    with _lock:
        if _session is None and not _failed:
            if not ensure_model():
                _failed = True
                return None
            try:
                import onnxruntime as ort

                from .ortenv import prepare

                prepare()
                opts = ort.SessionOptions()
                opts.intra_op_num_threads = 2
                _session = ort.InferenceSession(str(MODEL_PATH), opts, providers=["CPUExecutionProvider"])
            except Exception:
                _failed = True
        return _session


def _prep(rgba: np.ndarray, size: int = 224) -> np.ndarray:
    """Composite on mid grey, pad to square, resize, normalize (NCHW)."""
    a = rgba[..., 3:4].astype(np.float32) / 255.0
    rgb = rgba[..., :3].astype(np.float32) / 255.0 * a + 0.5 * (1 - a)
    h, w = rgb.shape[:2]
    s = max(h, w)
    canvas = np.full((s, s, 3), 0.5, np.float32)
    canvas[(s - h) // 2:(s - h) // 2 + h, (s - w) // 2:(s - w) // 2 + w] = rgb
    img = cv2.resize(canvas, (size, size), interpolation=cv2.INTER_AREA)
    img = (img - _MEAN) / _STD
    return img.transpose(2, 0, 1)[None].astype(np.float32)


def _edge_descriptor(rgba: np.ndarray) -> np.ndarray:
    x = _prep(rgba, 128)[0].transpose(1, 2, 0).mean(-1)
    gx = cv2.Sobel(x, cv2.CV_32F, 1, 0, ksize=3)
    gy = cv2.Sobel(x, cv2.CV_32F, 0, 1, ksize=3)
    mag = np.hypot(gx, gy)
    ang = (np.arctan2(gy, gx) + np.pi) / (2 * np.pi) * 8
    desc = []
    for by in range(4):
        for bx in range(4):
            sl = (slice(by * 32, by * 32 + 32), slice(bx * 32, bx * 32 + 32))
            hist = np.bincount(np.clip(ang[sl].astype(int), 0, 7).ravel(), weights=mag[sl].ravel(), minlength=8)
            desc.append(hist)
    d = np.concatenate(desc)
    return d / (np.linalg.norm(d) + 1e-9)


def _tokens(rgba: np.ndarray) -> np.ndarray | None:
    sess = _get_session()
    if sess is None:
        return None
    out = sess.run(None, {sess.get_inputs()[0].name: _prep(rgba)})[0]
    return out[0] if out.ndim == 3 else None


def embed(rgba: np.ndarray) -> np.ndarray:
    """Pose-tolerant identity embedding (CLS token)."""
    tok = _tokens(rgba)
    if tok is None:
        return _edge_descriptor(rgba)
    v = tok[0]
    return v / (np.linalg.norm(v) + 1e-9)


def embed_spatial(rgba: np.ndarray, grid: int = 4) -> np.ndarray:
    """Layout-aware embedding for facing checks: patch tokens pooled on a coarse grid, so a mirror
    image lands far from the original even when the CLS token barely changes."""
    tok = _tokens(rgba)
    if tok is None:
        return _edge_descriptor(rgba)
    patches = tok[1:]
    side = int(round(np.sqrt(len(patches))))
    p = patches[: side * side].reshape(side, side, -1)
    step = side // grid
    pooled = p[: step * grid, : step * grid].reshape(grid, step, grid, step, -1).mean((1, 3))
    v = pooled.reshape(-1)
    return v / (np.linalg.norm(v) + 1e-9)


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-9))
