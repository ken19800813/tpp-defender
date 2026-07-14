# -*- mode: python ; coding: utf-8 -*-
# 直播小幫手 Windows 版本 PyInstaller spec 檔
# 在 Windows 上執行: pyinstaller TPPchat_windows.spec

import os
import site
from PyInstaller.utils.hooks import get_module_file_attribute

# 找出 playwright 相關檔案位置
try:
    playwright_path = os.path.dirname(get_module_file_attribute('playwright'))
except:
    playwright_path = None

datas = []
if playwright_path:
    datas.append((os.path.join(playwright_path, '.browsers'), 'playwright/.browsers'))

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
