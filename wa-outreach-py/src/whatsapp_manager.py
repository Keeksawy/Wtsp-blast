# Manages multiple WhatsApp Web sessions using Playwright, driving the real
# web.whatsapp.com UI in a headless Chromium profile per number — the same idea as
# the Node version's whatsapp-web.js/Puppeteer approach: you connect numbers you
# already control by scanning the QR code from that number's phone, same as opening
# web.whatsapp.com normally. No throwaway/farmed accounts, no fingerprint spoofing,
# no proxy/IP rotation.
#
# IMPORTANT — read this before relying on the Python version in production:
# whatsapp-web.js (the Node version's library) talks to WhatsApp's internal
# WebSocket protocol directly, so message send/receive is event-driven and fairly
# robust. This Playwright version instead drives the visible web.whatsapp.com page
# and *polls* the DOM for new messages and *scrapes* text out of chat bubbles using
# CSS selectors (see SELECTORS below). That is inherently more fragile: WhatsApp
# changes its web UI's internal class names periodically without notice, which can
# silently break sending, QR detection, or inbound-message scraping until the
# selectors here are updated. Treat this as a less battle-tested foundation than
# the Node version, test it carefully with your own numbers before real campaigns,
# and expect to need to patch SELECTORS if WhatsApp ships a UI change.
import base64
import queue
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

from . import db, safety

DATA_DIR = Path(__file__).parent.parent / "data" / "sessions"

# Centralized so a WhatsApp Web UI change only needs updating in one place.
SELECTORS = {
    "qr_canvas": "canvas[aria-label], div[data-ref] canvas",
    "chat_list_ready": "#pane-side, div[aria-label='Chat list']",
    "compose_box": "footer div[contenteditable='true'][data-tab]",
    "send_button": "button[aria-label='Send'], span[data-icon='send']",
    "unread_chat_rows": "#pane-side div[aria-testid='cell-frame-container']",
    "unread_badge": "span[aria-label*='unread']",
    "last_message_in": "div.message-in span.selectable-text span",
    "invalid_number_dialog": "div[data-animate-modal-popup='true']",
}


class NumberSession:
    """Owns one Playwright browser context + page, always driven from its own
    background thread (Playwright's sync API is not thread-safe across threads)."""

    def __init__(self, manager, number):
        self.manager = manager
        self.number_id = number["id"]
        self.session_id = number["sessionId"]
        self.commands = queue.Queue()
        self.stop_event = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.stop_event.set()

    def send(self, phone_e164, text, timeout=90):
        """Cross-thread call: enqueue a send command and block for the result."""
        reply_q = queue.Queue()
        self.commands.put(("send", {"phone": phone_e164, "text": text}, reply_q))
        try:
            ok, err = reply_q.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError(f"No response from WhatsApp session for number {self.number_id} within {timeout}s")
        if not ok:
            raise RuntimeError(err or "send failed")

    # ---- thread body ----
    def _run(self):
        profile_dir = str(DATA_DIR / self.session_id)
        Path(profile_dir).mkdir(parents=True, exist_ok=True)
        try:
            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    profile_dir, headless=True,
                    args=["--no-sandbox", "--disable-setuid-sandbox"],
                )
                page = context.pages[0] if context.pages else context.new_page()
                page.goto("https://web.whatsapp.com", timeout=60000)
                self._loop(page)
                context.close()
        except Exception as e:
            db.update_number(self.number_id, {"status": "error", "paused": True, "pauseReason": str(e)})
            self.manager._emit("status", {"numberId": self.number_id, "status": "error", "error": str(e)})

    def _loop(self, page):
        connected = False
        last_poll = 0.0
        while not self.stop_event.is_set():
            try:
                if not connected:
                    connected = self._check_login_state(page)

                # handle any pending send/etc commands, non-blocking
                try:
                    cmd, args, reply_q = self.commands.get(timeout=0.5)
                except queue.Empty:
                    cmd = None

                if cmd == "send":
                    ok, err = self._do_send(page, args["phone"], args["text"]) if connected else (False, "not connected")
                    reply_q.put((ok, err))

                if connected and time.time() - last_poll > 8:
                    self._poll_inbound(page)
                    last_poll = time.time()
            except Exception as e:
                # never let one bad iteration kill the whole session thread
                self.manager._emit("error", {"numberId": self.number_id, "error": str(e)})
                time.sleep(2)

    def _check_login_state(self, page):
        try:
            if page.locator(SELECTORS["chat_list_ready"]).first.is_visible(timeout=1000):
                current = db.find_number_by_id(self.number_id)
                patch = {"status": "connected"}
                if current and not current.get("activatedAt"):
                    patch["activatedAt"] = datetime.now(timezone.utc).isoformat()
                db.update_number(self.number_id, patch)
                self.manager.qr_store.pop(self.number_id, None)
                self.manager._emit("status", {"numberId": self.number_id, "status": "connected"})
                return True
        except Exception:
            pass
        try:
            qr = page.locator(SELECTORS["qr_canvas"]).first
            if qr.is_visible(timeout=1000):
                png_bytes = qr.screenshot()
                data_url = "data:image/png;base64," + base64.b64encode(png_bytes).decode("ascii")
                self.manager.qr_store[self.number_id] = data_url
                db.update_number(self.number_id, {"status": "awaiting_qr_scan"})
                self.manager._emit("status", {"numberId": self.number_id, "status": "awaiting_qr_scan"})
        except Exception:
            pass
        return False

    def _do_send(self, page, phone_e164, text):
        digits = re.sub(r"\D", "", phone_e164)
        try:
            from urllib.parse import quote
            page.goto(f"https://web.whatsapp.com/send?phone={digits}&text={quote(text)}", timeout=30000)
            # WhatsApp shows a modal if the number isn't on WhatsApp / phone format is invalid
            invalid = page.locator(SELECTORS["invalid_number_dialog"])
            if invalid.count() > 0 and invalid.first.is_visible(timeout=3000):
                return False, "WhatsApp reports this number is invalid or not on WhatsApp"
            box = page.locator(SELECTORS["compose_box"]).last
            box.wait_for(state="visible", timeout=20000)
            send_btn = page.locator(SELECTORS["send_button"]).last
            send_btn.wait_for(state="visible", timeout=10000)
            send_btn.click()
            page.wait_for_timeout(1200)
            return True, None
        except Exception as e:
            return False, f"send failed: {e}"

    def _poll_inbound(self, page):
        """Best-effort scrape of unread chats. This is the part most likely to need
        selector maintenance over time — see module docstring."""
        try:
            rows = page.locator(SELECTORS["unread_chat_rows"])
            n = min(rows.count(), 10)  # cap per poll to keep each loop iteration bounded
            for i in range(n):
                row = rows.nth(i)
                if row.locator(SELECTORS["unread_badge"]).count() == 0:
                    continue
                row.click()
                page.wait_for_timeout(800)
                title_el = page.locator("header span[dir='auto']").first
                chat_name = title_el.inner_text(timeout=2000) if title_el.count() else None
                msgs = page.locator(SELECTORS["last_message_in"])
                if msgs.count() == 0:
                    continue
                body = msgs.last.inner_text(timeout=2000)
                self._handle_inbound(chat_name, body)
        except Exception as e:
            self.manager._emit("error", {"numberId": self.number_id, "error": f"poll_inbound: {e}"})

    def _handle_inbound(self, chat_name, body):
        contact = None
        if chat_name:
            for c in db.get_contacts():
                if c.get("ownerName") and c["ownerName"].strip().lower() == chat_name.strip().lower():
                    contact = c
                    break
        db.log_message({
            "contactId": contact["contactId"] if contact else None,
            "numberId": self.number_id, "direction": "inbound", "body": body, "status": "received",
        })
        cfg = self.manager.cfg
        if contact and safety.detect_opt_out(body, cfg):
            db.update_contact(contact["contactId"], {"optedOut": True, "optedOutAt": datetime.now(timezone.utc).isoformat()})
            outbound = db.get_messages(contact_id=contact["contactId"], direction="outbound")
            if outbound:
                outbound[-1]["resultedInOptOut"] = True
                db.persist()
            from . import templates
            text = templates.render(templates.OPT_OUT_CONFIRMATION["text"], {
                "ownerName": contact.get("ownerName"), "unitNumber": contact.get("unitNumber"),
                "companyName": self.manager.company_name,
            })
            try:
                self.send(contact["phoneE164"], text)
                db.log_message({"contactId": contact["contactId"], "numberId": self.number_id, "direction": "outbound", "body": text, "status": "sent", "templateId": "OPTOUT"})
            except Exception as e:
                self.manager._emit("error", {"numberId": self.number_id, "error": str(e)})
            self.manager._emit("optout", {"numberId": self.number_id, "contactId": contact["contactId"]})
        else:
            self.manager._emit("inbound", {"numberId": self.number_id, "contactId": contact["contactId"] if contact else None, "body": body})


class WhatsAppManager:
    def __init__(self, cfg, company_name="Our Agency"):
        self.cfg = cfg
        self.company_name = company_name
        self.sessions = {}  # number_id -> NumberSession
        self.qr_store = {}  # number_id -> data URL
        self._listeners = []

    def on(self, callback):
        """callback(event_name, payload) — simple pub/sub, replaces Node's EventEmitter."""
        self._listeners.append(callback)

    def _emit(self, event, payload):
        for cb in self._listeners:
            try:
                cb(event, payload)
            except Exception:
                pass

    def add_number(self, label):
        number_id = db.next_number_seq()
        session_id = f"line-{number_id}"
        number = db.add_number({
            "id": number_id, "label": label, "sessionId": session_id,
            "status": "initializing", "activatedAt": None, "dailyCapOverride": None,
            "paused": False, "pauseReason": None,
            "createdAt": datetime.now(timezone.utc).isoformat(),
        })
        session = NumberSession(self, number)
        self.sessions[number_id] = session
        session.start()
        return number

    def get_qr(self, number_id):
        return self.qr_store.get(number_id)

    def send_text(self, number_id, phone_e164, text):
        session = self.sessions.get(number_id)
        if not session:
            raise RuntimeError(f"No active session for number {number_id}")
        session.send(phone_e164, text)

    def pause_number(self, number_id, reason=None):
        db.update_number(number_id, {"paused": True, "pauseReason": reason or "Paused manually"})
        self._emit("status", {"numberId": number_id, "status": "paused"})

    def resume_number(self, number_id):
        db.update_number(number_id, {"paused": False, "pauseReason": None})
        self._emit("status", {"numberId": number_id, "status": "resumed"})

    def shutdown(self):
        for session in self.sessions.values():
            session.stop()
