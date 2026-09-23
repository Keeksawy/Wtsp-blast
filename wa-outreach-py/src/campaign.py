# Campaign assignment + per-number send workers. 1:1 port of src/campaign.js —
# same locking (one owner -> one number -> one agent, write-once), same worker loop
# shape, same reasoning. Python threads stand in for the Node event loop's
# per-number async workers.
import threading
import time
from datetime import datetime, timezone

from . import db, safety, templates


def assign_contacts_to_numbers(number_ids, campaign_name=None, agent_assigner=None):
    contacts = [
        c for c in db.get_contacts()
        if not c.get("optedOut") and not c.get("invalidNumber") and not c.get("assignedNumberId")
        and (not campaign_name or c.get("campaignName") == campaign_name)
    ]
    assigned = 0
    for i, c in enumerate(contacts):
        number_id = number_ids[i % len(number_ids)]
        agent = agent_assigner(c, i) if agent_assigner else None
        db.update_contact(c["contactId"], {
            "assignedNumberId": number_id,
            "assignedAgent": agent,
            "templateVariant": templates.pick_initial_variant(c["contactId"])["id"],
            "messageStatus": "queued",
        })
        assigned += 1
    return {"assigned": assigned, "totalEligible": len(contacts)}


class CampaignRunner:
    def __init__(self, wa_manager, cfg, company_name="Our Agency", agent_default_name="Our Team"):
        self.wa_manager = wa_manager
        self.cfg = cfg
        self.company_name = company_name
        self.agent_default_name = agent_default_name
        self.workers = {}  # number_id -> {"running": bool, "thread": Thread}

    def start_number(self, number_id):
        if number_id in self.workers:
            return
        state = {"running": True}
        t = threading.Thread(target=self._run_loop, args=(number_id, state), daemon=True)
        state["thread"] = t
        self.workers[number_id] = state
        t.start()

    def stop_number(self, number_id):
        state = self.workers.get(number_id)
        if state:
            state["running"] = False
        self.workers.pop(number_id, None)

    def stop_all(self):
        for number_id in list(self.workers.keys()):
            self.stop_number(number_id)

    def _run_loop(self, number_id, state):
        while state["running"]:
            number = db.find_number_by_id(number_id)
            if not number or number.get("status") != "connected" or number.get("paused"):
                time.sleep(15)
                continue

            check = safety.should_auto_pause(number_id, db.get_messages(), self.cfg)
            if check["pause"]:
                db.update_number(number_id, {"paused": True, "pauseReason": f"Auto-paused: {check['reason']}"})
                self.wa_manager._emit("status", {"numberId": number_id, "status": "auto_paused", "reason": check["reason"]})
                time.sleep(15)
                continue

            now = datetime.now()
            if not safety.is_within_business_hours(now, self.cfg):
                resume_at = safety.next_business_window_start(now, self.cfg)
                wait_s = min((resume_at - now).total_seconds(), 30 * 60)
                time.sleep(max(wait_s, 60))
                continue

            cap = safety.get_daily_cap_for_number(number, self.cfg, now)
            sent_today = safety.count_sent_today(number_id, db.get_messages(), now)
            if sent_today >= cap:
                time.sleep(10 * 60)
                continue

            nxt = self._next_contact_for_number(number_id)
            if not nxt:
                time.sleep(30)
                continue

            self._send_one(number_id, nxt)
            time.sleep(safety.jitter_delay_seconds(self.cfg))

    def _next_contact_for_number(self, number_id):
        contacts = [c for c in db.get_contacts() if c.get("assignedNumberId") == number_id and not c.get("optedOut") and not c.get("invalidNumber")]
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

        status, error_msg = "sent", None
        try:
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
        if kind == "initial":
            patch["firstContactedAt"] = now
        else:
            patch["followUpCount"] = (contact.get("followUpCount") or 0) + 1
            patch["followUpRequired"] = False
        db.update_contact(contact["contactId"], patch)
