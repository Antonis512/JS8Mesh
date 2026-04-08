# -*- mode: python ; coding: utf-8 -*-

import os

import PyInstaller.building.build_main as build_main

_SITE_PACKAGES = os.path.join(
    os.environ.get("LOCALAPPDATA", ""),
    "Programs",
    "Python",
    "Python312",
    "Lib",
    "site-packages",
)
_HOOK_DIRS = [
    (os.path.join(_SITE_PACKAGES, "_pyinstaller_hooks_contrib", "stdhooks"), -1000),
    (os.path.join(_SITE_PACKAGES, "_pyinstaller_hooks_contrib"), -1000),
]

# Work around a local PyInstaller isolated-subprocess crash during hook discovery
# by supplying the discovered hook directories directly.
build_main.discover_hook_directories = lambda: list(_HOOK_DIRS)

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='JS8Mesh',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
