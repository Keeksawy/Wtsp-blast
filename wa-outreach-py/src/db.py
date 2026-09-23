# Minimal JSON-file-backed data store. Fine for a single-operator tool at up to a
# few thousand contacts. Every other module only talks to the functions exported
# here, so swapping this for a real database later doesn't touch business logic.
import json
import os
import threading

DATA_DIR = os.path.join(os.path.dirname(__file__), "..", "data")
DB_FILE = os.path.join(DATA_DIR, "db.json")

_lock = threading.RLock()
_state = None


def _empty_state():
    return {
        "contacts": [],
        "numbers": [],
        "messages": [],
        "meta": {"next_contact_seq": 1, "next_number_seq": 1, "next_message_seq": 1},
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
