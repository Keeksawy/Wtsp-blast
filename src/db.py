# Minimal JSON-file-backed data store. Fine for a single-operator tool at up to a
# few thousand contacts. Every other module only talks to the functions exported
# here, so swapping this for a real database later doesn't touch business logic.
import json
import os
import threading

DATA_DIR = os.environ.get("WA_DATA_DIR") or os.path.join(os.path.dirname(__file__), "..", "data")
DB_FILE = os.path.join(DATA_DIR, "db.json")

_lock = threading.RLock()
_state = None


def _empty_state():
    return {
        "contacts": [],
        "numbers": [],
        "messages": [],
        "crm_events": [],
        "users": [],
        "sessions": [],
        "meta": {"next_contact_seq": 1, "next_number_seq": 1, "next_message_seq": 1, "next_user_seq": 1},
        "settings": {"delayMinSeconds": 30, "delayMaxSeconds": 100, "initialTemplateOverrides": {}, "crmWebhookUrl": ""},
    }


def load():
    global _state
    with _lock:
        if _state is not None:
            return _state
        os.makedirs(DATA_DIR, exist_ok=True)
        if not os.path.exists(DB_FILE):
            _state = _empty_state()
            persist()
        else:
            with open(DB_FILE, "r", encoding="utf-8") as f:
                _state = json.load(f)
        return _state


def persist():
    global _state
    with _lock:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(DB_FILE, "w", encoding="utf-8") as f:
            json.dump(_state, f, indent=2, default=str)


# ---- contacts ----
def next_contact_id():
    s = load()
    with _lock:
        cid = f"OWN-{s['meta']['next_contact_seq']:06d}"
        s["meta"]["next_contact_seq"] += 1
        return cid


def get_contacts():
    return load()["contacts"]


def find_contact_by_phone(phone_e164):
    return next((c for c in load()["contacts"] if c.get("phoneE164") == phone_e164), None)


def find_contact_by_id(contact_id):
    return next((c for c in load()["contacts"] if c.get("contactId") == contact_id), None)


def add_contact(contact):
    s = load()
    with _lock:
        s["contacts"].append(contact)
        persist()
    return contact


def update_contact(contact_id, patch):
    s = load()
    with _lock:
        c = find_contact_by_id(contact_id)
        if c is None:
            raise ValueError(f"Contact {contact_id} not found")
        c.update(patch)
        persist()
    return c


def delete_contacts(predicate):
    """Remove all contacts where predicate(contact) is True. Returns count deleted."""
    s = load()
    with _lock:
        before = len(s["contacts"])
        s["contacts"] = [c for c in s["contacts"] if not predicate(c)]
        removed = before - len(s["contacts"])
        if removed:
            persist()
    return removed


# ---- numbers ----
def next_number_seq():
    s = load()
    with _lock:
        nid = s["meta"]["next_number_seq"]
        s["meta"]["next_number_seq"] += 1
        return nid


def get_numbers():
    return load()["numbers"]


def find_number_by_id(number_id):
    return next((n for n in load()["numbers"] if n.get("id") == number_id), None)


def add_number(number):
    s = load()
    with _lock:
        s["numbers"].append(number)
        persist()
    return number


def update_number(number_id, patch):
    s = load()
    with _lock:
        n = find_number_by_id(number_id)
        if n is None:
            raise ValueError(f"Number {number_id} not found")
        n.update(patch)
        persist()
    return n

def delete_number(number_id):
    s = load()
    with _lock:
        s["numbers"] = [n for n in s["numbers"] if n.get("id") != number_id]
        persist()



# ---- messages ----
def log_message(entry):
    import datetime
    s = load()
    with _lock:
        mid = s["meta"]["next_message_seq"]
        s["meta"]["next_message_seq"] += 1
        record = {"id": mid, "createdAt": datetime.datetime.utcnow().isoformat() + "Z", **entry}
        s["messages"].append(record)
        persist()
    return record


def log_crm_event(contact_id, intent, webhook_url, http_status, error):
    import datetime
    s = load()
    with _lock:
        s.setdefault("crm_events", []).append({
            "createdAt": datetime.datetime.utcnow().isoformat() + "Z",
            "contactId": contact_id,
            "intent": intent,
            "webhookUrl": webhook_url,
            "httpStatus": http_status,
            "error": error,
        })
        persist()


def get_crm_events(contact_id=None):
    events = load().get("crm_events", [])
    if contact_id:
        events = [e for e in events if e.get("contactId") == contact_id]
    return events


def get_messages(contact_id=None, number_id=None, direction=None):
    msgs = load()["messages"]
    if contact_id:
        msgs = [m for m in msgs if m.get("contactId") == contact_id]
    if number_id:
        msgs = [m for m in msgs if m.get("numberId") == number_id]
    if direction:
        msgs = [m for m in msgs if m.get("direction") == direction]
    return msgs


def reset_all():
    global _state
    with _lock:
        _state = _empty_state()
        persist()


DEFAULT_SETTINGS = {
    "delayMinSeconds": 30,
    "delayMaxSeconds": 100,
    "initialTemplateOverrides": {},
}


def get_settings():
    s = load()
    with _lock:
        if not isinstance(s.get("settings"), dict):
            s["settings"] = dict(DEFAULT_SETTINGS)
            persist()
        merged = dict(DEFAULT_SETTINGS)
        merged.update(s["settings"])
        if not isinstance(merged.get("initialTemplateOverrides"), dict):
            merged["initialTemplateOverrides"] = {}
        return merged


def update_settings(patch):
    s = load()
    with _lock:
        if not isinstance(s.get("settings"), dict):
            s["settings"] = dict(DEFAULT_SETTINGS)
        if "initialTemplateOverrides" in patch and isinstance(patch["initialTemplateOverrides"], dict):
            existing = s["settings"].get("initialTemplateOverrides")
            if not isinstance(existing, dict):
                existing = {}
            existing.update(patch["initialTemplateOverrides"])
            s["settings"]["initialTemplateOverrides"] = existing
            patch = {k: v for k, v in patch.items() if k != "initialTemplateOverrides"}
        s["settings"].update(patch)
        persist()
        return get_settings()


# ---- users ----

def _next_user_id():
    s = load()
    with _lock:
        uid = f"USR-{s['meta'].get('next_user_seq', 1):06d}"
        s['meta']['next_user_seq'] = s['meta'].get('next_user_seq', 1) + 1
        persist()
    return uid


def get_users():
    s = load()
    s.setdefault("users", [])
    return [{"userId": u["userId"], "email": u["email"], "role": u["role"], "createdAt": u["createdAt"]}
            for u in s["users"]]


def find_user_by_email(email):
    s = load()
    s.setdefault("users", [])
    return next((u for u in s["users"] if u.get("email", "").lower() == email.lower()), None)


def find_user_by_id(user_id):
    s = load()
    s.setdefault("users", [])
    return next((u for u in s["users"] if u.get("userId") == user_id), None)


def add_user(email, password_hash, role="user"):
    import datetime
    s = load()
    with _lock:
        s.setdefault("users", [])
        user = {
            "userId": _next_user_id(),
            "email": email.lower().strip(),
            "passwordHash": password_hash,
            "role": role,
            "createdAt": datetime.datetime.utcnow().isoformat() + "Z",
        }
        s["users"].append(user)
        persist()
    return {"userId": user["userId"], "email": user["email"], "role": user["role"], "createdAt": user["createdAt"]}


def delete_user(user_id):
    s = load()
    with _lock:
        s.setdefault("users", [])
        before = len(s["users"])
        s["users"] = [u for u in s["users"] if u.get("userId") != user_id]
        removed = before - len(s["users"])
        if removed:
            # also kill their sessions
            s.setdefault("sessions", [])
            s["sessions"] = [sess for sess in s["sessions"] if sess.get("userId") != user_id]
            persist()
    return removed > 0


def update_user_password(user_id, new_password_hash):
    s = load()
    with _lock:
        s.setdefault("users", [])
        user = next((u for u in s["users"] if u.get("userId") == user_id), None)
        if user:
            user["passwordHash"] = new_password_hash
            persist()
    return user is not None


# ---- sessions ----

def create_session(user_id, token, expiry_iso):
    s = load()
    with _lock:
        s.setdefault("sessions", [])
        s["sessions"].append({"token": token, "userId": user_id, "expiresAt": expiry_iso})
        persist()


def find_session(token):
    s = load()
    s.setdefault("sessions", [])
    return next((sess for sess in s["sessions"] if sess.get("token") == token), None)


def delete_session(token):
    s = load()
    with _lock:
        s.setdefault("sessions", [])
        s["sessions"] = [sess for sess in s["sessions"] if sess.get("token") != token]
        persist()


def purge_expired_sessions():
    from .auth import is_token_expired
    s = load()
    with _lock:
        s.setdefault("sessions", [])
        s["sessions"] = [sess for sess in s["sessions"] if not is_token_expired(sess.get("expiresAt", ""))]
        persist()
