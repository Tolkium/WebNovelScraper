# -*- mode: python ; coding: utf-8 -*-


a = Analysis(
    ['WebNovelDesktop.py'],
    pathex=[],
    binaries=[],
    datas=[('icon.ico', '.'), ('webnovel_assets/chevron-up.svg', 'webnovel_assets'), ('webnovel_assets/chevron-down.svg', 'webnovel_assets'), ('webnovel_assets/check.svg', 'webnovel_assets')],
    hiddenimports=['PySide6.QtSvg'],
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
    name='WebNovelScraper',
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
    icon=['icon.ico'],
)
