# Excel/CSV import + cleaning: standardize phone numbers, dedupe, flag multi-unit
# owners and invalid numbers. 1:1 port of src/contacts.js (openpyxl + phonenumbers
# instead of xlsx + libphonenumber-js).
import re
import io
from datetime import datetime, timezone
import openpyxl
import phonenumbers
from . import db

COLUMN_ALIASES = {
    "ownerName": ["owner name", "owner_name", "name"],
    "mobile": ["mobile number", "mobile", "phone", "phone number", "mobile_number"],
    "unitNumber": ["unit number", "unit", "unit_number"],
}


def _normalize_header(h):
    return str(h or "").strip().lower()


def _map_row(headers, raw_row):
    out = {}
    for field, aliases in COLUMN_ALIASES.items():
        idx = next((i for i, h in enumerate(headers) if _normalize_header(h) in aliases), None)
        out[field] = str(raw_row[idx]).strip() if idx is not None and raw_row[idx] is not None else ""
    return out


def parse_excel_buffer(file_bytes):
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    sheet = wb[wb.sheetnames[0]]
    rows_iter = sheet.iter_rows(values_only=True)
    headers = next(rows_iter, [])
    return [_map_row(headers, row) for row in rows_iter]


_UAE_LOCAL_RE = re.compile(r"^0?5\d{8}$")
_UAE_PREFIX_RE = re.compile(r"^971")


def standardize_phone(raw):
    """Standardizes to E.164. Defaults to UAE (+971) when the number has no country
    code and looks like a local UAE mobile (05x...). Returns dict(e164, valid, reason)."""
    if not raw:
        return {"e164": None, "valid": False, "reason": "empty"}
    candidate = re.sub(r"[\s\-()]", "", str(raw).strip())
    if not candidate.startswith("+"):
        if _UAE_LOCAL_RE.match(candidate):
            candidate = "+971" + re.sub(r"^0", "", candidate)
        elif _UAE_PREFIX_RE.match(candidate):
            candidate = "+" + candidate
        else:
            candidate = "+" + candidate  # best effort; phonenumbers rejects if invalid
    try:
        parsed = phonenumbers.parse(candidate, "AE")
    except phonenumbers.NumberParseException:
        return {"e164": None, "valid": False, "reason": "unparseable_or_invalid"}
    if not phonenumbers.is_valid_number(parsed):
        return {"e164": None, "valid": False, "reason": "unparseable_or_invalid"}
    e164 = phonenumbers.format_number(parsed, phonenumbers.PhoneNumberFormat.E164)
    return {"e164": e164, "valid": True, "reason": None}


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def import_contacts(rows, campaign_name=None):
    summary = {"totalRows": len(rows), "added": 0, "mergedAsMultiUnit": 0, "invalid": 0, "duplicateSkipped": 0, "suppressedOptedOut": 0}
    existing = db.get_contacts()
    seen_in_batch = {}  # e164 -> contactId

    for row in rows:
        if not row.get("ownerName") and not row.get("mobile"):
            continue  # skip fully blank rows
        result = standardize_phone(row.get("mobile"))

        if not result["valid"]:
            db.add_contact({
                "contactId": db.next_contact_id(),
                "ownerName": row.get("ownerName"),
                "phoneRaw": row.get("mobile"),
                "phoneE164": None,
                "unitNumber": row.get("unitNumber"),
                "linkedUnits": [],
                "invalidNumber": True,
                "invalidReason": "Unparseable or invalid phone number",
                "optedOut": False,
                "campaignName": campaign_name,
                "createdAt": _now_iso(),
            })
            summary["invalid"] += 1
            continue

        e164 = result["e164"]

        prior_opt_out = next((c for c in existing if c.get("phoneE164") == e164 and c.get("optedOut")), None)
        if prior_opt_out:
            summary["suppressedOptedOut"] += 1
            continue

        if e164 in seen_in_batch:
            contact_id = seen_in_batch[e164]
            c = db.find_contact_by_id(contact_id)
            unit = row.get("unitNumber")
            if unit and unit not in c["linkedUnits"] and unit != c.get("unitNumber"):
                db.update_contact(contact_id, {"linkedUnits": c["linkedUnits"] + [unit]})
                summary["mergedAsMultiUnit"] += 1
            else:
                summary["duplicateSkipped"] += 1
            continue

        existing_contact = next((c for c in existing if c.get("phoneE164") == e164), None)
        if existing_contact:
            unit = row.get("unitNumber")
            if unit and unit not in existing_contact["linkedUnits"] and unit != existing_contact.get("unitNumber"):
                db.update_contact(existing_contact["contactId"], {"linkedUnits": existing_contact["linkedUnits"] + [unit]})
                summary["mergedAsMultiUnit"] += 1
            else:
                summary["duplicateSkipped"] += 1
            seen_in_batch[e164] = existing_contact["contactId"]
            continue

        contact_id = db.next_contact_id()
        db.add_contact({
            "contactId": contact_id,
            "ownerName": row.get("ownerName"),
            "phoneRaw": row.get("mobile"),
            "phoneE164": e164,
            "unitNumber": row.get("unitNumber"),
            "linkedUnits": [],
            "assignedAgent": None,
            "assignedNumberId": None,
            "campaignName": campaign_name,
            "templateVariant": None,
            "firstContactedAt": None,
            "lastContactedAt": None,
            "nextFollowUpAt": None,
            "followUpCount": 0,
            "messageStatus": "pending",
            "ownerResponse": None,
            "selling": None,
            "renting": None,
            "notInterested": False,
            "followUpRequired": False,
            "optedOut": False,
            "optedOutAt": None,
            "invalidNumber": False,
            "invalidReason": None,
            "contactChannelUsed": None,
            "notes": "",
            "createdAt": _now_iso(),
        })
        seen_in_batch[e164] = contact_id
        summary["added"] += 1

    return summary
