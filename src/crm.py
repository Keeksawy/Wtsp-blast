"""
Fires a webhook POST to the configured CRM URL whenever a contact replies
with a sell or rent keyword. The payload is a generic JSON envelope —
map the fields to your CRM's expected format in the CRM's webhook settings.
"""
import json
import threading
import urllib.request
import urllib.error
from datetime import datetime, timezone

from . import db


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def send_lead(contact, intent, crm_webhook_url):
    """
    POST contact data + intent to the CRM webhook.
    intent: "sell" | "rent"
    Runs in a background thread so it never blocks the inbound polling loop.
    """
    if not crm_webhook_url:
        return

    payload = {
        "event": "lead_intent",
        "intent": intent,           # "sell" or "rent"
        "timestamp": _now_iso(),
        "contact": {
            "phone": contact.get("phoneE164") or contact.get("phoneRaw"),
            "name": contact.get("ownerName"),
            "unitNumber": contact.get("unitNumber"),
            "campaignName": contact.get("campaignName"),
        },
        "salesAgentId": contact.get("salesAgentId"),
    }

    def _post():
        try:
            data = json.dumps(payload).encode("utf-8")
            req = urllib.request.Request(
                crm_webhook_url,
                data=data,
                headers={"Content-Type": "application/json", "User-Agent": "wa-outreach/1.0"},
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = resp.status
            db.log_crm_event(contact.get("contactId"), intent, crm_webhook_url, status, None)
        except urllib.error.HTTPError as e:
            db.log_crm_event(contact.get("contactId"), intent, crm_webhook_url, e.code, str(e))
        except Exception as e:
            db.log_crm_event(contact.get("contactId"), intent, crm_webhook_url, None, str(e))

    threading.Thread(target=_post, daemon=True).start()


def detect_intent(text, cfg):
    """
    Returns "sell", "rent", or None based on keyword match.
    Checked after opt-out detection — opt-out takes priority.
    """
    lower = text.lower().strip()
    for kw in cfg.get("sell_keywords", []):
        if kw.lower() in lower:
            return "sell"
    for kw in cfg.get("rent_keywords", []):
        if kw.lower() in lower:
            return "rent"
    return None
