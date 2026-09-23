"""
WA Outreach — desktop entry point.

Starts Flask in a background thread, then opens a native pywebview window
at http://127.0.0.1:3000. Closing the window shuts everything down cleanly.

Run in development:   python3 main.py
Run as built app:     double-click WA Outreach.app / WA Outreach.exe
"""
import os
import sys
import socket
import threading
import time


# ── Path bootstrap (must run before any project import) ───────────────────
def _bootstrap():
    """
    Configure paths for both development and PyInstaller bundle mode.

    In a bundle, sys.frozen=True and sys._MEIPASS is the read-only extract dir.
    Writable data (db.json, sessions, uploads) lives OUTSIDE _MEIPASS so it
    survives re-launches.
    """
    if getattr(sys, "frozen", False):
        bundle_dir = sys._MEIPASS          # read-only resources inside bundle
        exe_path   = sys.executable        # points to the real executable

        if sys.platform == "darwin":
            # Executable is at:  WA Outreach.app/Contents/MacOS/WA Outreach
            # Place data beside the .app, not inside it (read-only on signed builds).
            mac_macos    = os.path.dirname(exe_path)       # .../MacOS
            mac_contents = os.path.dirname(mac_macos)      # .../Contents
            mac_app      = os.path.dirname(mac_contents)   # .../WA Outreach.app
            data_root    = os.path.dirname(mac_app)        # folder containing .app
        else:
            data_root = os.path.dirname(exe_path)          # beside the .exe

        data_dir = os.path.join(data_root, "WA Outreach Data")
        os.environ.setdefault("WA_DATA_DIR",              data_dir)
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", os.path.join(data_dir, "browsers"))
        os.chdir(bundle_dir)    # so relative imports inside the bundle resolve
    else:
        # Development: use the project root (directory of this file)
        os.chdir(os.path.dirname(os.path.abspath(__file__)))


_bootstrap()

import webview   # noqa: E402  (must come after _bootstrap sets cwd)


FLASK_PORT = int(os.environ.get("PORT", 3000))

_LOADING_HTML = """<!doctype html>
<html>
<head><meta charset="utf-8">
<style>
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body {
    display: flex; align-items: center; justify-content: center;
    height: 100vh;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
    background: #0F2240; color: #fff;
  }
  .wrap { text-align: center; }
  .dp {
    width: 80px; height: 80px; background: #C4A257; border-radius: 20px;
    display: flex; align-items: center; justify-content: center;
    font-size: 30px; font-weight: 800; margin: 0 auto 20px; color: #0F2240;
  }
  h1   { font-size: 22px; font-weight: 700; margin-bottom: 6px; }
  p    { font-size: 13px; color: rgba(255,255,255,.5); }
  .spinner {
    margin: 28px auto 0; width: 32px; height: 32px;
    border: 3px solid rgba(196,162,87,.25); border-top-color: #C4A257;
    border-radius: 50%; animation: spin .75s linear infinite;
  }
  @keyframes spin { to { transform: rotate(360deg); } }
</style>
</head>
<body>
  <div class="wrap">
    <div class="dp">DP</div>
    <h1>WhatsApp Outreach</h1>
    <p>Starting up&hellip;</p>
    <div class="spinner"></div>
  </div>
</body>
</html>"""


# ── Helpers ────────────────────────────────────────────────────────────────

def _port_in_use(port: int) -> bool:
    """Return True if something is already bound to 127.0.0.1:<port>."""
    with socket.socket() as s:
        try:
            s.bind(("127.0.0.1", port))
            return False
        except OSError:
            return True


def _wait_for_flask(timeout: float = 30.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", FLASK_PORT), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.15)
    return False


def _start_flask():
    from app import app as flask_app
    flask_app.run(
        host="127.0.0.1",
        port=FLASK_PORT,
        debug=False,
        use_reloader=False,
        threaded=True,
    )


def _ensure_playwright_browsers():
    """
    Download Playwright's Chromium on first run.
    Runs in a background thread — won't block the UI.
    Uses PLAYWRIGHT_BROWSERS_PATH env var when set.
    """
    import glob
    browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    if browsers_path:
        os.makedirs(browsers_path, exist_ok=True)
        if glob.glob(os.path.join(browsers_path, "chromium-*")):
            return  # already installed
    else:
        # Check the default Playwright location
        try:
            from playwright._impl._driver import compute_driver_executable
            # If we can import this without error, Playwright itself is fine;
            # we still need to check if the browser binary exists.
            pass
        except Exception:
            pass

    import subprocess
    try:
        print("[desktop] Installing Playwright Chromium browser…", flush=True)
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True,
        )
        print("[desktop] Playwright Chromium ready.", flush=True)
    except Exception as exc:
        print(f"[desktop] Warning: Playwright install failed: {exc}", file=sys.stderr, flush=True)


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    # Guard against double-launch
    if _port_in_use(FLASK_PORT):
        webview.create_window(
            "WA Outreach",
            html=(
                "<body style='font-family:sans-serif;padding:48px;text-align:center'>"
                "<h2 style='color:#0F2240'>WA Outreach is already running.</h2>"
                "<p style='color:#666;margin-top:8px'>Check your taskbar or system tray.</p>"
                "</body>"
            ),
            width=420, height=200,
        )
        webview.start()
        return

    # Start Flask server
    flask_thread = threading.Thread(target=_start_flask, daemon=True)
    flask_thread.start()

    # Download Playwright Chromium in background (non-blocking for UI)
    browser_thread = threading.Thread(target=_ensure_playwright_browsers, daemon=True)
    browser_thread.start()

    # Create the window with a loading splash; swap to the real URL once ready
    window = webview.create_window(
        "WA Outreach — Driven Properties",
        html=_LOADING_HTML,
        width=1280,
        height=860,
        min_size=(960, 640),
    )

    def _after_start():
        """Called by pywebview in a background thread once the window is ready."""
        if _wait_for_flask(timeout=30):
            window.load_url(f"http://127.0.0.1:{FLASK_PORT}/")
        else:
            window.load_html(
                "<body style='font-family:sans-serif;padding:48px;color:#c0392b'>"
                "<h2>Server failed to start.</h2>"
                "<p>Please quit and reopen the app. If the problem persists, "
                "check that port 3000 is not in use.</p>"
                "</body>"
            )

    webview.start(_after_start, debug=False)


if __name__ == "__main__":
    main()
