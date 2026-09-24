# PyInstaller spec — macOS build
# Usage (from project root):
#   pyinstaller build/WA_Outreach_mac.spec --clean --noconfirm
#
# Output: dist/WA Outreach.app

from pathlib import Path
from PyInstaller.utils.hooks import collect_all, collect_data_files

PROJECT = Path(SPECPATH).parent   # noqa: F821  (SPECPATH is injected by PyInstaller)

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
        "engineio.async_drivers.threading",
        "flask",
        "flask.templating",
        "werkzeug.serving",
        # pywebview macOS backend
        "webview",
        "webview.platforms.cocoa",
        # Playwright
        "playwright",
        "playwright.sync_api",
        "playwright._impl._sync_base",
        "playwright._impl._driver",
        # Phonenumbers (large data package needs explicit collection)
        "phonenumbers",
        "phonenumbers.data",
        # Pillow
        "PIL",
        "PIL.Image",
        # OpenPyXL
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
    upx=False,
    console=False,
    icon=str(PROJECT / "assets" / "icon.icns"),
)

coll = COLLECT(   # noqa: F821
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="WA Outreach",
)

app = BUNDLE(   # noqa: F821
    coll,
    name="WA Outreach.app",
    icon=str(PROJECT / "assets" / "icon.icns"),
    bundle_identifier="com.drivenproperties.wa-outreach",
    info_plist={
        "CFBundleName":                "WA Outreach",
        "CFBundleDisplayName":         "WA Outreach",
        "CFBundleShortVersionString":  "1.0.0",
        "CFBundleVersion":             "1",
        "NSHighResolutionCapable":     True,
        "NSRequiresAquaSystemAppearance": False,
        "NSAppTransportSecurity": {
            "NSAllowsLocalNetworking": True,
        },
        "LSMinimumSystemVersion": "11.0",
        "LSMultipleInstancesProhibited": True,
    },
)
