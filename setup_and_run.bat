@echo off
cd /d "%~dp0"
echo === WhatsApp Outreach (Python) setup ===
echo.

where py >nul 2>nul
if %errorlevel%==0 (
  set PYCMD=py
  goto :havepython
)
where python >nul 2>nul
if %errorlevel%==0 (
  set PYCMD=python
  goto :havepython
)

echo Python not found. Trying to install it automatically via winget...
where winget >nul 2>nul
if not %errorlevel%==0 (
  echo winget is not available on this machine either.
  echo Please install Python manually from https://python.org ^(check "Add python.exe to PATH"^), then re-run this script.
  pause
  exit /b 1
)
winget install --id Python.Python.3.13 -e --scope user --accept-package-agreements --accept-source-agreements
if errorlevel 1 (
  echo Automatic install failed. Please install Python manually from https://python.org ^(check "Add python.exe to PATH"^), then re-run this script.
  pause
  exit /b 1
)
echo Python was installed. Closing this window in 5 seconds - please re-run setup_and_run.bat afterward so the new PATH takes effect.
timeout /t 5
exit /b 0

:havepython
echo Using Python command: %PYCMD%
echo Python details:
%PYCMD% -c "import platform,struct; print(' version:', platform.python_version()); print(' machine:', platform.machine()); print(' bitness:', struct.calcsize('P')*8)"
echo.

if not exist .venv (
  echo Creating virtual environment...
  %PYCMD% -m venv .venv
)

call .venv\Scripts\activate.bat

echo Upgrading pip...
.venv\Scripts\python.exe -m pip install --upgrade pip

echo Installing dependencies (first run takes a few minutes)...
echo Forcing pre-built wheels only for greenlet/playwright to avoid needing a C++ compiler...
.venv\Scripts\python.exe -m pip install --only-binary=greenlet,playwright -r requirements.txt
if errorlevel 1 (
  echo.
  echo pip install failed - see errors above.
  echo If the error mentions "no matching distribution" for greenlet or a missing
  echo wheel for your platform, this machine's Python/CPU architecture may not have
  echo a pre-built package available and we'll need a different approach - copy the
  echo full error text back to Claude.
  pause
  exit /b 1
)

echo Installing Playwright's Chromium browser (one-time download)...
.venv\Scripts\python.exe -m playwright install chromium
if errorlevel 1 (
  echo playwright install failed - see errors above.
  pause
  exit /b 1
)

if not exist .env (
  echo Creating default .env file...
  (
    echo COMPANY_NAME=Your Company Name
    echo DEFAULT_AGENT_NAME=Your Name
    echo PORT=3000
  ) > .env
  echo Created .env - edit COMPANY_NAME/DEFAULT_AGENT_NAME whenever you like.
)

echo.
echo === Starting the server ===
echo Once you see "WA outreach dashboard running at http://localhost:3000", open that link in your browser.
echo Leave this window open - closing it stops the server.
echo.
.venv\Scripts\python.exe app.py

pause
