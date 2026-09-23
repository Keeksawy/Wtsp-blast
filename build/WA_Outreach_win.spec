# PyInstaller spec — Windows build
# Usage (from project root, in a Windows terminal):
#   pyinstaller build\WA_Outreach_win.spec --clean --noconfirm
#
# Output: dist\WA Outreach\WA Outreach.exe

from pathlib import Path
from PyInstaller.utils.hooks import collect_all

PROJECT = Path(SPECPATH).parent   # noqa: F821

block_cipher = None

# Collect pywebview package data, binaries and hidden imports up front
_wv_datas, _wv_bins, _wv_hidden = collect_all("webview")

a = Analysis(
    [str(PROJECT / "main.py")],
    pathex=[str(PROJECT)],
    binaries=_wv_bins,
    datas=[
        (str(PROJECT / "public"),        "public"),
        (str(PROJECT / "config"),        "config"),
        (str(PROJECT / "src"),           "src"),
        (str(PROJECT / "assets"),        "assets"),
        *([( str(PROJECT / ".env"), ".")] if (PROJECT / ".env").exists() else []),
        *_wv_datas,
    ],
    hiddenimports=[
        # Flask / Werkzeug
        "flask",
        "flask.templating",
        "werkzeug.serving",
        # pywebview Windows backend (WebView2/Edge — no .NET needed)
        "webview",
        "webview.platforms.edgechromium",
        # Playwright
        "playwright",
        "playwright.sync_api",
        "playwright._impl._sync_base",
        "playwright._impl._driver",
        # Others
        "phonenumbers",
        "phonenumbers.data",
        "PIL",
        "PIL.Image",
        "openpyxl",
        "openpyxl.styles",
        "openpyxl.utils",
        *_wv_hidden,
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
