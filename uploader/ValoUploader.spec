# PyInstaller spec: one-file, windowed ValoUploader.exe.
# valo_uploader/__main__.py uses a relative import, which fails when PyInstaller runs
# it as a top-level script. So the spec writes a two-line launcher into build/ (git-ignored)
# that imports the package normally; the package entry (__main__ -> main.main) is unchanged.
import os

root = os.path.abspath(SPECPATH)
build_dir = os.path.join(root, "build")
os.makedirs(build_dir, exist_ok=True)
launcher = os.path.join(build_dir, "_entry.py")
with open(launcher, "w", encoding="utf-8") as f:
    f.write("from valo_uploader.main import main\nraise SystemExit(main())\n")

a = Analysis(
    [launcher],
    pathex=[root],
    binaries=[],
    datas=[],
    hiddenimports=["pystray._win32", "PIL._tkinter_finder"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="ValoUploader",
    debug=False,
    strip=False,
    upx=False,
    console=False,
    runtime_tmpdir=None,
)
