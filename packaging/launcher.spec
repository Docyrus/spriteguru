# PyInstaller spec for the launcher window. It contains no engine code paths beyond starting the
# engine binary (built by engine.spec) that sits next to it. Build the engine first, then:
#   uv run pyinstaller packaging/launcher.spec
# and copy dist/spriteguru-engine into engine/ next to the app executable (build_app.sh does this).
import os
import sys
from importlib.metadata import version

from PyInstaller.utils.hooks import collect_all

VERSION = version("spriteguru")
MIN_MACOS = "14.0"  # the bundled scipy wheels are built for macOS 14

brand = os.path.join(SPECPATH, "..", "brand")
datas, binaries, hidden = [], [], []
d, b, h = collect_all("webview")
datas += d
# the window icon for builds without a bundle icon (spriteguru.launcher.app_icon)
datas += [(os.path.join(brand, "png", n), "brand/png") for n in ("app-icon-macos-512.png", "mark-256.png")]
binaries += b
hidden += h

a = Analysis(["launcher_entry.py"], pathex=[], binaries=binaries, datas=datas, hiddenimports=hidden + [
             "spriteguru.launcher", "spriteguru.project", "spriteguru.spec"],
             excludes=["numpy", "cv2", "scipy", "numba", "onnxruntime", "torch", "skimage"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="SpriteGuru", console=False,
          icon=os.path.join(brand, "SpriteGuru.ico" if sys.platform == "win32" else "SpriteGuru.icns"))
coll = COLLECT(exe, a.binaries, a.datas, name="SpriteGuru")
app = BUNDLE(coll, name="SpriteGuru.app", bundle_identifier="com.spriteguru.studio",
             icon=os.path.join(brand, "SpriteGuru.icns"), version=VERSION,
             info_plist={"CFBundleVersion": VERSION, "LSMinimumSystemVersion": MIN_MACOS,
                         "NSHighResolutionCapable": True})
