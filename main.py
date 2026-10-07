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
            data_dir = os.path.join(os.path.expanduser("~"), "Library", "Application Support", "WA Outreach")
        else:
            data_dir = os.path.join(os.path.dirname(exe_path), "WA Outreach Data")
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


_DEFAULT_PORT = int(os.environ.get("PORT", 3000))

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

FLASK_PORT: int = _DEFAULT_PORT   # resolved to a free port in _run()
_flask_start_error: str = ""      # populated if _start_flask crashes


def _find_free_port(start: int, attempts: int = 20) -> int:
    """Return the first free port at or after start. Raises RuntimeError if none found."""
    for p in range(start, start + attempts):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError(
        f"Could not find a free port in range {start}–{start + attempts - 1}. "
        "Close other applications and try again."
    )


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
    global _flask_start_error
    try:
        from app import app as flask_app
        flask_app.run(
            host="127.0.0.1",
            port=FLASK_PORT,
            debug=False,
            use_reloader=False,
            threaded=True,
        )
    except Exception as exc:
        _flask_start_error = str(exc)
        print(f"[flask] startup error: {exc}", file=sys.stderr, flush=True)


def _ensure_playwright_browsers():
    import glob
    browsers_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "")
    if browsers_path:
        os.makedirs(browsers_path, exist_ok=True)
        if glob.glob(os.path.join(browsers_path, "chromium-*")):
            _unquarantine(browsers_path)
            return

    # Use Playwright's own bundled driver to install Chromium — running
    # sys.executable with -m playwright does not work inside a PyInstaller bundle.
    try:
        from playwright._impl._driver import compute_driver_executable
        driver = str(compute_driver_executable())
    except Exception:
        driver = None

    import subprocess
    try:
        cmd = ([driver, "install", "chromium"] if driver
               else [sys.executable, "-m", "playwright", "install", "chromium"])
        subprocess.run(cmd, check=True, env={**os.environ, "PLAYWRIGHT_BROWSERS_PATH": browsers_path or ""})
        if browsers_path:
            _unquarantine(browsers_path)
    except Exception as exc:
        print(f"[desktop] Playwright install failed: {exc}", file=sys.stderr, flush=True)


def _unquarantine(path: str):
    """Remove macOS quarantine attribute from Playwright Chromium after download.
    Without this step macOS silently blocks the browser binary from running."""
    if sys.platform != "darwin":
        return
    import subprocess
    try:
        subprocess.run(["xattr", "-r", "-d", "com.apple.quarantine", path],
                       capture_output=True)
    except Exception:
        pass


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
    global FLASK_PORT

    if not _acquire_instance_lock():
        sys.exit(0)

    # Resolve a free port before starting Flask so we never get a silent bind failure.
    try:
        FLASK_PORT = _find_free_port(_DEFAULT_PORT)
    except RuntimeError as exc:
        _fatal("WA Outreach — Port unavailable", str(exc))

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
                return
            detail = _flask_start_error or "Flask did not respond within 30 seconds."
            window.load_html(
                "<body style='font-family:-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif;"
                "padding:48px;background:#fff;color:#c0392b'>"
                "<h2 style='margin:0 0 12px'>Server failed to start</h2>"
                f"<p style='font-size:13px;color:#333;margin:0 0 16px'>{detail}</p>"
                "<p style='font-size:12px;color:#888'>Please quit and reopen the app. "
                "If this keeps happening, restart your computer and try again.</p>"
                "</body>"
            )
        except Exception as exc:
            print(f"[desktop] _after_start error: {exc}", file=sys.stderr)

    webview.start(_after_start, debug=False)


if __name__ == "__main__":
    main()
