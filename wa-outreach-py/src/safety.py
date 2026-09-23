# Restriction-risk safety controls. 1:1 port of src/safety.js — same logic, same
# thresholds, same reasoning. Kept in sync deliberately; if you change one, change
# both (or better, treat the Node version as retired once this one is verified).
#
# Deliberately NOT included here, on principle (matches the design report's explicit
# exclusions): device/browser fingerprint spoofing, proxy/IP rotation, multi-SIM or
# emulator tricks, or anything else whose purpose is to make WhatsApp unable to see
# that these numbers belong to one business. Everything below is about sending
# responsibly and predictably, not evading detection.
#
# None of the numeric defaults are guaranteed safe thresholds — WhatsApp/Meta does
# not publish one. They are conservative starting points; watch delivery/reply/
# opt-out data per number and tune config/defaults.py accordingly.
#
# IMPORTANT: reply-rate monitoring below only counts genuine inbound messages from
# real recipients. Do not feed this signal by having numbers message/reply to each
# other or to synthetic contacts — that fabricates the exact evidence this check
# exists to require, and a cluster of numbers that only ever talk to each other is
# its own detectable pattern. The only legitimate way to raise this number is
# genuine recipient engagement (better targeting, better opt-in, better relevance).
from datetime import datetime, timedelta


def _parse(dt):
    if isinstance(dt, datetime):
        return dt
    return datetime.fromisoformat(str(dt).replace("Z", "+00:00")).replace(tzinfo=None)


def is_within_business_hours(dt, cfg):
    # Python weekday(): Mon=0..Sun=6. Convert to the same 0=Sun..6=Sat convention
    # used throughout config/defaults.py so the two ports stay directly comparable.
    sun0_day = (dt.weekday() + 1) % 7
    if sun0_day not in cfg["business_hours"]["days"]:
        return False
    return cfg["business_hours"]["start_hour"] <= dt.hour < cfg["business_hours"]["end_hour"]


def next_business_window_start(dt, cfg):
    d = dt
    for _ in range(8):
        sun0_day = (d.weekday() + 1) % 7
        if sun0_day in cfg["business_hours"]["days"]:
            window_start = d.replace(hour=cfg["business_hours"]["start_hour"], minute=0, second=0, microsecond=0)
            window_end = d.replace(hour=cfg["business_hours"]["end_hour"], minute=0, second=0, microsecond=0)
            if d < window_start:
                return window_start
            if window_start <= d < window_end:
                return d  # already in window
        d = (d + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)
    return d


def days_since(iso_date_string, now=None):
    now = now or datetime.utcnow()
    if not iso_date_string:
        return float("inf")
    then = _parse(iso_date_string)
    return (now - then).total_seconds() / 86400.0


def get_daily_cap_for_number(number, cfg, now=None):
    now = now or datetime.utcnow()
    if number.get("dailyCapOverride"):
        return number["dailyCapOverride"]
    active_days = days_since(number.get("activatedAt"), now) if number.get("activatedAt") else 0
    cap = cfg["warmup_ramp"][0]["daily_cap"]
    for step in cfg["warmup_ramp"]:
        if active_days >= step["min_days_active"]:
            cap = step["daily_cap"]
    return min(cap, cfg["steady_state_daily_cap_default"])


def count_sent_today(number_id, messages, now=None):
    now = now or datetime.utcnow()
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return len([
        m for m in messages
        if m.get("numberId") == number_id and m.get("direction") == "outbound"
        and _parse(m["createdAt"]) >= start_of_day
    ])


def jitter_delay_seconds(cfg):
    import random
    lo, hi = cfg["jitter_delay_seconds_range"]
    return lo + random.random() * (hi - lo)


def eligible_for_first_touch(contact):
    if contact.get("optedOut"):
        return False
    if contact.get("invalidNumber"):
        return False
    if contact.get("firstContactedAt"):
        return False
    return True


def eligible_for_follow_up(contact, cfg, now=None):
    now = now or datetime.utcnow()
    if contact.get("optedOut") or contact.get("invalidNumber"):
        return False
    if not contact.get("firstContactedAt"):
        return False
    if contact.get("notInterested"):
        return False
    if (contact.get("followUpCount") or 0) >= cfg["max_follow_ups"]:
        return False
    since_last = days_since(contact.get("lastContactedAt"), now)
    return since_last >= cfg["per_contact_frequency_cap_days"]


def detect_opt_out(text, cfg):
    if not text:
        return False
    t = text.strip().lower()
    return any(k.lower() in t for k in cfg["opt_out_keywords"])


def compute_reply_stats(number_id, messages, cfg, now=None):
    now = now or datetime.utcnow()
    eligible_outbound = [
        m for m in messages
        if m.get("numberId") == number_id and m.get("direction") == "outbound"
        and m.get("status") == "sent" and m.get("templateId") != "OPTOUT"
        and days_since(m["createdAt"], now) >= cfg["reply_monitoring"]["grace_period_days"]
    ]
    contact_ids = list({m["contactId"] for m in eligible_outbound if m.get("contactId")})
    replied = sum(
        1 for cid in contact_ids
        if any(m.get("contactId") == cid and m.get("direction") == "inbound" for m in messages)
    )
    total = len(contact_ids)
    return {
        "total": total,
        "replied": replied,
        "replyRate": (replied / total) if total > 0 else None,
    }


def get_reply_rate_status(number_id, messages, cfg, now=None):
    stats = compute_reply_stats(number_id, messages, cfg, now)
    if stats["total"] < cfg["reply_monitoring"]["min_evaluable_contacts"]:
        status = "insufficient_data"
    elif stats["replyRate"] <= cfg["reply_monitoring"]["pause_below_reply_rate"]:
        status = "critical"
    elif stats["replyRate"] < cfg["reply_monitoring"]["healthy_reply_rate"]:
        status = "warning"
    else:
        status = "healthy"
    return {**stats, "status": status}


def should_auto_pause(number_id, messages, cfg, now=None):
    now = now or datetime.utcnow()
    sent = [m for m in messages if m.get("numberId") == number_id and m.get("direction") == "outbound"]
    if len(sent) >= cfg["auto_pause"]["min_sends_before_evaluating"]:
        opt_outs = len([m for m in sent if m.get("resultedInOptOut")])
        failed = len([m for m in sent if m.get("status") in ("failed", "undeliverable")])
        opt_out_rate = opt_outs / len(sent)
        fail_rate = failed / len(sent)
        if opt_out_rate > cfg["auto_pause"]["max_opt_out_rate"]:
            return {"pause": True, "reason": f"Opt-out rate {opt_out_rate * 100:.1f}% exceeds threshold"}
        if fail_rate > cfg["auto_pause"]["max_undelivered_rate"]:
            return {"pause": True, "reason": f"Undelivered/failed rate {fail_rate * 100:.1f}% exceeds threshold"}

    reply_stats = compute_reply_stats(number_id, messages, cfg, now)
    if (reply_stats["total"] >= cfg["reply_monitoring"]["min_evaluable_contacts"]
            and reply_stats["replyRate"] is not None
            and reply_stats["replyRate"] <= cfg["reply_monitoring"]["pause_below_reply_rate"]):
        pct = cfg["reply_monitoring"]["pause_below_reply_rate"] * 100
        return {
            "pause": True,
            "reason": (
                f"Reply rate {reply_stats['replyRate'] * 100:.1f}% "
                f"({reply_stats['replied']}/{reply_stats['total']}) at/below the {pct:.0f}% "
                "threshold — near-zero engagement reads as spam regardless of volume"
            ),
        }

    return {"pause": False, "reason": None}
