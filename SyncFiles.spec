# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for SyncFiles (single-file, with bundled adb).

Produces a single ``dist/SyncFiles.exe`` that contains the Python runtime,
Tcl/Tk, the syncfiles package, and ``adb.exe`` plus its required DLLs.
At runtime PyInstaller unpacks everything to a temp dir and exposes it via
``sys._MEIPASS``; ``syncfiles.adb.resolve_adb_path`` already looks there.
"""

from pathlib import Path

block_cipher = None

ADB_DIR = Path("src/syncfiles/adb_fallback")
adb_binaries = [
    (str(ADB_DIR / "adb.exe"), "adb_fallback"),
    (str(ADB_DIR / "AdbWinApi.dll"), "adb_fallback"),
    (str(ADB_DIR / "AdbWinUsbApi.dll"), "adb_fallback"),
    (str(ADB_DIR / "libwinpthread-1.dll"), "adb_fallback"),
    # adb.exe is 32-bit; these two 32-bit VC++ runtime DLLs keep it
    # working on stripped Windows images that don't have 32-bit VC++.
    (str(ADB_DIR / "vcruntime140.dll"), "adb_fallback"),
    (str(ADB_DIR / "msvcp140.dll"), "adb_fallback"),
]

a = Analysis(
    ["src/syncfiles/__main__.py"],
    pathex=[],
    binaries=adb_binaries,
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="SyncFiles",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
