@echo off
cd /d "%~dp0"
echo === Public link setup (Cloudflare quick tunnel) ===
echo.
echo This does NOT need an account, sign-in, or admin rights - it's an
echo anonymous, free tunnel, and cloudflared.exe is downloaded straight into
echo this folder rather than installed system-wide.
echo.

if exist cloudflared.exe goto :havecloudflared

echo Downloading cloudflared.exe (about 30-40 MB, one-time)...
powershell -NoProfile -Command "Invoke-WebRequest -Uri 'https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-windows-amd64.exe' -OutFile 'cloudflared.exe'"
if not exist cloudflared.exe (
  echo Download failed. Check your internet connection and try again, or
  echo download it manually from:
  echo https://github.com/cloudflare/cloudflared/releases/latest
  echo ^(grab cloudflared-windows-amd64.exe and rename it to cloudflared.exe
  echo in this same folder: %cd%^)
  pause
  exit /b 1
)
echo Downloaded.

:havecloudflared
echo Make sure setup_and_run.bat is already running in another window first
echo ^(the dashboard needs to be live at http://localhost:3000 before this will work^).
echo.
echo Starting tunnel... your public link will appear below as a
echo https://something.trycloudflare.com address in a few seconds.
echo.
cloudflared.exe tunnel --url http://localhost:3000

pause
