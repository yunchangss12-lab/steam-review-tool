# -*- mode: python ; coding: utf-8 -*-

# 安装器打包配置：把主程序 EXE、config 模板、使用说明内嵌进安装器
a = Analysis(
    ['installer.py'],
    pathex=['.'],
    binaries=[],
    datas=[
        ('dist/Steam差评比对工具.exe', '.'),   # 主程序
        ('installer/config.local.json', '.'),   # config 模板
        ('installer/使用说明.md', '.'),         # 使用说明
    ],
    hiddenimports=[],
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
    name='Steam差评比对工具_安装包',
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
    icon='steam_icon.ico',
)
