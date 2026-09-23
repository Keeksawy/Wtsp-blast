# Flask API + dashboard server. 1:1 port of server.js — same routes, same JSON
# response shapes — so the existing public/ dashboard (index.html/app.js/style.css)
# works completely unchanged against this backend.
import os
import signal
import sys
from datetime import datetime

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory

load_dotenv()

sys.path.insert(0, os.path.dirname(__file__))
from config.defaults import DEFAULTS as cfg
from src import db, safety, templates, contacts as contacts_lib
from src.campaign import assign_contacts_to_numbers, CampaignRunner
from src.whatsapp_manager import WhatsAppManager

PORT = int(os.environ.get("PORT", 3000))
COMPANY_NAME = os.environ.get("COMPANY_NAME", "Our Agency")
DEFAULT_AGENT_NAME = os.environ.get("DEFAULT_AGENT_NAME", "Our Team")

app = Flask(__name__, static_folder=os.path.join(os.path.dirname(__file__), "public"), static_url_path="")

wa_manager = WhatsAppManager(cfg, company_name=COMPANY_NAME)
runner = CampaignRunner(wa_manager, cfg, company_name=COMPANY_NAME, agent_default_name=DEFAULT_AGENT_NAME)


@app.route("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


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


@app.route("/api/numbers/<int:number_id>/cap", methods=["POST"])
def set_cap(number_id):
    body = request.get_json(silent=True) or {}
    db.update_number(number_id, {"dailyCapOverride": body.get("dailyCapOverride") or None})
    return jsonify(db.find_number_by_id(number_id))


# ---- contacts ----
@app.route("/api/contacts/import", methods=["POST"])
def import_contacts_route():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400
    file = request.files["file"]
    campaign_name = request.form.get("campaignName") or f"Campaign-{datetime.utcnow().strftime('%Y-%m-%d')}"
    try:
        rows = contacts_lib.parse_excel_buffer(file.read())
        summary = contacts_lib.import_contacts(rows, campaign_name)
        return jsonify({"campaignName": campaign_name, **summary})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/contacts", methods=["GET"])
def get_contacts_route():
    return jsonify(db.get_contacts())


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
@app.route("/api/campaigns/assign", methods=["POST"])
def assign_route():
    body = request.get_json(silent=True) or {}
    number_ids = body.get("numberIds")
    if not isinstance(number_ids, list) or not number_ids:
        return jsonify({"error": "numberIds (array) required"}), 400
    result = assign_contacts_to_numbers(number_ids, campaign_name=body.get("campaignName"))
    return jsonify(result)


@app.route("/api/campaigns/start", methods=["POST"])
def start_route():
    body = request.get_json(silent=True) or {}
    number_ids = body.get("numberIds")
    if not (isinstance(number_ids, list) and number_ids):
        number_ids = [n["id"] for n in db.get_numbers() if n.get("status") == "connected"]
    for nid in number_ids:
        runner.start_number(nid)
    return jsonify({"started": number_ids})


@app.route("/api/campaigns/stop", methods=["POST"])
def stop_route():
    runner.stop_all()
    return jsonify({"ok": True})


# ---- templates ----
@app.route("/api/templates", methods=["GET"])
def templates_route():
    return jsonify({
        "initial": templates.INITIAL_TEMPLATES,
        "followup": templates.FOLLOWUP_TEMPLATES,
        "optout": templates.OPT_OUT_CONFIRMATION,
    })


# ---- dashboard ----
@app.route("/api/dashboard", methods=["GET"])
def dashboard_route():
    all_contacts = db.get_contacts()
    messages = db.get_messages()
    outbound = [m for m in messages if m.get("direction") == "outbound"]
    inbound = [m for m in messages if m.get("direction") == "inbound"]

    by_number = []
    for n in db.get_numbers():
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
    })


@app.route("/api/inbox", methods=["GET"])
def inbox_route():
    messages = db.get_messages(direction="inbound")[-100:]
    return jsonify(list(reversed(messages)))


def _shutdown(*_):
    print("\nShutting down...")
    runner.stop_all()
    wa_manager.shutdown()
    sys.exit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGINT, _shutdown)
    print(f"WA outreach dashboard running at http://localhost:{PORT}")
    app.run(host="0.0.0.0", port=PORT, threaded=True)
