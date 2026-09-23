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
    "salesAgentId": ["sales agent id", "agent id", "agent_id", "salesagentid", "agentid", "agent"],
}


def _normalize_header(h):
    return str(h or "").strip().lower()


def _map_row(headers, raw_row):
    out = {}
    for field, aliases in COLUMN_ALIASES.items():
        idx = next((i for i, h in enumerate(headers) if _normalize_header(h) in aliases), None)
        out[field] = str(raw_row[idx]).strip() if idx is not None and raw_row[idx] is not None else ""
    return out


def _is_header_row(row):
    """Return True if at least one cell matches a known column alias."""
    all_aliases = {alias for aliases in COLUMN_ALIASES.values() for alias in aliases}
    return any(_normalize_header(cell) in all_aliases for cell in row if cell is not None)


def parse_excel_buffer(file_bytes):
    wb = openpyxl.load_workbook(io.BytesIO(file_bytes), data_only=True)
    sheet = wb[wb.sheetnames[0]]
    all_rows = list(sheet.iter_rows(values_only=True))

    # Find the header row by scanning up to the first 10 rows.
    # This skips title banners and description rows that sit above the actual headers.
    header_idx = next(
        (i for i, row in enumerate(all_rows[:10]) if _is_header_row(row)),
        0,
    )
    headers = all_rows[header_idx]
    # Skip the description/hint row that immediately follows the header in the template
    # (it contains long instructional text rather than real data).
    data_rows = all_rows[header_idx + 1:]
    # Drop description/hint rows: any row whose mobile cell isn't digit-only
    # (after stripping +, spaces, dashes) is not real data.
    _PHONE_RE = re.compile(r"^[\+\d\s\-\(\)]{6,20}$")

    def _looks_like_hint(row):
        mapped = _map_row(headers, row)
        mobile = mapped.get("mobile", "").strip()
        return not mobile or not _PHONE_RE.match(mobile)

    data_rows = [r for r in data_rows if not _looks_like_hint(r)]
    return [_map_row(headers, row) for row in data_rows]


_UAE_LOCAL_RE = re.compile(r"^0?5\d{8}$")
_UAE_PREFIX_RE = re.compile(r"^971")


def standardize_phone(raw):
    """Standardizes to E.164. Defaults to UAE (+971) when the number has no country
    code and looks like a local UAE mobile (05x...). Returns dict(e164, valid, reason)."""
    if not raw:
        return {"e164": None, "valid": False, "reason": "empty"}
    raw_str = str(raw).strip()
    # Excel often exports phone numbers as floats, e.g. "971522478544.0"
    _float_match = re.match(r"^(\d+)\.0+$", raw_str)
    if _float_match:
        raw_str = _float_match.group(1)
    candidate = re.sub(r"[\s\-()]", "", raw_str)
    if candidate.startswith("00"):
        candidate = "+" + candidate[2:]
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
            "salesAgentId": row.get("salesAgentId") or None,
            "assignedAgent": None,
            "assignedNumberId": None,
            "campaignName": campaign_name,
            "templateVariant": None,
            "firstContactedAt": None,
            "lastContactedAt": None,
            "nextFollowUpAt": None,
            "followUpCount": 0,
            "messageStatus": "",
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
