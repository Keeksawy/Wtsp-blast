# Pytest port of test/run_tests.js — same cases, same coverage, for the Python
# rewrite (config/defaults.py, src/safety.py, src/templates.py, src/contacts.py).
import io
import os
import sys
from datetime import datetime, timedelta

import openpyxl
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from config.defaults import DEFAULTS as cfg
from src import db, safety, templates, contacts


@pytest.fixture(autouse=True)
def reset_db():
    db.reset_all()
    yield


# ---------------- safety.py ----------------
def test_business_hours_true_sun_10am():
    d = datetime(2026, 8, 2, 10, 0, 0)  # Sunday
    assert safety.is_within_business_hours(d, cfg) is True


def test_business_hours_false_friday():
    d = datetime(2026, 8, 7, 10, 0, 0)  # Friday
    assert safety.is_within_business_hours(d, cfg) is False


def test_business_hours_false_before_9am():
    d = datetime(2026, 8, 2, 7, 0, 0)
    assert safety.is_within_business_hours(d, cfg) is False


def test_daily_cap_brand_new_number():
    num = {"activatedAt": datetime.utcnow().isoformat()}
    assert safety.get_daily_cap_for_number(num, cfg) == 8


def test_daily_cap_day_10_milestone():
    num = {"activatedAt": (datetime.utcnow() - timedelta(days=10)).isoformat()}
    assert safety.get_daily_cap_for_number(num, cfg) == 35


def test_daily_cap_day_30_established():
    num = {"activatedAt": (datetime.utcnow() - timedelta(days=30)).isoformat()}
    assert safety.get_daily_cap_for_number(num, cfg) == 90


def test_daily_cap_steady_state():
    num = {"activatedAt": (datetime.utcnow() - timedelta(days=200)).isoformat()}
    assert safety.get_daily_cap_for_number(num, cfg) == 300


def test_daily_cap_override_wins():
    num = {"activatedAt": datetime.utcnow().isoformat(), "dailyCapOverride": 5}
    assert safety.get_daily_cap_for_number(num, cfg) == 5


def test_jitter_within_range():
    for _ in range(50):
        d = safety.jitter_delay_seconds(cfg)
        assert cfg["jitter_delay_seconds_range"][0] <= d <= cfg["jitter_delay_seconds_range"][1]


def test_eligible_first_touch_false_opted_out():
    assert safety.eligible_for_first_touch({"optedOut": True}) is False


def test_eligible_first_touch_false_already_contacted():
    assert safety.eligible_for_first_touch({"firstContactedAt": "2026-01-01"}) is False


def test_eligible_first_touch_true_fresh():
    assert safety.eligible_for_first_touch({}) is True


def test_eligible_followup_false_before_cap():
    c = {"firstContactedAt": "2026-07-01", "lastContactedAt": datetime.utcnow().isoformat(), "followUpCount": 0}
    assert safety.eligible_for_follow_up(c, cfg) is False


def test_eligible_followup_true_after_cap():
    old = (datetime.utcnow() - timedelta(days=31)).isoformat()
    c = {"firstContactedAt": old, "lastContactedAt": old, "followUpCount": 0}
    assert safety.eligible_for_follow_up(c, cfg) is True


def test_detect_optout_matches_stop():
    assert safety.detect_opt_out("please STOP messaging me", cfg) is True


def test_detect_optout_no_false_positive():
    assert safety.detect_opt_out("yes I am interested in renting", cfg) is False


def test_auto_pause_below_min_sample():
    msgs = [{"numberId": 1, "direction": "outbound", "resultedInOptOut": True}]
    assert safety.should_auto_pause(1, msgs, cfg)["pause"] is False


def test_auto_pause_optout_rate_exceeds():
    msgs = [{"numberId": 1, "direction": "outbound", "resultedInOptOut": i < 3} for i in range(20)]
    assert safety.should_auto_pause(1, msgs, cfg)["pause"] is True


def _outbound_at(contact_id, days_ago):
    return {
        "numberId": 1, "contactId": contact_id, "direction": "outbound", "status": "sent", "templateId": "A",
        "createdAt": (datetime.utcnow() - timedelta(days=days_ago)).isoformat(),
    }


def test_reply_stats_ignores_grace_period():
    msgs = [_outbound_at("C1", 1)]
    stats = safety.compute_reply_stats(1, msgs, cfg)
    assert stats["total"] == 0
    assert stats["replyRate"] is None


def test_reply_stats_counts_real_inbound_only():
    msgs = [
        _outbound_at("C1", 10),
        {"numberId": 1, "contactId": "C1", "direction": "inbound", "body": "sure", "createdAt": datetime.utcnow().isoformat()},
        _outbound_at("C2", 10),
    ]
    stats = safety.compute_reply_stats(1, msgs, cfg)
    assert stats["total"] == 2
    assert stats["replied"] == 1
    assert stats["replyRate"] == 0.5


def test_reply_rate_status_insufficient_data():
    msgs = [_outbound_at("C1", 10)]
    assert safety.get_reply_rate_status(1, msgs, cfg)["status"] == "insufficient_data"


def test_reply_rate_status_critical_at_zero():
    msgs = [_outbound_at(f"C{i}", 10) for i in range(20)]
    assert safety.get_reply_rate_status(1, msgs, cfg)["status"] == "critical"


def test_reply_rate_status_healthy_at_20pct():
    msgs = []
    for i in range(20):
        msgs.append(_outbound_at(f"C{i}", 10))
        if i < 4:
            msgs.append({"numberId": 1, "contactId": f"C{i}", "direction": "inbound", "body": "ok", "createdAt": datetime.utcnow().isoformat()})
    assert safety.get_reply_rate_status(1, msgs, cfg)["status"] == "healthy"


def test_auto_pause_on_near_zero_reply_rate():
    msgs = [_outbound_at(f"C{i}", 10) for i in range(20)]
    assert safety.should_auto_pause(1, msgs, cfg)["pause"] is True


def test_no_pause_on_reply_rate_before_min_sample():
    msgs = [_outbound_at("C1", 10)]
    assert safety.should_auto_pause(1, msgs, cfg)["pause"] is False


# ---------------- templates.py ----------------
def test_render_substitutes_variables():
    out = templates.render(
        "Hi {{Owner_Name}}, unit {{Unit_Number}}, {{Agent_Name}} at {{Company_Name}}",
        {"ownerName": "Ahmed", "unitNumber": "1204", "agentName": "Sara", "companyName": "Acme Realty"},
    )
    assert out == "Hi Ahmed, unit 1204, Sara at Acme Realty"


def test_pick_initial_variant_deterministic():
    a = templates.pick_initial_variant("OWN-000123")
    b = templates.pick_initial_variant("OWN-000123")
    assert a["id"] == b["id"]


def test_pick_initial_variant_distributes():
    ids = {templates.pick_initial_variant(f"OWN-{i}")["id"] for i in range(50)}
    assert len(ids) > 1


def test_template_counts():
    assert len(templates.INITIAL_TEMPLATES) == 5
    assert len(templates.FOLLOWUP_TEMPLATES) == 3


def test_render_for_contact_deterministic():
    contact = {"contactId": "OWN-000777", "ownerName": "Ahmed", "unitNumber": "1204"}
    v = {"ownerName": "Ahmed", "unitNumber": "1204", "agentName": "Sara", "companyName": "Acme Realty"}
    a = templates.render_for_contact("initial", contact, v, cfg=cfg)
    b = templates.render_for_contact("initial", contact, v, cfg=cfg)
    assert a["text"] == b["text"]


def test_render_for_contact_no_leftover_placeholders():
    contact = {"contactId": "OWN-000778", "ownerName": "Fatima", "unitNumber": "9"}
    v = {"ownerName": "Fatima", "unitNumber": "9", "agentName": "Omar", "companyName": "Acme Realty"}
    r = templates.render_for_contact("initial", contact, v, cfg=cfg)
    assert "{{" not in r["text"]


def test_render_for_contact_varies_across_contacts():
    texts = set()
    for i in range(30):
        contact = {"contactId": f"OWN-VAR-{i}", "ownerName": "Owner", "unitNumber": "1", "templateVariant": "A"}
        v = {"ownerName": "Owner", "unitNumber": "1", "agentName": "Agent", "companyName": "Acme"}
        r = templates.render_for_contact("initial", contact, v, cfg=cfg)
        texts.add(r["text"])
    assert len(texts) > 1


def test_render_for_contact_variance_can_be_disabled():
    cfg_off = {**cfg, "content_variance": {"enabled": False}}
    v = {"ownerName": "Owner", "unitNumber": "1", "agentName": "Agent", "companyName": "Acme"}
    a = templates.render_for_contact("initial", {"contactId": "OWN-OFF-1", "templateVariant": "A"}, v, cfg=cfg_off)
    b = templates.render_for_contact("initial", {"contactId": "OWN-OFF-2", "templateVariant": "A"}, v, cfg=cfg_off)
    assert a["text"] == b["text"]


# ---------------- contacts.py ----------------
def test_standardize_phone_uae_leading_zero():
    r = contacts.standardize_phone("0501234567")
    assert r["valid"] is True
    assert r["e164"] == "+971501234567"


def test_standardize_phone_uae_no_leading_zero():
    r = contacts.standardize_phone("501234567")
    assert r["valid"] is True
    assert r["e164"] == "+971501234567"


def test_standardize_phone_already_e164():
    r = contacts.standardize_phone("+971501234567")
    assert r["valid"] is True
    assert r["e164"] == "+971501234567"


def test_standardize_phone_garbage():
    assert contacts.standardize_phone("12345")["valid"] is False


def test_standardize_phone_empty():
    assert contacts.standardize_phone("")["valid"] is False


def test_import_dedupes_same_phone():
    rows = [
        {"ownerName": "Ahmed Ali", "mobile": "0501234567", "unitNumber": "1204"},
        {"ownerName": "Ahmed Ali", "mobile": "0501234567", "unitNumber": "1204"},
    ]
    summary = contacts.import_contacts(rows, "TestCampaign")
    assert summary["added"] == 1
    assert summary["duplicateSkipped"] == 1
    assert len(db.get_contacts()) == 1


def test_import_multi_unit_merge():
    rows = [
        {"ownerName": "Fatima Noor", "mobile": "0559876543", "unitNumber": "501"},
        {"ownerName": "Fatima Noor", "mobile": "0559876543", "unitNumber": "2002"},
    ]
    summary = contacts.import_contacts(rows, "TestCampaign")
    assert summary["added"] == 1
    assert summary["mergedAsMultiUnit"] == 1
    c = db.get_contacts()[0]
    assert c["unitNumber"] == "501"
    assert c["linkedUnits"] == ["2002"]


def test_import_invalid_number_flagged():
    rows = [{"ownerName": "Bad Number", "mobile": "abc123", "unitNumber": "10"}]
    summary = contacts.import_contacts(rows, "TestCampaign")
    assert summary["invalid"] == 1
    assert db.get_contacts()[0]["invalidNumber"] is True


def test_import_suppresses_prior_optout():
    contacts.import_contacts([{"ownerName": "Opted Out Owner", "mobile": "0501112222", "unitNumber": "77"}], "C1")
    c = db.get_contacts()[0]
    db.update_contact(c["contactId"], {"optedOut": True, "optedOutAt": datetime.utcnow().isoformat()})
    summary = contacts.import_contacts([{"ownerName": "Opted Out Owner", "mobile": "0501112222", "unitNumber": "77"}], "C2")
    assert summary["suppressedOptedOut"] == 1
    assert len(db.get_contacts()) == 1


def test_excel_import_roundtrip():
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(["Owner Name", "Mobile Number", "Unit Number"])
    ws.append(["Test Owner", "0501234567", "42"])
    buf = io.BytesIO()
    wb.save(buf)
    rows = contacts.parse_excel_buffer(buf.getvalue())
    assert len(rows) == 1
    assert rows[0]["ownerName"] == "Test Owner"
    assert rows[0]["mobile"] == "501234567" or rows[0]["mobile"] == "0501234567"
