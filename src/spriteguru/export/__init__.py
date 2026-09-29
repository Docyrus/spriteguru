"""Export (section 15): one canonical frame set written as a uniform grid sheet plus engine formats.

Frames sit in equal cells with 2 px padding and 1 px edge extrusion so bilinear filtering never
bleeds between neighbours. Pixel art ships at 1× plus a 4× nearest-neighbour copy.
"""

from __future__ import annotations

import json
import math
import shutil
import zipfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from .. import __version__
from ..report import gif_bytes

PAD = 2


@dataclass
class FrameSet:
    name: str  # animation id, e.g. knight-walk-E
    action: str
    frames: list[np.ndarray]  # RGBA, equal size
    durations: list[int]  # ms
    pivot: tuple[float, float]  # normalized
    loop: bool
    fps: int
    pixel: bool = False
    root_motion: list[float] | None = None
    blend: str = "normal"  # "add": draw with additive blending (glowing effects on black, K8)

    @property
    def size(self) -> tuple[int, int]:
        h, w = self.frames[0].shape[:2]
        return w, h


def grid_cols(n: int) -> int:
    if n <= 8:
        return n
    rows = math.ceil(n / 8)
    return math.ceil(n / rows)


def pack(fs: FrameSet, scale: int = 1, cols: int | None = None) -> tuple[Image.Image, list[tuple[int, int, int, int]]]:
    """Uniform grid with padding and 1 px edge extrusion; returns sheet and frame rects (x, y, w, h)."""
    w, h = fs.size
    w, h = w * scale, h * scale
    n = len(fs.frames)
    cols = cols or grid_cols(n)
    rows = math.ceil(n / cols)
    cw, ch = w + 2 * PAD, h + 2 * PAD
    sheet = np.zeros((rows * ch, cols * cw, 4), np.uint8)
    rects = []
    for i, f in enumerate(fs.frames):
        if scale != 1:
            f = np.asarray(Image.fromarray(f, "RGBA").resize((w, h), Image.Resampling.NEAREST))
        r, c = divmod(i, cols)
        x, y = c * cw + PAD, r * ch + PAD
        sheet[y:y + h, x:x + w] = f
        # 1 px extrusion into the padding
        sheet[y - 1, x:x + w] = f[0]
        sheet[y + h, x:x + w] = f[-1]
        sheet[y:y + h, x - 1] = f[:, 0]
        sheet[y:y + h, x + w] = f[:, -1]
        sheet[y - 1, x - 1], sheet[y - 1, x + w] = f[0, 0], f[0, -1]
        sheet[y + h, x - 1], sheet[y + h, x + w] = f[-1, 0], f[-1, -1]
        rects.append((x, y, w, h))
    return Image.fromarray(sheet, "RGBA"), rects


def aseprite_json(fs: FrameSet, rects, image: str, size: tuple[int, int]) -> dict:
    w, h = fs.size if rects and rects[0][2] == fs.size[0] else (rects[0][2], rects[0][3])
    return {
        "frames": [{"filename": f"{fs.name} {i}.aseprite", "frame": {"x": x, "y": y, "w": rw, "h": rh},
                    "rotated": False, "trimmed": False, "spriteSourceSize": {"x": 0, "y": 0, "w": rw, "h": rh},
                    "sourceSize": {"w": rw, "h": rh}, "duration": int(d)}
                   for i, ((x, y, rw, rh), d) in enumerate(zip(rects, fs.durations))],
        "meta": {"app": "https://github.com/Docyrus/spriteguru", "version": __version__, "image": image, "format": "RGBA8888",
                 "size": {"w": size[0], "h": size[1]}, "scale": "1",
                 "frameTags": [{"name": fs.action, "from": 0, "to": len(rects) - 1,
                                "direction": "forward", **({} if fs.loop else {"repeat": "1"})}],
                 "layers": [{"name": "sprite", "opacity": 255, "blendMode": "normal"}],
                 "slices": [{"name": "pivot", "color": "#0000ffff", "keys": [
                     {"frame": 0, "bounds": {"x": 0, "y": 0, "w": w, "h": h},
                      "pivot": {"x": round(fs.pivot[0] * w), "y": round(fs.pivot[1] * h)}}]}]},
    }


def texturepacker_json(fs: FrameSet, rects, image: str, size: tuple[int, int]) -> dict:
    frames = {}
    names = []
    for i, (x, y, w, h) in enumerate(rects):
        name = f"{fs.name}_{i:03d}"
        names.append(name)
        frames[name] = {"frame": {"x": x, "y": y, "w": w, "h": h}, "rotated": False, "trimmed": False,
                        "spriteSourceSize": {"x": 0, "y": 0, "w": w, "h": h}, "sourceSize": {"w": w, "h": h},
                        "pivot": {"x": round(fs.pivot[0], 4), "y": round(fs.pivot[1], 4)},
                        "anchor": {"x": round(fs.pivot[0], 4), "y": round(fs.pivot[1], 4)},
                        "duration": fs.durations[i]}
    meta = {"app": "spriteguru", "version": __version__, "image": image, "format": "RGBA8888",
            "size": {"w": size[0], "h": size[1]}, "scale": "1", "loop": fs.loop,
            "blendMode": "ADD" if fs.blend == "add" else "NORMAL"}
    if fs.root_motion:
        meta["root_motion"] = fs.root_motion
    return {"frames": frames, "animations": {fs.action: names}, "meta": meta}


def phaser_anims(fs: FrameSet, atlas_key: str) -> dict:
    names = [f"{fs.name}_{i:03d}" for i in range(len(fs.frames))]
    base = 1000 / max(1, fs.fps)
    return {"blendMode": "ADD" if fs.blend == "add" else "NORMAL",
            "anims": [{"key": fs.name, "frameRate": fs.fps, "repeat": -1 if fs.loop else 0,
                       "frames": [{"key": atlas_key, "frame": n,
                                   **({"duration": round(d - base)} if abs(d - base) > 1 else {})}
                                  for n, d in zip(names, fs.durations)]}]}


def godot_tres(fs: FrameSet, rects, texture_path: str) -> str:
    base = min(fs.durations) if fs.durations else 83
    speed = 1000.0 / base
    lines = [f'[gd_resource type="SpriteFrames" load_steps={len(rects) + 2} format=3]', "",
             *(["; additive effect: give the AnimatedSprite2D a CanvasItemMaterial with blend_mode = Add", ""]
               if fs.blend == "add" else []),
             f'[ext_resource type="Texture2D" path="{texture_path}" id="1_sheet"]', ""]
    for i, (x, y, w, h) in enumerate(rects):
        lines += [f'[sub_resource type="AtlasTexture" id="AtlasTexture_{i}"]', 'atlas = ExtResource("1_sheet")',
                  f"region = Rect2({x}, {y}, {w}, {h})", ""]
    frames = ", ".join('{\n"duration": %.4f,\n"texture": SubResource("AtlasTexture_%d")\n}' % (d / base, i)
                       for i, d in enumerate(fs.durations))
    lines += ["[resource]", "animations = [{", f'"frames": [{frames}],', f'"loop": {"true" if fs.loop else "false"},',
              f'"name": &"{fs.action}",', f'"speed": {speed:.4f}', "}]", ""]
    return "\n".join(lines)


UNITY_IMPORTER = r'''// Generated by spriteguru. Place in an Editor folder. Slices sheets that have a sibling
// <name>.spriteguru.json, sets pivots, and builds an AnimationClip next to the texture.
#if UNITY_EDITOR
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEngine;

public class SpriteKitImporter : AssetPostprocessor
{
    [System.Serializable] class Rect { public int x, y, w, h; }
    [System.Serializable] class FrameDef { public string name; public Rect frame; public int duration; }
    [System.Serializable] class Meta { public int width, height; public float pivotX, pivotY; public bool loop; public string action; }
    [System.Serializable] class Def { public List<FrameDef> frames; public Meta meta; }

    static Def Load(string texPath)
    {
        var json = Path.ChangeExtension(texPath, null) + ".spriteguru.json";
        return File.Exists(json) ? JsonUtility.FromJson<Def>(File.ReadAllText(json)) : null;
    }

    void OnPreprocessTexture()
    {
        var def = Load(assetPath);
        if (def == null) return;
        var ti = (TextureImporter)assetImporter;
        ti.textureType = TextureImporterType.Sprite;
        ti.spriteImportMode = SpriteImportMode.Multiple;
        ti.mipmapEnabled = false;
        var metas = new List<SpriteMetaData>();
        foreach (var f in def.frames)
        {
            metas.Add(new SpriteMetaData {
                name = f.name,
                rect = new UnityEngine.Rect(f.frame.x, def.meta.height - f.frame.y - f.frame.h, f.frame.w, f.frame.h),
                alignment = (int)SpriteAlignment.Custom,
                pivot = new Vector2(def.meta.pivotX, 1f - def.meta.pivotY)
            });
        }
#pragma warning disable 618
        ti.spritesheet = metas.ToArray();
#pragma warning restore 618
    }

    static void OnPostprocessAllAssets(string[] imported, string[] deleted, string[] moved, string[] movedFrom)
    {
        foreach (var path in imported)
        {
            var def = Load(path);
            if (def == null) continue;
            var sprites = new Dictionary<string, Sprite>();
            foreach (var o in AssetDatabase.LoadAllAssetsAtPath(path))
                if (o is Sprite s) sprites[s.name] = s;
            var clip = new AnimationClip { frameRate = 60 };
            var binding = new EditorCurveBinding { type = typeof(SpriteRenderer), path = "", propertyName = "m_Sprite" };
            var keys = new List<ObjectReferenceKeyframe>();
            float t = 0;
            foreach (var f in def.frames)
            {
                if (!sprites.TryGetValue(f.name, out var sp)) continue;
                keys.Add(new ObjectReferenceKeyframe { time = t, value = sp });
                t += f.duration / 1000f;
            }
            if (keys.Count > 0) keys.Add(new ObjectReferenceKeyframe { time = t, value = keys[keys.Count - 1].value });
            AnimationUtility.SetObjectReferenceCurve(clip, binding, keys.ToArray());
            var settings = AnimationUtility.GetAnimationClipSettings(clip);
            settings.loopTime = def.meta.loop;
            AnimationUtility.SetAnimationClipSettings(clip, settings);
            AssetDatabase.CreateAsset(clip, Path.ChangeExtension(path, null) + ".anim");
        }
    }
}
#endif
'''


def unity_json(fs: FrameSet, rects, size) -> dict:
    return {"frames": [{"name": f"{fs.name}_{i:03d}", "frame": {"x": x, "y": y, "w": w, "h": h},
                        "duration": fs.durations[i]} for i, (x, y, w, h) in enumerate(rects)],
            "meta": {"width": size[0], "height": size[1], "pivotX": fs.pivot[0], "pivotY": fs.pivot[1],
                     "loop": fs.loop, "action": fs.action}}


def _godot_res_path(target: Path, file: Path) -> str:
    for d in (target, *target.parents):
        if (d / "project.godot").is_file():
            return "res://" + file.relative_to(d).as_posix()
    return file.name


def export(fs: FrameSet, out_dir: Path, engine: str, *, asset_folder: Path | None = None,
           report: dict | None = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = out_dir / "frames"
    if frames_dir.exists():
        shutil.rmtree(frames_dir)
    frames_dir.mkdir()
    files: list[str] = []

    def save_img(img: Image.Image, name: str):
        img.save(out_dir / name)
        files.append(name)

    def save_text(text: str, name: str):
        (out_dir / name).write_text(text)
        files.append(name)

    for i, f in enumerate(fs.frames):
        Image.fromarray(f, "RGBA").save(frames_dir / f"{i:03d}.png")
        files.append(f"frames/{i:03d}.png")
    cols = len(fs.frames) if engine == "gamemaker" else None
    sheet, rects = pack(fs, cols=cols)
    save_img(sheet, "sheet.png")
    size = sheet.size
    save_text(json.dumps(aseprite_json(fs, rects, "sheet.png", size), indent=2), "sheet.aseprite.json")
    if fs.pixel:
        sheet4, rects4 = pack(fs, scale=4, cols=cols)
        save_img(sheet4, "sheet@4x.png")
        save_text(json.dumps(aseprite_json(fs, rects4, "sheet@4x.png", sheet4.size), indent=2),
                  "sheet@4x.aseprite.json")
    gif = gif_bytes(fs.frames, fs.durations, pixel=fs.pixel, scale=4 if fs.pixel and fs.size[1] < 128 else 1,
                    blend=fs.blend)
    (out_dir / "preview.gif").write_bytes(gif)
    files.append("preview.gif")

    if engine in ("phaser", "pixi"):
        save_text(json.dumps(texturepacker_json(fs, rects, "sheet.png", size), indent=2), "sheet.json")
        if engine == "phaser":
            save_text(json.dumps(phaser_anims(fs, fs.name), indent=2), "anims.json")
    elif engine == "godot":
        tex = _godot_res_path(asset_folder or out_dir, (asset_folder or out_dir) / "sheet.png")
        save_text(godot_tres(fs, rects, tex), f"{fs.name}.tres")
    elif engine == "unity":
        save_text(json.dumps(unity_json(fs, rects, size), indent=2), "sheet.spriteguru.json")
        save_text(UNITY_IMPORTER, "SpriteKitImporter.cs")
    elif engine == "gamemaker":
        strip = f"{fs.name.replace('-', '_')}_strip{len(fs.frames)}.png"
        # GameMaker slices strips by equal division: no padding
        raw = np.concatenate(fs.frames, axis=1)
        Image.fromarray(raw, "RGBA").save(out_dir / strip)
        files.append(strip)
    meta = {"name": fs.name, "action": fs.action, "frames": len(fs.frames), "size": list(fs.size),
            "pivot": list(fs.pivot), "durations": fs.durations, "loop": fs.loop, "fps": fs.fps, "engine": engine,
            "sheet": "sheet.png", "rects": [list(r) for r in rects], "padding": PAD, "extrude": 1,
            "pixel": fs.pixel, "root_motion": fs.root_motion, "blend": fs.blend}
    if engine == "gamemaker":
        w, h = fs.size
        meta["strip"] = {"file": strip, "rects": [[i * w, 0, w, h] for i in range(len(fs.frames))], "padding": 0}
    save_text(json.dumps(meta, indent=2), "animation.json")
    if report is not None:
        save_text(json.dumps(report, indent=2), "report.json")
    bundle = out_dir / "export.zip"
    with zipfile.ZipFile(bundle, "w", zipfile.ZIP_DEFLATED) as z:
        for f in files:
            z.write(out_dir / f, f)
    copied = None
    if asset_folder:
        dest = Path(asset_folder) / fs.name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(out_dir, dest, ignore=shutil.ignore_patterns("export.zip"))
        copied = str(dest)
    return {"dir": str(out_dir), "files": files, "bundle": str(bundle), "copied_to": copied, "meta": meta}
