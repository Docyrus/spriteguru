"""Re-parse every export format and check it against the sheet it describes (E1–E4)."""

from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
from PIL import Image


def _rects_ok(rects, size, n) -> list[str]:
    errs = []
    W, H = size
    if len(rects) != n:
        errs.append(f"{len(rects)} rects for {n} frames")
    for x, y, w, h in rects:
        if x < 1 or y < 1 or x + w > W - 1 or y + h > H - 1:
            errs.append(f"rect {(x, y, w, h)} touches the sheet edge {size} (no padding)")
    for i, a in enumerate(rects):
        for b in rects[i + 1:]:
            if not (a[0] + a[2] + 2 <= b[0] or b[0] + b[2] + 2 <= a[0] or a[1] + a[3] + 2 <= b[1] or
                    b[1] + b[3] + 2 <= a[1]):
                errs.append(f"rects {a} and {b} are closer than the 2 px padding")
    return errs


def check_final(final: Path, engine: str) -> dict:
    meta = json.loads((final / "animation.json").read_text())
    sheet = np.asarray(Image.open(final / "sheet.png").convert("RGBA"))
    H, W = sheet.shape[:2]
    n = meta["frames"]
    rects = [tuple(r) for r in meta["rects"]]
    errs = _rects_ok(rects, (W, H), n) if engine != "gamemaker" or True else []
    # E1: 1 px extrusion — the ring around each frame repeats the frame's edge pixels
    for x, y, w, h in rects:
        if not (np.array_equal(sheet[y - 1, x:x + w], sheet[y, x:x + w]) and
                np.array_equal(sheet[y:y + h, x - 1], sheet[y:y + h, x]) and
                np.array_equal(sheet[y + h, x:x + w], sheet[y + h - 1, x:x + w]) and
                np.array_equal(sheet[y:y + h, x + w], sheet[y:y + h, x + w - 1])):
            errs.append(f"frame at {(x, y)} lacks 1 px edge extrusion")
            break
    # frames on disk match their rects in the sheet
    frames = sorted((final / "frames").glob("*.png"))
    if len(frames) != n:
        errs.append(f"{len(frames)} frame files for {n} frames")
    for f, (x, y, w, h) in zip(frames, rects):
        fa = np.asarray(Image.open(f).convert("RGBA"))
        if fa.shape[:2] != (h, w) or not np.array_equal(fa, sheet[y:y + h, x:x + w]):
            errs.append(f"{f.name} differs from its sheet rect")
            break
    ase = json.loads((final / "sheet.aseprite.json").read_text())
    if [fr["duration"] for fr in ase["frames"]] != meta["durations"]:
        errs.append("aseprite durations disagree with animation.json")
    if [(fr["frame"]["x"], fr["frame"]["y"], fr["frame"]["w"], fr["frame"]["h"]) for fr in ase["frames"]] != rects:
        errs.append("aseprite frame rects disagree")
    if ase["meta"]["size"] != {"w": W, "h": H}:
        errs.append("aseprite sheet size disagrees")
    if not ase["meta"]["frameTags"] or ase["meta"]["frameTags"][0]["to"] != n - 1:
        errs.append("aseprite frameTags wrong")
    if engine in ("phaser", "pixi"):
        tp = json.loads((final / "sheet.json").read_text())
        fr = list(tp["frames"].values())
        if [(f["frame"]["x"], f["frame"]["y"], f["frame"]["w"], f["frame"]["h"]) for f in fr] != rects:
            errs.append("TexturePacker rects disagree")
        if len(tp["animations"][meta["action"]]) != n:
            errs.append("TexturePacker animation length wrong")
        if abs(fr[0]["pivot"]["x"] - meta["pivot"][0]) > 1e-3:
            errs.append("TexturePacker pivot disagrees")
        if engine == "phaser":
            an = json.loads((final / "anims.json").read_text())["anims"][0]
            if len(an["frames"]) != n or an["repeat"] != (-1 if meta["loop"] else 0):
                errs.append("Phaser anims wrong")
    elif engine == "godot":
        tres = next(final.glob("*.tres")).read_text()
        regions = [tuple(int(v) for v in m) for m in re.findall(r"region = Rect2\((\d+), (\d+), (\d+), (\d+)\)", tres)]
        if regions != rects:
            errs.append("Godot AtlasTexture regions disagree")
        if f'"loop": {"true" if meta["loop"] else "false"}' not in tres:
            errs.append("Godot loop flag wrong")
        durs = [float(v) for v in re.findall(r'"duration": ([\d.]+)', tres)]
        base = min(meta["durations"])
        if [round(d * base) for d in durs] != meta["durations"]:
            errs.append("Godot durations disagree")
    elif engine == "unity":
        u = json.loads((final / "sheet.spriteguru.json").read_text())
        if [(f["frame"]["x"], f["frame"]["y"], f["frame"]["w"], f["frame"]["h"]) for f in u["frames"]] != rects:
            errs.append("Unity rects disagree")
        if "class SpriteKitImporter : AssetPostprocessor" not in (final / "SpriteKitImporter.cs").read_text():
            errs.append("Unity importer script missing")
    elif engine == "gamemaker":
        strips = list(final.glob("*_strip*.png"))
        if len(strips) != 1:
            errs.append("GameMaker strip missing")
        else:
            m = re.search(r"_strip(\d+)\.png$", strips[0].name)
            st = Image.open(strips[0])
            fw, fh = meta["size"]
            if not m or int(m.group(1)) != n or st.size != (fw * n, fh):
                errs.append(f"GameMaker strip {strips[0].name} {st.size} does not slice into {n} frames")
    if meta.get("pixel"):
        s4 = np.asarray(Image.open(final / "sheet@4x.png").convert("RGBA"))
        x, y, w, h = rects[0]
        a4 = json.loads((final / "sheet@4x.aseprite.json").read_text())["frames"][0]["frame"]
        f4 = s4[a4["y"]:a4["y"] + a4["h"], a4["x"]:a4["x"] + a4["w"]]
        f1 = sheet[y:y + h, x:x + w]
        if not np.array_equal(f4[::4, ::4], f1):  # E3: nearest-neighbour 4x copy
            errs.append("4x copy is not a nearest-neighbour upscale")
        gif = Image.open(final / "preview.gif")
        pal = gif.getpalette() or []
        colors = {tuple(pal[i:i + 3]) for i in range(0, len(pal), 3)}
        uniq = len(np.unique(f1[f1[..., 3] > 0][:, :3], axis=0))
        if uniq > 64:
            errs.append(f"pixel frame has {uniq} colours after palette unification")
    zipped = (final / "export.zip").is_file()
    if not zipped:
        errs.append("export.zip missing")
    return {"ok": not errs, "errors": errs, "frames": n, "size": [W, H]}
