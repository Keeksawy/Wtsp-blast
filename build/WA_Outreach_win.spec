# PyInstaller spec — Windows build
# Usage (from project root, in a Windows terminal):
#   pyinstaller build\WA_Outreach_win.spec --clean --noconfirm
#
# Output: dist\WA Outreach\WA Outreach.exe
#         (wrap with Inno Setup using build\installer_win.iss)

import sys
from pathlib import Path

PROJECT = Path(SPECPATH).parent   # noqa: F821

block_cipher = None

a = Analysis(
    [str(PROJECT / "main.py")],
    pathex=[str(PROJECT)],
    binaries=[],
    datas=[
        (str(PROJECT / "public"),        "public"),
        (str(PROJECT / "config"),        "config"),
        (str(PROJECT / "src"),           "src"),
        (str(PROJECT / "assets"),        "assets"),
        *([( str(PROJECT / ".env"), ".")] if (PROJECT / ".env").exists() else []),
    ],
    hiddenimports=[
        "engineio.async_drivers.threading",
        "flask",
        "flask.templating",
        "werkzeug.serving",
        # pywebview Windows backend (uses Microsoft WebView2)
        "webview",
        "webview.platforms.winforms",
        "webview.platforms.edgechromium",
        "clr",         # pythonnet, used by winforms backend
        # Playwright
        "playwright",
        "playwright.sync_api",
        "playwright._impl._sync_base",
        # Others
        "phonenumbers",
        "phonenumbers.data",
        "PIL",
        "PIL.Image",
        "openpyxl",
        "openpyxl.styles",
        "openpyxl.utils",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "PyQt5", "PyQt6", "PySide2", "PySide6"],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)

from PyInstaller.utils.hooks import collect_all
webview_datas, webview_binaries, webview_hiddenimports = collect_all("webview")
a.datas    += webview_datas
a.binaries += webview_binaries
a.hiddenimports += webview_hiddenimports

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)   # noqa: F821

exe = EXE(   # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="WA Outreach",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    icon=str(PROJECT / "assets" / "icon.ico"),
    # Windows manifest — request DPI awareness
    uac_admin=False,
)

coll = COLLECT(   # noqa: F821
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="WA Outreach",
)
