# -*- mode: python ; coding: utf-8 -*-

import os

datas = []
# 嘗試包含 playwright browsers，但如果不存在也不會失敗
try:
    from PyInstaller.utils.hooks import get_module_file_attribute
    playwright_path = os.path.dirname(get_module_file_attribute('playwright'))
    browsers_path = os.path.join(playwright_path, '.browsers')
    if os.path.exists(browsers_path):
        datas.append((browsers_path, 'playwright/.browsers'))
except:
    pass

a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=[
        'playwright',
        'playwright.sync_api',
        'playwright._impl',
        'pyperclip',
        'requests',
        'PIL',
        'customtkinter',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=['matplotlib', 'numpy', 'pandas'],
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
    name='TPPchat',
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
app = BUNDLE(
    exe,
    name='TPPchat.app',
    icon=None,
    bundle_identifier=None,
)
