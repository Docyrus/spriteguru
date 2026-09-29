# PyInstaller spec for the engine (rule 4: separate from the launcher, so the CLI, the tests and
# the app all run the same engine binary). Build: uv run pyinstaller packaging/engine.spec
from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

datas, binaries, hidden = [], [], []
for pkg in ("spriteguru", "uvicorn", "fastapi", "starlette", "onnxruntime", "rembg", "skimage", "av", "numba",
            "llvmlite", "playwright", "fal_client", "openai", "keyring", "pymatting"):
    d, b, h = collect_all(pkg)
    # __pycache__ holds the build machine's numba JIT caches, which are tied to its paths and CPU
    datas += [(src, dst) for src, dst in d if "__pycache__" not in src.replace("\\", "/").split("/")]
    binaries += b
    hidden += h
hidden += collect_submodules("uvicorn") + ["keyring.backends.macOS", "keyring.backends.Windows",
                                           "keyring.backends.SecretService"]

a = Analysis(["engine_entry.py"], pathex=[], binaries=binaries, datas=datas, hiddenimports=hidden,
             excludes=["webview", "tkinter", "PyQt5", "PyQt6", "PySide6"], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="spriteguru-engine", console=True)
coll = COLLECT(exe, a.binaries, a.datas, name="spriteguru-engine")
