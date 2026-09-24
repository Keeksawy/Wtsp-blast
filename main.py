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
import tempfile


# ── Path bootstrap (must run before any project import) ───────────────────
def _bootstrap():
    if getattr(sys, "frozen", False):
        bundle_dir = sys._MEIPASS
        exe_path   = sys.executable

        if sys.platform == "darwin":
            mac_macos    = os.path.dirname(exe_path)
            mac_contents = os.path.dirname(mac_macos)
            mac_app      = os.path.dirname(mac_contents)
            data_root    = os.path.dirname(mac_app)
        else:
            data_root = os.path.dirname(exe_path)

        data_dir = os.path.join(data_root, "WA Outreach Data")
        os.environ.setdefault("WA_DATA_DIR",              data_dir)
        os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", os.path.join(data_dir, "browsers"))
        os.chdir(bundle_dir)
    else:
        os.chdir(os.path.dirname(os.path.abspath(__file__)))


_bootstrap()

# ── Single-instance lock (atomic, survives fast double-click) ─────────────
_LOCK_FILE = None   # keep file object alive for the lifetime of the process

def _acquire_instance_lock() -> bool:
    """Try to grab an exclusive OS-level lock. Returns True if we're the first instance."""
    global _LOCK_FILE
    lock_dir = os.environ.get("WA_DATA_DIR") or tempfile.gettempdir()
    os.makedirs(lock_dir, exist_ok=True)
    lock_path = os.path.join(lock_dir, "wa_outreach.lock")
    try:
        _LOCK_FILE = open(lock_path, "w")
        if sys.platform == "win32":
            import msvcrt
            msvcrt.locking(_LOCK_FILE.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(_LOCK_FILE, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except (OSError, IOError):
        return False


# Force WebView2 (Edge) backend on Windows — avoids pythonnet/.NET entirely.
if sys.platform == "win32":
    os.environ.setdefault("PYWEBVIEW_GUI", "edgechromium")


# ── Fatal error dialog — shows ONCE then exits ────────────────────────────
def _fatal(title: str, message: str):
    """Show one error dialog and quit. Never loops."""
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(0, message, title, 0x10)  # MB_ICONERROR
        except Exception:
            print(f"{title}: {message}", file=sys.stderr)
    elif sys.platform == "darwin":
        try:
            import subprocess
            subprocess.run([
                "osascript", "-e",
                f'display dialog "{message}" with title "{title}" buttons {{"OK"}} '
                f'default button "OK" with icon stop'
            ])
        except Exception:
            print(f"{title}: {message}", file=sys.stderr)
    else:
        print(f"{title}: {message}", file=sys.stderr)
    sys.exit(1)


# ── Import webview after error handler is defined ─────────────────────────
try:
    import webview
except Exception as e:
    _fatal("WA Outreach — startup error", f"Could not load the window engine:\n\n{e}")


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
    import glob
    browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    if browsers_path:
        os.makedirs(browsers_path, exist_ok=True)
        if glob.glob(os.path.join(browsers_path, "chromium-*")):
            return
    import subprocess
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True,
        )
    except Exception as exc:
        print(f"[desktop] Playwright install failed: {exc}", file=sys.stderr, flush=True)


# ── Main ───────────────────────────────────────────────────────────────────

def main():
    try:
        _run()
    except Exception as e:
        # Catch-all — show ONE dialog and stop. Never loops.
        msg = str(e)
        if "edgechromium" in msg or "WebView2" in msg.lower() or "webview2" in msg:
            _fatal(
                "WA Outreach — Missing component",
                "Microsoft WebView2 is required but not installed.\n\n"
                "Please install it from:\n"
                "https://developer.microsoft.com/microsoft-edge/webview2/\n\n"
                "(Download the 'Evergreen Bootstrapper', ~2 MB, run it, then reopen the app.)"
            )
        else:
            _fatal("WA Outreach — Error", f"The app failed to start:\n\n{msg}")


def _run():
    if not _acquire_instance_lock():
        # Another instance already holds the lock — exit silently.
        sys.exit(0)

    flask_thread = threading.Thread(target=_start_flask, daemon=True)
    flask_thread.start()

    browser_thread = threading.Thread(target=_ensure_playwright_browsers, daemon=True)
    browser_thread.start()

    window = webview.create_window(
        "WA Outreach — Driven Properties",
        html=_LOADING_HTML,
        width=1280,
        height=860,
        min_size=(960, 640),
    )

    def _after_start():
        try:
            if _wait_for_flask(timeout=30):
                window.load_url(f"http://127.0.0.1:{FLASK_PORT}/")
            else:
                window.load_html(
                    "<body style='font-family:sans-serif;padding:48px;color:#c0392b'>"
                    "<h2>Server failed to start.</h2>"
                    "<p>Please quit and reopen the app.</p>"
                    "</body>"
                )
        except Exception as exc:
            print(f"[desktop] _after_start error: {exc}", file=sys.stderr)

    webview.start(_after_start, debug=False)


if __name__ == "__main__":
    main()
