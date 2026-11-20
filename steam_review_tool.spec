# -*- mode: python ; coding: utf-8 -*-

# Steam 差评比对工具打包入口（双击启动，无需命令行）
a = Analysis(
    ['launcher.py'],
    pathex=['.'],
    binaries=[],
    datas=[],
    hiddenimports=['socks', 'sockshandler'],
    hookspath=[],
    hooksconfig={},
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
    name='Steam差评比对工具',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,          # 保留控制台，便于交互输入 AppID
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='steam_icon.ico',
)
