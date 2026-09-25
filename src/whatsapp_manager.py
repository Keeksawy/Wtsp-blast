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
    # Multiple fallback selectors for inbound messages — WhatsApp changes class names periodically
    "message_in": "div.message-in, div[class*='message-in'], [data-testid='msg-container'] [class*='copyable-text']",
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
        self._last_deep_scan = 0.0
        self._seen_inbound = set()  # (phone, body_snippet) dedup cache

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

    def send_image(self, phone_e164, image_path, caption="", timeout=120):
        """Cross-thread call: enqueue an image send and block for the result."""
        reply_q = queue.Queue()
        self.commands.put(("send_image", {"phone": phone_e164, "image_path": image_path, "caption": caption}, reply_q))
        try:
            ok, err = reply_q.get(timeout=timeout)
        except queue.Empty:
            raise TimeoutError(f"No response from WhatsApp session for number {self.number_id} within {timeout}s")
        if not ok:
            raise RuntimeError(err or "image send failed")

    # ---- thread body ----
    def _run(self):
        profile_dir = str(DATA_DIR / self.session_id)
        Path(profile_dir).mkdir(parents=True, exist_ok=True)
        try:
            with sync_playwright() as p:
                context = p.chromium.launch_persistent_context(
                    profile_dir, headless=True,
                    channel="chrome",
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

                elif cmd == "send_image":
                    if connected:
                        ok, err = self._do_send_image(page, args["phone"], args["image_path"], args.get("caption", ""))
                    else:
                        ok, err = False, "not connected"
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

            # If the page is on a previous chat/send URL, return to home first so
            # the next navigation starts from a clean, settled state. This is the
            # root cause of the compose-box timeout on consecutive sends.
            current_url = page.url or ""
            if "web.whatsapp.com" in current_url and current_url != "https://web.whatsapp.com/":
                page.goto("https://web.whatsapp.com", timeout=30000)
                page.wait_for_selector(SELECTORS["chat_list_ready"], timeout=20000)
                page.wait_for_timeout(1000)

            page.goto(f"https://web.whatsapp.com/send?phone={digits}&text={quote(text)}", timeout=30000)
            page.wait_for_timeout(2000)

            # Dismiss any lingering toast/modal left over from the previous send
            try:
                close_btn = page.locator("button[aria-label='Close'], span[data-icon='x']").first
                if close_btn.is_visible(timeout=1000):
                    close_btn.click()
                    page.wait_for_timeout(500)
            except Exception:
                pass

            # WhatsApp shows a modal if the number isn't on WhatsApp / phone format is invalid
            invalid = page.locator(SELECTORS["invalid_number_dialog"])
            if invalid.count() > 0 and invalid.first.is_visible(timeout=3000):
                return False, "WhatsApp reports this number is invalid or not on WhatsApp"

            box = page.locator(SELECTORS["compose_box"]).last
            try:
                box.wait_for(state="visible", timeout=45000)
            except Exception:
                # One retry: reload and wait again — handles stale DOM after reconnect
                page.reload(timeout=30000)
                page.wait_for_timeout(2000)
                box.wait_for(state="visible", timeout=30000)

            send_btn = page.locator(SELECTORS["send_button"]).last
            send_btn.wait_for(state="visible", timeout=20000)
            send_btn.click()
            page.wait_for_timeout(1500)

            # Return to home so the session is in a clean state for the next send
            page.goto("https://web.whatsapp.com", timeout=30000)
            page.wait_for_timeout(1000)

            return True, None
        except Exception as e:
            return False, f"send failed: {e}"

    def _do_send_image(self, page, phone_e164, image_path, caption=""):
        """Send an image with an optional text caption.
        Falls back to text-only if the image upload flow fails."""
        digits = re.sub(r"\D", "", phone_e164)
        try:
            current_url = page.url or ""
            if "web.whatsapp.com" in current_url and current_url != "https://web.whatsapp.com/":
                page.goto("https://web.whatsapp.com", timeout=30000)
                page.wait_for_selector(SELECTORS["chat_list_ready"], timeout=20000)
                page.wait_for_timeout(1000)

            page.goto(f"https://web.whatsapp.com/send?phone={digits}", timeout=30000)

            # Wait for the compose box — confirms the chat is open
            box = page.locator(SELECTORS["compose_box"]).last
            box.wait_for(state="visible", timeout=45000)

            # Dismiss any lingering modal
            try:
                close_btn = page.locator("button[aria-label='Close'], span[data-icon='x']").first
                if close_btn.is_visible(timeout=1000):
                    close_btn.click()
                    page.wait_for_timeout(500)
            except Exception:
                pass

            # Check for invalid-number dialog
            invalid = page.locator(SELECTORS["invalid_number_dialog"])
            if invalid.count() > 0 and invalid.first.is_visible(timeout=3000):
                return False, "WhatsApp reports this number is invalid or not on WhatsApp"

            is_pdf = Path(image_path).suffix.lower() == ".pdf"

            # Attach via the file-chooser triggered by the paperclip menu.
            # IMPORTANT: click the paperclip OUTSIDE expect_file_chooser — the file
            # chooser is only triggered by the submenu item click, not the paperclip.
            try:
                page.locator(
                    '[data-testid="attach-menu-icon"], '
                    '[title="Attach"], '
                    'span[data-icon="attach-menu-background"], '
                    'div[title="Attach"]'
                ).first.click()
                page.wait_for_timeout(600)  # wait for submenu to render

                with page.expect_file_chooser(timeout=8000) as fc_info:
                    if is_pdf:
                        page.locator(
                            'li span[data-testid="attach-menu-document-icon"], '
                            '[data-testid="mi-attach-document"], '
                            'li[title="Document"]'
                        ).first.click()
                    else:
                        page.locator(
                            'li span[data-testid="attach-menu-photo-video-icon"], '
                            '[data-testid="mi-attach-photo-video"], '
                            'li[title="Photos & Videos"]'
                        ).first.click()
                fc_info.value.set_files(image_path)
            except Exception:
                # Fallback: directly set a hidden file input (visible after the menu opens)
                file_input = page.locator('input[type="file"]').first
                file_input.set_input_files(image_path)

            # Wait for the image preview dialog to appear
            page.wait_for_timeout(2500)

            # Type caption in the caption input (the editable area in the media dialog)
            if caption:
                caption_box = page.locator(
                    'div[data-testid="media-caption-input-container"] div[contenteditable],'
                    'div[aria-label*="caption" i],'
                    'div[data-testid="photo-caption"] div[contenteditable],'
                    'footer div[contenteditable]'
                ).last
                try:
                    caption_box.wait_for(state="visible", timeout=5000)
                    caption_box.click()
                    caption_box.type(caption, delay=20)
                except Exception:
                    pass  # caption not critical — image will still send without it

            # Send
            send_btn = page.locator(SELECTORS["send_button"]).last
            send_btn.wait_for(state="visible", timeout=10000)
            send_btn.click()
            page.wait_for_timeout(2000)

            page.goto("https://web.whatsapp.com", timeout=30000)
            page.wait_for_timeout(1000)
            return True, None
        except Exception as e:
            return False, f"image send failed: {e}"

    def _poll_inbound(self, page):
        """Scrape inbound messages. Two passes:
        1. Unread-badge rows (fast, real-time).
        2. Every 2 minutes: open each known contacted-contact's chat directly
           to catch messages that arrived while the browser was closed/reconnecting.
        """
        try:
            # Pass 1: unread badge rows
            rows = page.locator(SELECTORS["unread_chat_rows"])
            n = min(rows.count(), 10)
            for i in range(n):
                row = rows.nth(i)
                if row.locator(SELECTORS["unread_badge"]).count() == 0:
                    continue
                self._open_chat_and_capture(page, row)

            # Pass 2: retroactive scan of contacts we've messaged (every 2 min)
            now = time.time()
            if now - self._last_deep_scan > 120:
                self._last_deep_scan = now
                self._deep_scan_contacts(page)

        except Exception as e:
            self.manager._emit("error", {"numberId": self.number_id, "error": f"poll_inbound: {e}"})

    def _open_chat_and_capture(self, page, row=None, phone_e164=None):
        """Click a chat row (or navigate directly by phone) and capture inbound messages."""
        try:
            if phone_e164:
                page.goto(f"https://web.whatsapp.com/send?phone={phone_e164.lstrip('+')}", timeout=20000)
                page.wait_for_timeout(3500)
            elif row is not None:
                row.click()
                page.wait_for_timeout(1500)
            else:
                return

            chat_phone_e164 = phone_e164
            try:
                url = page.url or ""
                m = re.search(r"[?&]phone=(\d+)", url)
                if m:
                    chat_phone_e164 = "+" + m.group(1)
            except Exception:
                pass

            title_el = page.locator("header span[dir='auto']").first
            chat_name = title_el.inner_text(timeout=2000) if title_el.count() else None

            print(f"[inbound scan] phone={chat_phone_e164} name={chat_name}", flush=True)

            # Collect known outbound bodies so we can exclude them from the scraped list
            known_outbound = set()
            for m in db.get_messages(direction="outbound"):
                if m.get("body"):
                    known_outbound.add(m["body"].strip())

            # Strategy 1: inbound-specific selectors (class-based)
            all_bodies = []
            for sel in [
                "div.message-in span.selectable-text span",
                "div[class*='message-in'] span[class*='selectable-text'] span",
                "div[class*='message-in'] span[dir='ltr']",
            ]:
                els = page.locator(sel)
                if els.count() > 0:
                    for i in range(min(els.count(), 10)):
                        try:
                            t = els.nth(i).inner_text(timeout=1000).strip()
                            if t:
                                all_bodies.append(t)
                        except Exception:
                            pass
                    if all_bodies:
                        break

            # Strategy 2: read full message container text, filter out known outbound
            if not all_bodies:
                containers = page.locator("[data-testid='msg-container']")
                cnt = containers.count()
                print(f"[inbound scan] generic selector found {cnt} containers", flush=True)
                for i in range(min(cnt, 20)):
                    try:
                        full_text = containers.nth(i).inner_text(timeout=1000).strip()
                        if not full_text:
                            continue
                        # Refresh known_outbound each iteration to catch opt-out confirmations
                        known_outbound = set(
                            m["body"].strip() for m in db.get_messages(direction="outbound") if m.get("body")
                        )
                        # Check if this container's text starts with any known outbound body
                        is_outbound = any(
                            full_text.startswith(ob[:40]) or ob.startswith(full_text[:40])
                            for ob in known_outbound if len(ob) > 5
                        )
                        if not is_outbound:
                            # Use first non-empty line as the message body
                            lines = [l.strip() for l in full_text.splitlines() if l.strip()]
                            body = lines[0] if lines else full_text
                            if body and len(body) > 1:
                                all_bodies.append(body)
                    except Exception:
                        pass

            print(f"[inbound scan] inbound candidates: {all_bodies[-5:] if all_bodies else []}", flush=True)

            for body in all_bodies:
                if body:
                    self._handle_inbound(chat_phone_e164, chat_name, body)
        except Exception as e:
            print(f"[inbound scan] error: {e}", flush=True)

    def _deep_scan_contacts(self, page):
        """Navigate to each known contacted-contact's chat and check for a new inbound message."""
        try:
            all_contacts = db.get_contacts()
            contacts = [c for c in all_contacts
                        if c.get("assignedNumberId") == self.number_id
                        and c.get("messageStatus") in ("sent", "queued", "failed")
                        and c.get("phoneE164")]
            print(f"[deep scan] numberId={self.number_id} scanning {len(contacts)} of {len(all_contacts)} contacts", flush=True)
            for c in contacts[:15]:
                self._open_chat_and_capture(page, phone_e164=c["phoneE164"])
                page.wait_for_timeout(500)
            # Return to home after scan
            page.goto("https://web.whatsapp.com", timeout=15000)
            page.wait_for_timeout(1000)
        except Exception as e:
            print(f"[deep scan] error: {e}", flush=True)

    def _handle_inbound(self, chat_phone_e164, chat_name, body):
        if not body or not body.strip():
            return
        body = body.strip()

        # In-memory dedup (fast path within session)
        dedup_key = (chat_phone_e164 or chat_name or "", body[:60])
        if dedup_key in self._seen_inbound:
            return
        self._seen_inbound.add(dedup_key)
        if len(self._seen_inbound) > 500:
            self._seen_inbound.clear()

        # Persistent dedup — check DB so restarts don't re-store the same message
        existing = db.get_messages(direction="inbound")
        for m in existing:
            if m.get("body", "").strip()[:60] == body[:60]:
                # Already stored — still update in-memory cache but skip DB write
                return

        contact = None
        contacts = db.get_contacts()

        # Primary match: E.164 phone — reliable regardless of saved contact name
        if chat_phone_e164:
            for c in contacts:
                if c.get("phoneE164") == chat_phone_e164:
                    contact = c
                    break

        # Fallback: display name match (for cases where URL parsing failed)
        if contact is None and chat_name:
            for c in contacts:
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
            # Check for sell/rent intent — triggers CRM webhook if matched
            if contact:
                from . import crm
                intent = crm.detect_intent(body, cfg)
                if intent:
                    crm_url = db.get_settings().get("crmWebhookUrl", "")
                    crm.send_lead(contact, intent, crm_url)
                    db.update_contact(contact["contactId"], {
                        intent: "Yes",
                        "ownerResponse": body,
                        "crmLeadSentAt": datetime.now(timezone.utc).isoformat(),
                        "crmLeadIntent": intent,
                    })
                    self.manager._emit("crm_lead", {"numberId": self.number_id, "contactId": contact["contactId"], "intent": intent})
            self.manager._emit("inbound", {"numberId": self.number_id, "contactId": contact["contactId"] if contact else None, "body": body})


class WhatsAppManager:
    def __init__(self, cfg, company_name="Our Agency"):
        self.cfg = cfg
        self.company_name = company_name
        self.sessions = {}  # number_id -> NumberSession
        self.qr_store = {}  # number_id -> data URL
        self._listeners = []
        self._resume_existing_sessions()

    def _resume_existing_sessions(self):
        """On process restart, re-launch a browser session for every number
        already in the DB, reusing its persistent Chrome profile so it logs
        back into WhatsApp Web without a fresh QR scan."""
        try:
            existing = db.get_numbers()
        except Exception:
            existing = []
        for number in existing:
            try:
                session = NumberSession(self, number)
                self.sessions[number["id"]] = session
                session.start()
            except Exception:
                pass


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

    def send_image(self, number_id, phone_e164, image_path, caption=""):
        session = self.sessions.get(number_id)
        if not session:
            raise RuntimeError(f"No active session for number {number_id}")
        session.send_image(phone_e164, image_path, caption)

    def pause_number(self, number_id, reason=None):
        db.update_number(number_id, {"paused": True, "pauseReason": reason or "Paused manually"})
        self._emit("status", {"numberId": number_id, "status": "paused"})

    def resume_number(self, number_id):
        db.update_number(number_id, {"paused": False, "pauseReason": None})
        self._emit("status", {"numberId": number_id, "status": "resumed"})

    def shutdown(self):
        for session in self.sessions.values():
            session.stop()

    def remove_number(self, number_id):
        session = self.sessions.pop(number_id, None)
        if session:
            try:
                session.stop()
            except Exception:
                pass
        self.qr_store.pop(number_id, None)
