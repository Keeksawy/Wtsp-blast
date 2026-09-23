# Campaign assignment + per-number send workers. 1:1 port of src/campaign.js —
# same locking (one owner -> one number -> one agent, write-once), same worker loop
# shape, same reasoning. Python threads stand in for the Node event loop's
# per-number async workers.
import threading
import random
import time
from datetime import datetime, timezone
from pathlib import Path

from . import db, safety, templates


def queue_eligible_contacts(campaign_name=None):
    """Mark all eligible contacts as queued and assign their template variant.
    Called automatically when a campaign starts — no manual assign step needed."""
    contacts = [
        c for c in db.get_contacts()
        if not c.get("optedOut") and not c.get("invalidNumber") and c.get("messageStatus","") in ("", "pending")
        and (not campaign_name or c.get("campaignName") == campaign_name)
    ]
    for c in contacts:
        db.update_contact(c["contactId"], {
            "templateVariant": templates.pick_initial_variant(c["contactId"])["id"],
            "messageStatus": "queued",
        })
    return {"queued": len(contacts)}

class CampaignRunner:
    """Single global round-robin scheduler across all active numbers.
    Strict rotation: send 1 -> number 1, send 2 -> number 2, ... wrapping
    around. Numbers that are disconnected, paused, or over their daily cap
    are skipped for that turn but stay in rotation. No business-hours gating.
    One global delay range (from db settings) is applied between every send,
    regardless of which number just sent.
    """

    def __init__(self, wa_manager, cfg, company_name="Our Agency", agent_default_name="Our Team"):
        self.wa_manager = wa_manager
        self.cfg = cfg
        self.company_name = company_name
        self.agent_default_name = agent_default_name
        self.active_number_ids = []
        self.state = {"running": False}
        self.thread = None
        self._rr_index = 0
        self._lock = threading.RLock()

    def start_number(self, number_id):
        with self._lock:
            if number_id not in self.active_number_ids:
                self.active_number_ids.append(number_id)
            if not self.state.get("running"):
                self.state = {"running": True}
                self.thread = threading.Thread(target=self._run_loop, args=(self.state,), daemon=True)
                self.thread.start()

    def stop_number(self, number_id):
        with self._lock:
            if number_id in self.active_number_ids:
                self.active_number_ids.remove(number_id)

    def stop_all(self):
        with self._lock:
            self.state["running"] = False
            self.active_number_ids = []

    def _run_loop(self, state):
        while state["running"]:
            settings = db.get_settings()
            delay_min = settings.get("delayMinSeconds", 30)
            delay_max = settings.get("delayMaxSeconds", 100)
            if delay_max < delay_min:
                delay_max = delay_min

            ids_snapshot = list(self.active_number_ids)
            if not ids_snapshot:
                time.sleep(5)
                continue

            sent = False
            for _ in range(len(ids_snapshot)):
                if not state["running"]:
                    break
                idx = self._rr_index % len(ids_snapshot)
                number_id = ids_snapshot[idx]
                self._rr_index = (idx + 1) % len(ids_snapshot)

                number = db.find_number_by_id(number_id)
                if not number or number.get("status") != "connected" or number.get("paused"):
                    continue

                check = safety.should_auto_pause(number_id, db.get_messages(), self.cfg)
                if check["pause"]:
                    db.update_number(number_id, {"paused": True, "pauseReason": f"Auto-paused: {check['reason']}"})
                    self.wa_manager._emit("status", {"numberId": number_id, "status": "auto_paused", "reason": check["reason"]})
                    continue

                now = datetime.now()
                cap = safety.get_daily_cap_for_number(number, self.cfg, now)
                sent_today = safety.count_sent_today(number_id, db.get_messages(), now)
                if sent_today >= cap:
                    continue

                nxt = self._next_contact_global()
                if not nxt:
                    continue
                self._send_one(number_id, nxt)
                sent = True
                time.sleep(random.uniform(delay_min, delay_max))
                break

            if not sent:
                time.sleep(5)

    def _next_contact_global(self):
        contacts = [c for c in db.get_contacts() if not c.get("optedOut") and not c.get("invalidNumber")]
        first_touch = next((c for c in contacts if c.get("messageStatus") == "queued" and safety.eligible_for_first_touch(c)), None)
        if first_touch:
            return {"contact": first_touch, "kind": "initial"}
        follow_up = next((c for c in contacts if c.get("followUpRequired") and safety.eligible_for_follow_up(c, self.cfg)), None)
        if follow_up:
            return {"contact": follow_up, "kind": "followup"}
        return None

    def _send_one(self, number_id, next_item):
        contact = next_item["contact"]
        kind = next_item["kind"]
        v = {
            "ownerName": contact.get("ownerName"),
            "unitNumber": contact.get("unitNumber"),
            "agentName": contact.get("assignedAgent") or self.agent_default_name,
            "companyName": self.company_name,
        }
        rendered = templates.render_for_contact(kind, contact, v, follow_up_index=contact.get("followUpCount") or 0, cfg=self.cfg)
        text, template_obj, greeting, opt_out_line = rendered["text"], rendered["templateObj"], rendered["greeting"], rendered["optOutLine"]

        # Use image send if the template has an attached image
        image_paths = db.get_settings().get("templateImagePaths") or {}
        image_path = image_paths.get(template_obj["id"])

        status, error_msg = "sent", None
        try:
            if image_path and Path(image_path).exists():
                self.wa_manager.send_image(number_id, contact["phoneE164"], image_path, text)
            else:
                self.wa_manager.send_text(number_id, contact["phoneE164"], text)
        except Exception as e:
            status, error_msg = "failed", str(e)

        db.log_message({
            "contactId": contact["contactId"], "numberId": number_id, "direction": "outbound",
            "body": text, "status": status, "templateId": template_obj["id"], "error": error_msg,
            "greetingVariant": greeting, "optOutLineVariant": opt_out_line,
        })

        now = datetime.now(timezone.utc).isoformat()
        patch = {"lastContactedAt": now, "messageStatus": "sent" if status == "sent" else "failed"}
        patch["assignedNumberId"] = number_id
        if kind == "initial":
            patch["firstContactedAt"] = now
        else:
            patch["followUpCount"] = (contact.get("followUpCount") or 0) + 1
            patch["followUpRequired"] = False
        db.update_contact(contact["contactId"], patch)
