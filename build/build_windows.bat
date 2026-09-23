@echo off
REM ============================================================================
REM  Build WA Outreach.exe for Windows
REM  Run from the project root:  build\build_windows.bat
REM
REM  Prerequisites:
REM    - Python 3.11+ in PATH
REM    - Inno Setup 6 installed at default path (for the installer step)
REM ============================================================================
setlocal enabledelayedexpansion

set ROOT=%~dp0..
set VENV=%ROOT%\.venv
set DIST=%ROOT%\dist
set APP=WA Outreach

echo === WA Outreach - Windows build ===
echo Project: %ROOT%
echo.

REM ── 1. Venv & deps ──────────────────────────────────────────────────────
if not exist "%VENV%\Scripts\python.exe" (
    echo [1/5] Creating venv...
    python -m venv "%VENV%"
)

echo [1/5] Installing dependencies...
"%VENV%\Scripts\pip" install -q --upgrade pip
"%VENV%\Scripts\pip" install -q -r "%ROOT%\requirements.txt"
"%VENV%\Scripts\pip" install -q "pywebview>=4.3" "pythonnet>=3.0" pyinstaller

REM ── 2. Icons ─────────────────────────────────────────────────────────────
echo [2/5] Generating icons...
"%VENV%\Scripts\python" "%ROOT%\assets\make_icon.py"

REM ── 3. PyInstaller ──────────────────────────────────────────────────────
echo [3/5] Running PyInstaller...
"%VENV%\Scripts\pyinstaller" "%ROOT%\build\WA_Outreach_win.spec" --clean --noconfirm

echo.
echo   Built: %DIST%\%APP%\%APP%.exe

REM ── 4. Inno Setup installer (optional) ──────────────────────────────────
echo [4/5] Building installer...
set ISCC="C:\Program Files (x86)\Inno Setup 6\ISCC.exe"
if exist %ISCC% (
    %ISCC% "%ROOT%\build\installer_win.iss"
    echo   Installer: %DIST%\WA_Outreach_Setup.exe
) else (
    echo   Inno Setup not found - skipping installer.
    echo   Download from: https://jrsoftware.org/isdl.php
    echo   Or just zip the folder: %DIST%\%APP%\
)

REM ── 5. Summary ───────────────────────────────────────────────────────────
echo.
echo [5/5] Done.
echo.
echo   Exe folder: %DIST%\%APP%\
echo.
echo First-time install note:
echo   On first launch the app downloads Playwright Chromium (~150 MB).
echo   User data is stored in a "WA Outreach Data" folder beside the exe.

endlocal
