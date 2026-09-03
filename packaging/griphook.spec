# PyInstaller spec for the Windows build.
#
#   uv run pyinstaller packaging/griphook.spec --noconfirm
#
# Produces dist/griphook/griphook.exe (onedir: instant startup, unlike onefile,
# which unpacks itself to a temp folder on every invocation — a real cost for a
# CLI an agent calls repeatedly).

import os

from PyInstaller.utils.hooks import collect_submodules

# SPECPATH is injected by PyInstaller; paths below are relative to this file so
# the build works no matter which directory it is launched from.
HERE = SPECPATH  # noqa: F821
ROOT = os.path.dirname(HERE)

hiddenimports = [
    "pyodbc",
    # sqlglot resolves dialects by name at runtime, so static analysis misses them.
    *collect_submodules("sqlglot.dialects"),
]

a = Analysis(
    [os.path.join(HERE, "launcher.py")],
    pathex=[os.path.join(ROOT, "src")],
    binaries=[],
    datas=[],
    hiddenimports=hiddenimports,
    hookspath=[],
    runtime_hooks=[],
    excludes=["tkinter", "pytest", "IPython"],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="griphook",
    console=True,
    disable_windowed_traceback=False,
    upx=False,
    version=os.path.join(HERE, "version_info.txt"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="griphook",
)
