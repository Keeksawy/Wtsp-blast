# Flask API + dashboard server. 1:1 port of server.js — same routes, same JSON
# response shapes — so the existing public/ dashboard (index.html/app.js/style.css)
# works completely unchanged against this backend.
import os
import signal
import sys
from datetime import datetime, timezone
import uuid

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory

load_dotenv()

sys.path.insert(0, os.path.dirname(__file__))
from config.defaults import DEFAULTS as cfg
from src import db, safety, templates, contacts as contacts_lib, auth as auth_lib
from src.campaign import queue_eligible_contacts, CampaignRunner
from src.whatsapp_manager import WhatsAppManager

PORT = int(os.environ.get("PORT", 3000))
COMPANY_NAME = os.environ.get("COMPANY_NAME", "Our Agency")
DEFAULT_AGENT_NAME = os.environ.get("DEFAULT_AGENT_NAME", "Our Team")
CRM_WEBHOOK_URL = os.environ.get("CRM_WEBHOOK_URL", "")
# First-run admin seed — set these in .env, then add users via the dashboard
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

app = Flask(__name__, static_folder=os.path.join(os.path.dirname(__file__), "public"), static_url_path="")


def _current_user():
    """Return the user record for the current request's session token, or None."""
    token = request.headers.get("X-Auth-Token") or request.args.get("token")
    if not token:
        return None
    session = db.find_session(token)
    if not session or auth_lib.is_token_expired(session.get("expiresAt", "")):
        return None
    return db.find_user_by_id(session["userId"])


def _require_admin():
    """Return (user, None) if the requester is an admin, else (None, error response)."""
    user = _current_user()
    if not user:
        return None, (jsonify({"error": "Unauthorized"}), 401)
    if user.get("role") != "admin":
        return None, (jsonify({"error": "Admin access required"}), 403)
    return user, None


PUBLIC_API_PATHS = {"/api/auth/login", "/api/auth/register"}

@app.before_request
def require_auth():
    """Block all /api/ routes except public paths unless the session token is valid."""
    if not request.path.startswith("/api/"):
        return
    if request.path in PUBLIC_API_PATHS:
        return
    if _current_user() is None:
        return jsonify({"error": "Unauthorized"}), 401

wa_manager = WhatsAppManager(cfg, company_name=COMPANY_NAME)
runner = CampaignRunner(wa_manager, cfg, company_name=COMPANY_NAME, agent_default_name=DEFAULT_AGENT_NAME)

# Seed CRM webhook URL from env if not already saved in DB
if CRM_WEBHOOK_URL and not db.get_settings().get("crmWebhookUrl"):
    db.update_settings({"crmWebhookUrl": CRM_WEBHOOK_URL})
# Seed company name and default agent name from env if not already saved
if not db.get_settings().get("companyName"):
    db.update_settings({"companyName": COMPANY_NAME})
if not db.get_settings().get("defaultAgentName"):
    db.update_settings({"defaultAgentName": DEFAULT_AGENT_NAME})

# Seed an initial admin from environment variables — set ADMIN_EMAIL and
# ADMIN_PASSWORD before first launch. If neither is set and no users exist,
# the first person who registers via the UI becomes admin automatically.
_seed_email = os.environ.get("ADMIN_EMAIL", "").strip().lower()
_seed_pw    = os.environ.get("ADMIN_PASSWORD", "").strip()
if _seed_email and _seed_pw and not db.get_users():
    if auth_lib.is_allowed_email(_seed_email):
        db.add_user(_seed_email, auth_lib.hash_password(_seed_pw), role="admin")
        print(f"[auth] Seeded admin user: {_seed_email}")


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


# ---- auth ----
@app.route("/api/auth/login", methods=["POST"])
def auth_login():
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    if not auth_lib.is_allowed_email(email):
        return jsonify({"error": "Only @drivenproperties.com accounts are allowed"}), 403
    user = db.find_user_by_email(email)
    if not user or not auth_lib.verify_password(password, user.get("passwordHash", "")):
        return jsonify({"error": "Invalid email or password"}), 401
    token = auth_lib.generate_token()
    db.create_session(user["userId"], token, auth_lib.token_expiry())
    db.purge_expired_sessions()
    return jsonify({"token": token, "email": user["email"], "role": user["role"]})


@app.route("/api/auth/register", methods=["POST"])
def auth_register():
    """Self-service registration — open to anyone with a @drivenproperties.com email."""
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    if not auth_lib.is_allowed_email(email):
        return jsonify({"error": "Only @drivenproperties.com email addresses can register"}), 403
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400
    if db.find_user_by_email(email):
        return jsonify({"error": "An account with that email already exists"}), 409
    # First user on this machine automatically becomes admin
    existing_users = db.get_users()
    role = "admin" if not existing_users else "user"
    user = db.add_user(email, auth_lib.hash_password(password), role=role)
    # Auto sign-in after registration
    token = auth_lib.generate_token()
    db.create_session(user["userId"], token, auth_lib.token_expiry())
    return jsonify({"token": token, "email": user["email"], "role": user["role"]}), 201


@app.route("/api/auth/logout", methods=["POST"])
def auth_logout():
    token = request.headers.get("X-Auth-Token") or ""
    db.delete_session(token)
    return jsonify({"ok": True})


@app.route("/api/auth/me", methods=["GET"])
def auth_me():
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    return jsonify({"userId": user["userId"], "email": user["email"], "role": user["role"], "createdAt": user.get("createdAt")})


# ---- user management (admin only) ----
@app.route("/api/users", methods=["GET"])
def list_users():
    _, err = _require_admin()
    if err:
        return err
    return jsonify(db.get_users())


@app.route("/api/users", methods=["POST"])
def create_user():
    _, err = _require_admin()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    email = (body.get("email") or "").strip().lower()
    password = body.get("password") or ""
    role = body.get("role", "user")
    if not auth_lib.is_allowed_email(email):
        return jsonify({"error": "Only @drivenproperties.com email addresses are allowed"}), 400
    if len(password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400
    if role not in ("admin", "user"):
        role = "user"
    if db.find_user_by_email(email):
        return jsonify({"error": "A user with that email already exists"}), 409
    user = db.add_user(email, auth_lib.hash_password(password), role)
    return jsonify(user), 201


@app.route("/api/users/<user_id>", methods=["DELETE"])
def delete_user(user_id):
    requester, err = _require_admin()
    if err:
        return err
    if requester["userId"] == user_id:
        return jsonify({"error": "You cannot delete your own account"}), 400
    removed = db.delete_user(user_id)
    if not removed:
        return jsonify({"error": "User not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/users/<user_id>/role", methods=["POST"])
def change_user_role(user_id):
    _, err = _require_admin()
    if err:
        return err
    body = request.get_json(silent=True) or {}
    role = body.get("role", "user")
    if role not in ("admin", "user"):
        return jsonify({"error": "Role must be 'admin' or 'user'"}), 400
    s = db.load()
    with db._lock:
        user = next((u for u in s.get("users", []) if u["userId"] == user_id), None)
        if not user:
            return jsonify({"error": "User not found"}), 404
        user["role"] = role
        db.persist()
    return jsonify({"ok": True, "userId": user_id, "role": role})


@app.route("/api/users/<user_id>/password", methods=["POST"])
def change_password(user_id):
    requester = _current_user()
    if not requester:
        return jsonify({"error": "Unauthorized"}), 401
    # Admins can reset anyone's password; regular users can only change their own
    if requester["role"] != "admin" and requester["userId"] != user_id:
        return jsonify({"error": "Forbidden"}), 403
    body = request.get_json(silent=True) or {}
    new_password = body.get("password") or ""
    if len(new_password) < 8:
        return jsonify({"error": "Password must be at least 8 characters"}), 400
    ok = db.update_user_password(user_id, auth_lib.hash_password(new_password))
    if not ok:
        return jsonify({"error": "User not found"}), 404
    return jsonify({"ok": True})


# ---- numbers ----
@app.route("/api/numbers", methods=["GET"])
def get_numbers():
    messages = db.get_messages()
    out = []
    for n in db.get_numbers():
        out.append({
            **n,
            "qr": wa_manager.get_qr(n["id"]),
            "dailyCap": safety.get_daily_cap_for_number(n, cfg),
            "sentToday": safety.count_sent_today(n["id"], messages),
            "replyRate": safety.get_reply_rate_status(n["id"], messages, cfg),
        })
    return jsonify(out)


@app.route("/api/numbers", methods=["POST"])
def add_number():
    body = request.get_json(silent=True) or {}
    label = body.get("label") or f"Line {len(db.get_numbers()) + 1}"
    try:
        number = wa_manager.add_number(label)
        return jsonify(number)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/numbers/<int:number_id>/pause", methods=["POST"])
def pause_number(number_id):
    body = request.get_json(silent=True) or {}
    wa_manager.pause_number(number_id, body.get("reason"))
    runner.stop_number(number_id)
    return jsonify({"ok": True})


@app.route("/api/numbers/<int:number_id>/resume", methods=["POST"])
def resume_number(number_id):
    wa_manager.resume_number(number_id)
    runner.start_number(number_id)
    return jsonify({"ok": True})


@app.route("/api/numbers/<int:number_id>/reconnect", methods=["POST"])
def reconnect_number(number_id):
    try:
        wa_manager.reconnect_number(number_id)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/numbers/<int:number_id>/cap", methods=["POST"])
def set_cap(number_id):
    body = request.get_json(silent=True) or {}
    db.update_number(number_id, {"dailyCapOverride": body.get("dailyCapOverride") or None})
    return jsonify(db.find_number_by_id(number_id))




@app.route("/api/numbers/<int:number_id>", methods=["DELETE"])
def delete_number_route(number_id):
    runner.stop_number(number_id)
    wa_manager.remove_number(number_id)
    db.delete_number(number_id)
    return jsonify({"ok": True})

# ---- contacts ----
@app.route("/api/contacts/preview", methods=["POST"])
def preview_contacts_route():
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files["file"]
    try:
        rows = contacts_lib.parse_excel_buffer(file.read(), filename=file.filename or "")
        return jsonify(contacts_lib.preview_import(rows))
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/contacts/import-rows", methods=["POST"])
def import_rows_route():
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    data = request.get_json() or {}
    rows = (data.get("rows") or []) + (data.get("corrections") or [])
    campaign_name = data.get("campaignName") or f"Campaign-{datetime.utcnow().strftime('%Y-%m-%d')}"
    try:
        summary = contacts_lib.import_contacts(rows, campaign_name)
        db.update_settings({"lastCampaignName": campaign_name})
        return jsonify({"campaignName": campaign_name, **summary})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/contacts/import", methods=["POST"])
def import_contacts_route():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files["file"]
    campaign_name = request.form.get("campaignName") or f"Campaign-{datetime.utcnow().strftime('%Y-%m-%d')}"
    try:
        rows = contacts_lib.parse_excel_buffer(file.read(), filename=file.filename or "")
        summary = contacts_lib.import_contacts(rows, campaign_name)
        db.update_settings({"lastCampaignName": campaign_name})
        return jsonify({"campaignName": campaign_name, **summary})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/contacts", methods=["GET"])
def get_contacts_route():
    contacts = db.get_contacts()
    # Build a map of contactId -> last error string for failed sends,
    # so the UI can show the reason without opening db.json directly.
    messages = db.get_messages(direction="outbound")
    last_error = {}
    for m in messages:
        cid = m.get("contactId")
        if cid and m.get("error"):
            last_error[cid] = m["error"]
    for c in contacts:
        c["lastSendError"] = last_error.get(c["contactId"])
    return jsonify(contacts)


@app.route("/api/contacts/<contact_id>/outcome", methods=["POST"])
def contact_outcome(contact_id):
    body = request.get_json(silent=True) or {}
    patch = {}
    for key in ("selling", "renting", "notInterested", "followUpRequired", "notes"):
        if key in body:
            patch[key] = body[key]
    if body.get("optedOut"):
        patch["optedOut"] = True
        patch["optedOutAt"] = datetime.utcnow().isoformat()
    try:
        updated = db.update_contact(contact_id, patch)
        return jsonify(updated)
    except ValueError as e:
        return jsonify({"error": str(e)}), 404


# ---- campaign control ----
@app.route("/api/campaigns/start", methods=["POST"])
def start_route():
    body = request.get_json(silent=True) or {}
    campaign_name = body.get("campaignName") or None
    # Auto-queue all eligible contacts (no manual assign step needed)
    queued = queue_eligible_contacts(campaign_name=campaign_name)
    # Start across all connected numbers
    number_ids = [n["id"] for n in db.get_numbers() if n.get("status") == "connected"]
    for nid in number_ids:
        runner.start_number(nid)
    return jsonify({"started": number_ids, "queued": queued["queued"]})


@app.route("/api/campaigns/stop", methods=["POST"])
def stop_route():
    runner.stop_all()
    return jsonify({"ok": True})


# ---- templates ----
@app.route("/api/templates", methods=["GET"])
def templates_route():
    return jsonify({
            "initial": _effective_initial_templates(),
            "followup": templates.FOLLOWUP_TEMPLATES,
            "optout": templates.OPT_OUT_CONFIRMATION,    
        })


def _effective_initial_templates():
    settings = db.get_settings()
    overrides = settings.get("initialTemplateOverrides", {}) or {}
    out = []
    for t in templates.INITIAL_TEMPLATES:
        merged = dict(t)
        if overrides.get(t["id"]):
            merged["text"] = overrides[t["id"]]
            merged["isOverridden"] = True
        else:
            merged["isOverridden"] = False
        out.append(merged)
    return out


@app.route("/api/templates/initial", methods=["POST"])
def update_initial_templates_route():
    body = request.get_json(silent=True) or {}
    overrides = body.get("overrides")
    if not isinstance(overrides, dict):
        return jsonify({"error": "overrides (object of templateId -> text) required"}), 400
    valid_ids = {t["id"] for t in templates.INITIAL_TEMPLATES}
    clean = {}
    for tid, text in overrides.items():
        if tid not in valid_ids:
            continue
        if isinstance(text, str) and text.strip():
            clean[tid] = text
        else:
            clean[tid] = None
    settings = db.update_settings({"initialTemplateOverrides": clean})
    return jsonify({"initial": _effective_initial_templates(), "settings": settings})



# ---- settings (delay range, template overrides) ----
@app.route("/api/settings", methods=["GET"])
def get_settings_route():
    return jsonify(db.get_settings())


@app.route("/api/settings", methods=["POST"])
def update_settings_route():
    body = request.get_json(silent=True) or {}
    patch = {}
    if "delayMinSeconds" in body:
        try:
            patch["delayMinSeconds"] = max(5, int(body["delayMinSeconds"]))
        except (TypeError, ValueError):
            return jsonify({"error": "delayMinSeconds must be a number"}), 400
    if "delayMaxSeconds" in body:
        try:
            patch["delayMaxSeconds"] = max(5, int(body["delayMaxSeconds"]))
        except (TypeError, ValueError):
            return jsonify({"error": "delayMaxSeconds must be a number"}), 400
    if "delayMinSeconds" in patch and "delayMaxSeconds" in patch and patch["delayMaxSeconds"] < patch["delayMinSeconds"]:
        patch["delayMaxSeconds"] = patch["delayMinSeconds"]
    if "crmWebhookUrl" in body:
        patch["crmWebhookUrl"] = str(body["crmWebhookUrl"]).strip()
    if "companyName" in body:
        patch["companyName"] = str(body["companyName"]).strip()
    if "defaultAgentName" in body:
        patch["defaultAgentName"] = str(body["defaultAgentName"]).strip()
    settings = db.update_settings(patch)
    return jsonify(settings)


# ---- dashboard ----
@app.route("/api/dashboard", methods=["GET"])
def dashboard_route():
    all_contacts = db.get_contacts()
    messages = db.get_messages()
    outbound = [m for m in messages if m.get("direction") == "outbound"]
    inbound = [m for m in messages if m.get("direction") == "inbound"]

    numbers = db.get_numbers()
    by_number = []
    for n in numbers:
        sent = [m for m in outbound if m.get("numberId") == n["id"]]
        delivered = [m for m in sent if m.get("status") == "sent"]
        optouts = len([m for m in sent if m.get("resultedInOptOut")])
        by_number.append({
            "numberId": n["id"], "label": n["label"], "status": n["status"], "paused": n.get("paused"),
            "sends": len(sent), "delivered": len(delivered), "optOuts": optouts,
            "dailyCap": safety.get_daily_cap_for_number(n, cfg), "sentToday": safety.count_sent_today(n["id"], messages),
            "replyRate": safety.get_reply_rate_status(n["id"], messages, cfg),
        })

    by_variant = {t["id"]: {"sends": 0, "replies": 0, "optOuts": 0} for t in templates.INITIAL_TEMPLATES}
    for c in all_contacts:
        variant = c.get("templateVariant")
        if variant and variant in by_variant and c.get("firstContactedAt"):
            by_variant[variant]["sends"] += 1
        if variant and variant in by_variant and c.get("optedOut"):
            by_variant[variant]["optOuts"] += 1

    last_campaign_name = db.get_settings().get("lastCampaignName")
    campaign_contacts = [c for c in all_contacts if c.get("campaignName") == last_campaign_name] if last_campaign_name else []
    campaign_ids = {c["contactId"] for c in campaign_contacts}
    campaign_outbound = [m for m in outbound if m.get("contactId") in campaign_ids]
    campaign_inbound = [m for m in inbound if m.get("contactId") in campaign_ids]
    current_campaign = None
    if last_campaign_name:
        current_campaign = {
            "name": last_campaign_name,
            "totalContacts": len(campaign_contacts),
            "validNumbers": len([c for c in campaign_contacts if not c.get("invalidNumber")]),
            "invalidNumbers": len([c for c in campaign_contacts if c.get("invalidNumber")]),
            "messagesSent": len([m for m in campaign_outbound if m.get("status") == "sent"]),
            "messagesFailed": len([m for m in campaign_outbound if m.get("status") == "failed"]),
            "repliesReceived": len(campaign_inbound),
            "interestedSelling": len([c for c in campaign_contacts if c.get("selling") == "Yes"]),
            "interestedRenting": len([c for c in campaign_contacts if c.get("renting") == "Yes"]),
            "notInterested": len([c for c in campaign_contacts if c.get("notInterested")]),
            "optedOut": len([c for c in campaign_contacts if c.get("optedOut")]),
        }

    contact_map = {c["contactId"]: c for c in all_contacts}
    number_map  = {n["id"]: n for n in numbers}
    recent_sorted = sorted(
        [m for m in outbound if m.get("createdAt")],
        key=lambda m: m.get("createdAt", ""),
        reverse=True
    )[:15]
    TEMPLATE_NAMES = {"A": "Direct", "B": "Question-first", "C": "Market-context", "D": "Short", "E": "Courtesy"}
    recent_activity = [
        {
            "ownerName":    contact_map.get(m.get("contactId"), {}).get("ownerName", "Unknown"),
            "phone":        contact_map.get(m.get("contactId"), {}).get("phoneE164", ""),
            "status":       m.get("status", ""),
            "numberLabel":  number_map.get(m.get("numberId"), {}).get("label", ""),
            "sentAt":       m.get("createdAt", ""),
            "templateId":   m.get("templateId", ""),
            "templateName": TEMPLATE_NAMES.get(m.get("templateId", ""), ""),
        }
        for m in recent_sorted
    ]

    return jsonify({
        "totalContacts": len(all_contacts),
        "validNumbers": len([c for c in all_contacts if not c.get("invalidNumber")]),
        "invalidNumbers": len([c for c in all_contacts if c.get("invalidNumber")]),
        "multiUnitOwners": len([c for c in all_contacts if c.get("linkedUnits")]),
        "messagesSent": len([m for m in outbound if m.get("status") == "sent"]),
        "messagesFailed": len([m for m in outbound if m.get("status") == "failed"]),
        "repliesReceived": len(inbound),
        "interestedSelling": len([c for c in all_contacts if c.get("selling") == "Yes"]),
        "interestedRenting": len([c for c in all_contacts if c.get("renting") == "Yes"]),
        "notInterested": len([c for c in all_contacts if c.get("notInterested")]),
        "optedOut": len([c for c in all_contacts if c.get("optedOut")]),
        "byNumber": by_number,
        "byVariant": by_variant,
        "currentCampaign": current_campaign,
        "recentActivity": recent_activity,
    })

@app.route("/api/contacts/<contact_id>/requeue", methods=["POST"])
def requeue_contact_route(contact_id):
    connected = [n for n in db.get_numbers() if n.get("status") == "connected"]
    if not connected:
        return jsonify({"ok": False, "error": "No WhatsApp number is connected. Please connect a number first before retrying."}), 400
    db.update_contact(contact_id, {
        "messageStatus": "",          # empty string matches queue_eligible_contacts(); None does not
        "optedOut": False,
        "notInterested": False,
        "invalidNumber": False,
        "firstContactedAt": None,
        "lastContactedAt": None,
        "followUpRequired": False,
        "nextFollowUpAt": None,
    })
    return jsonify({"ok": True})


@app.route("/api/contacts/<contact_id>", methods=["DELETE"])
def delete_contact_route(contact_id):
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    removed = db.delete_contacts(lambda c: c.get("contactId") == contact_id)
    if not removed:
        return jsonify({"error": "Contact not found"}), 404
    return jsonify({"ok": True})


@app.route("/api/contacts/<contact_id>", methods=["PATCH"])
def update_contact_route(contact_id):
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    body = request.get_json(silent=True) or {}
    patch = {}
    if "status" in body:
        s = str(body["status"]).strip()
        allowed = {"queued", "sent", "failed", "opted-out", "not-interested", "do-not-contact"}
        if s not in allowed:
            return jsonify({"error": f"Invalid status. Must be one of: {', '.join(sorted(allowed))}"}), 400
        if s == "opted-out":
            patch["optedOut"] = True
            patch["optedOutAt"] = datetime.now(timezone.utc).isoformat()
            patch["notInterested"] = False
        elif s == "not-interested":
            patch["optedOut"] = False
            patch["notInterested"] = True
            patch["messageStatus"] = s
        elif s == "do-not-contact":
            patch["optedOut"] = False
            patch["notInterested"] = True   # also blocks follow-ups
            patch["messageStatus"] = s
        else:
            patch["optedOut"] = False
            patch["notInterested"] = False
            patch["messageStatus"] = s
    if "notes" in body:
        patch["notes"] = str(body.get("notes", "")).strip()
    if not patch:
        return jsonify({"error": "Nothing to update"}), 400
    try:
        contact = db.update_contact(contact_id, patch)
    except ValueError:
        return jsonify({"error": "Contact not found"}), 404
    return jsonify({"ok": True, "contact": contact})


@app.route("/api/contacts/clear-invalid", methods=["POST"])
def clear_invalid_contacts_route():
    removed = db.delete_contacts(lambda c: c.get("invalidNumber") or not c.get("phoneE164"))
    return jsonify({"removed": removed})


@app.route("/api/contacts/clear-all", methods=["POST"])
def clear_all_contacts_route():
    removed = db.delete_contacts(lambda c: True)
    return jsonify({"removed": removed})


@app.route("/api/reset", methods=["POST"])
def reset_route():
    """Stop all number connections and wipe contacts, messages, and numbers.
    Settings and users are preserved."""
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    for n in db.get_numbers():
        nid = n.get("id")
        if nid:
            try: runner.stop_number(nid)
            except Exception: pass
            try: wa_manager.remove_number(nid)
            except Exception: pass
    db.reset_campaign_data()
    return jsonify({"ok": True})


@app.route("/api/inbox", methods=["GET"])
def inbox_route():
    """Return all messages (both directions) grouped into conversations by contact."""
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    all_msgs = db.get_messages()
    contacts_map = {c["contactId"]: c for c in db.get_contacts()}

    # Group by contactId (or phone for unmatched)
    threads = {}
    for m in all_msgs:
        key = m.get("contactId") or m.get("phone") or "unknown"
        if key not in threads:
            contact = contacts_map.get(m.get("contactId")) if m.get("contactId") else None
            threads[key] = {
                "contactId": m.get("contactId"),
                "contactName": contact["ownerName"] if contact else m.get("phone", "Unknown"),
                "phone": contact["phoneE164"] if contact else m.get("phone"),
                "unitNumber": contact.get("unitNumber") if contact else None,
                "salesAgentId": contact.get("salesAgentId") if contact else None,
                "numberId": m.get("numberId"),
                "messages": [],
                "lastMessageAt": None,
                "hasUnreplied": False,
                "unreadCount": 0,
            }
        threads[key]["messages"].append(m)
        if not threads[key]["lastMessageAt"] or m.get("createdAt", "") > threads[key]["lastMessageAt"]:
            threads[key]["lastMessageAt"] = m.get("createdAt")

    # Mark threads where last message is inbound and count unread since last read
    for t in threads.values():
        sorted_msgs = sorted(t["messages"], key=lambda x: x.get("createdAt", ""))
        t["messages"] = sorted_msgs
        contact = contacts_map.get(t.get("contactId"))
        last_read_at = (contact or {}).get("lastInboxReadAt") or ""
        unread = sum(
            1 for m in sorted_msgs
            if m.get("direction") == "inbound" and m.get("createdAt", "") > last_read_at
        )
        t["unreadCount"] = unread
        t["hasUnreplied"] = unread > 0

    result = sorted(threads.values(), key=lambda x: x.get("lastMessageAt") or "", reverse=True)
    return jsonify(result)


@app.route("/api/inbox/read/<contact_id>", methods=["POST"])
def inbox_mark_read(contact_id):
    """Mark all messages in a thread as read by recording the current timestamp."""
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401
    import datetime
    now = datetime.datetime.utcnow().isoformat() + "Z"
    try:
        db.update_contact(contact_id, {"lastInboxReadAt": now})
    except ValueError:
        pass
    return jsonify({"ok": True})


@app.route("/api/inbox/reply", methods=["POST"])
def inbox_reply():
    """Send a manual reply to a contact from the dashboard."""
    user = _current_user()
    if not user:
        return jsonify({"error": "Unauthorized"}), 401

    body = request.get_json(force=True)
    contact_id = body.get("contactId")
    number_id = body.get("numberId")
    text = (body.get("text") or "").strip()

    if not text:
        return jsonify({"error": "Message text is required"}), 400

    contact = db.find_contact_by_id(contact_id) if contact_id else None
    if not contact:
        return jsonify({"error": "Contact not found"}), 404

    phone = contact.get("phoneE164")
    if not phone:
        return jsonify({"error": "Contact has no phone number"}), 400

    # Use the number that originally sent to this contact, or first active number
    if not number_id:
        number_id = contact.get("assignedNumberId")
    if not number_id:
        nums = db.get_numbers()
        active = [n for n in nums if n.get("status") == "connected"]
        if not active:
            return jsonify({"error": "No connected WhatsApp numbers available"}), 503
        number_id = active[0]["id"]

    try:
        wa_manager.send_text(number_id, phone, text)
        db.log_message({
            "contactId": contact_id,
            "numberId": number_id,
            "direction": "outbound",
            "body": text,
            "status": "sent",
            "manualReply": True,
        })
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _shutdown(*_):
    print("\nShutting down...")
    runner.stop_all()
    wa_manager.shutdown()
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, _shutdown)
    print(f"WA outreach dashboard running at http://localhost:{PORT}")
    app.run(host="0.0.0.0", port=PORT, threaded=True)
