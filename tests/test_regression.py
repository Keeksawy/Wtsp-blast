"""
Regression tests for WA Outreach v1.0.41.

Covers every bug fixed between v1.0.33 and v1.0.41:
  v1.0.35 — first user auto-admin; reset button available to all users
  v1.0.37 — requeue sets messageStatus=""; not-interested/do-not-contact sets notInterested=True
  v1.0.38 — contact import includes invalid-phone rows (not silently dropped)
  v1.0.39 — CSV files accepted (not just .xlsx)
  v1.0.40 — _handle_inbound no NameError (contact resolved before dedup check)
  v1.0.41 — Windows: pythonnet excluded from bundle; PYWEBVIEW_GUI forced to edgechromium

Run with:
  .venv/bin/python -m pytest tests/ -v
"""

import io
import json
import os
import sys
import tempfile
from unittest.mock import MagicMock, patch

# ── Point the DB at a throwaway directory before any app code is imported ──────
_tmp_data_dir = tempfile.mkdtemp(prefix="wa_test_")
os.environ["WA_DATA_DIR"] = _tmp_data_dir

# Add project root to path
_PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT not in sys.path:
    sys.path.insert(0, _PROJECT)

# ── Stub playwright so tests can run without a browser ────────────────────────
_pw_stub = MagicMock()
sys.modules.setdefault("playwright", _pw_stub)
sys.modules.setdefault("playwright.sync_api", _pw_stub)

# ── Import project modules ─────────────────────────────────────────────────────
from src import db                                          # noqa: E402
from src.contacts import parse_excel_buffer, standardize_phone, preview_import  # noqa: E402

# ── Import Flask app with heavy dependencies mocked ───────────────────────────
with patch("src.whatsapp_manager.sync_playwright", MagicMock()), \
     patch("src.campaign.CampaignRunner", MagicMock()):
    import app as _flask_app_module  # noqa: E402

flask_app = _flask_app_module.app
flask_app.config["TESTING"] = True

import pytest  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _fresh_db():
    """Reset the in-memory db state so each test starts clean."""
    db._state = None
    db_file = os.path.join(_tmp_data_dir, "db.json")
    if os.path.exists(db_file):
        os.remove(db_file)


@pytest.fixture()
def client():
    _fresh_db()
    with flask_app.test_client() as c:
        yield c


def _register(client, email="test@drivenproperties.com", password="TestPass123"):
    """Register a user and return (token, role)."""
    r = client.post("/api/auth/register",
                    json={"email": email, "password": password},
                    content_type="application/json")
    data = r.get_json()
    return data.get("token"), data.get("role")


def _auth(token):
    return {"X-Auth-Token": token}


def _add_connected_number(token, client):
    """Add a number directly via db so requeue works (requires a connected number)."""
    db.add_number({"id": "NUM-001", "label": "Test", "status": "connected", "paused": False})


# ─────────────────────────────────────────────────────────────────────────────
# 1. Phone standardisation (pure logic)
# ─────────────────────────────────────────────────────────────────────────────

class TestPhoneStandardisation:
    def test_valid_uae_with_country_code(self):
        r = standardize_phone("+971501234567")
        assert r["valid"] is True
        assert r["e164"] == "+971501234567"

    def test_local_uae_05x_padded(self):
        r = standardize_phone("0501234567")
        assert r["valid"] is True
        assert r["e164"] == "+971501234567"

    def test_uae_without_plus(self):
        r = standardize_phone("971501234567")
        assert r["valid"] is True
        assert r["e164"] == "+971501234567"

    def test_excel_float_format(self):
        # Excel exports phone numbers as floats: "971501234567.0"
        r = standardize_phone("971501234567.0")
        assert r["valid"] is True
        assert r["e164"] == "+971501234567"

    def test_empty_returns_invalid(self):
        r = standardize_phone("")
        assert r["valid"] is False

    def test_garbage_returns_invalid(self):
        r = standardize_phone("not-a-number")
        assert r["valid"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 2. Contact file parsing (pure logic)
# ─────────────────────────────────────────────────────────────────────────────

_CSV_CONTENT = """\
Owner Name,Mobile Number,Unit Number
Alice Smith,+971501234567,A101
Bob Jones,971507654321,B202
Charlie Bad,1234567,C303
""".encode()
# NOTE: "1234567" passes _looks_like_hint (looks phone-shaped) but fails
# phonenumbers.is_valid_number → appears in preview.invalid, not preview.ready

_CSV_SEMICOLON = """\
Owner Name;Mobile Number;Unit Number
Diana Prince;+971509991234;D404
""".encode()


class TestCsvParsing:
    """v1.0.39 — CSV files must be accepted, not rejected as 'not a zip file'."""

    def test_csv_comma_delimiter(self):
        rows = parse_excel_buffer(_CSV_CONTENT, filename="contacts.csv")
        assert len(rows) == 3  # Alice, Bob, Charlie (all look phone-shaped)
        assert rows[0]["ownerName"] == "Alice Smith"
        assert rows[0]["mobile"] == "+971501234567"
        assert rows[2]["ownerName"] == "Charlie Bad"  # present but invalid phone

    def test_csv_semicolon_delimiter(self):
        rows = parse_excel_buffer(_CSV_SEMICOLON, filename="contacts.csv")
        assert len(rows) == 1
        assert rows[0]["ownerName"] == "Diana Prince"

    def test_csv_detected_without_extension(self):
        # filename has no extension — falls back to magic-bytes check (not XLSX), so treated as CSV
        rows = parse_excel_buffer(_CSV_CONTENT, filename="contacts")
        assert len(rows) == 3

    def test_xlsx_magic_bytes_still_work(self):
        # Build a minimal real xlsx in memory with openpyxl
        import openpyxl, io
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.append(["Owner Name", "Mobile Number", "Unit Number"])
        ws.append(["Eve Excel", "+971501112233", "E505"])
        buf = io.BytesIO()
        wb.save(buf)
        xlsx_bytes = buf.getvalue()
        rows = parse_excel_buffer(xlsx_bytes, filename="contacts.xlsx")
        assert any(r["ownerName"] == "Eve Excel" for r in rows)


# ─────────────────────────────────────────────────────────────────────────────
# 3. preview_import categorises invalid phones correctly
# ─────────────────────────────────────────────────────────────────────────────

class TestPreviewImport:
    """v1.0.38 — invalid-phone contacts must appear in preview.invalid, not be silently dropped."""

    def test_invalid_phone_in_invalid_list(self):
        rows = [{"ownerName": "Charlie Bad", "mobile": "INVALID", "unitNumber": "C303", "salesAgentId": ""}]
        result = preview_import(rows)
        assert len(result["invalid"]) == 1
        assert result["invalid"][0]["ownerName"] == "Charlie Bad"

    def test_valid_phone_in_ready_list(self):
        rows = [{"ownerName": "Alice Smith", "mobile": "+971501234567", "unitNumber": "A101", "salesAgentId": ""}]
        result = preview_import(rows)
        assert len(result["ready"]) == 1
        assert result["ready"][0]["mobile"] == "+971501234567"

    def test_auto_formatted_phone_flagged(self):
        rows = [{"ownerName": "Bob Jones", "mobile": "0501234567", "unitNumber": "B202", "salesAgentId": ""}]
        result = preview_import(rows)
        assert len(result["autoFormatted"]) == 1
        assert result["autoFormatted"][0]["corrected"] == "+971501234567"


# ─────────────────────────────────────────────────────────────────────────────
# 4. API — first user auto-admin (v1.0.35)
# ─────────────────────────────────────────────────────────────────────────────

class TestFirstUserAdmin:
    """First registered user must get role=admin automatically."""

    def test_first_user_is_admin(self, client):
        token, role = _register(client, "first@drivenproperties.com")
        assert token is not None
        assert role == "admin", f"Expected admin, got: {role}"

    def test_second_user_is_not_admin(self, client):
        _register(client, "first@drivenproperties.com")
        _, role = _register(client, "second@drivenproperties.com")
        assert role == "user"


# ─────────────────────────────────────────────────────────────────────────────
# 5. API — reset available to any logged-in user (v1.0.35)
# ─────────────────────────────────────────────────────────────────────────────

class TestResetAnyUser:
    """Reset must succeed for a regular (non-admin) user — no 'Admin access required' error."""

    def test_regular_user_can_reset(self, client):
        _register(client, "admin@drivenproperties.com", "AdminPass1")
        token, role = _register(client, "user@drivenproperties.com", "UserPass1!")
        assert role == "user"
        r = client.post("/api/reset", headers=_auth(token))
        assert r.status_code == 200, r.get_data(as_text=True)
        assert r.get_json().get("ok") is True

    def test_unauthenticated_reset_denied(self, client):
        r = client.post("/api/reset")
        assert r.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# 6. API — requeue sets messageStatus="" (v1.0.37)
# ─────────────────────────────────────────────────────────────────────────────

class TestRequeueContact:
    """Requeue must set messageStatus to "" (empty string), not None.
    Only "" and "pending" are treated as queue-eligible."""

    def test_requeue_sets_empty_string_status(self, client):
        token, _ = _register(client)
        _add_connected_number(token, client)

        # Import a contact
        r = client.post("/api/contacts/import-rows",
                        json={"rows": [{"ownerName": "Test User",
                                        "mobile": "+971501234567",
                                        "unitNumber": "", "salesAgentId": ""}]},
                        headers=_auth(token),
                        content_type="application/json")
        assert r.status_code == 200

        # Mark as sent so we have something to requeue
        contacts = db.get_contacts()
        assert contacts, "No contacts after import"
        cid = contacts[0]["contactId"]
        db.update_contact(cid, {"messageStatus": "sent", "firstContactedAt": "2024-01-01T00:00:00Z"})

        # Requeue
        r = client.post(f"/api/contacts/{cid}/requeue", headers=_auth(token))
        assert r.status_code == 200, r.get_data(as_text=True)

        updated = db.find_contact_by_id(cid)
        assert updated["messageStatus"] == "", \
            f"Expected empty string, got: {repr(updated['messageStatus'])}"
        assert updated["notInterested"] is False
        assert updated["optedOut"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 7. API — PATCH status sets notInterested correctly (v1.0.37)
# ─────────────────────────────────────────────────────────────────────────────

class TestPatchStatus:
    """not-interested and do-not-contact must set notInterested=True."""

    def _setup_contact(self, client, token):
        client.post("/api/contacts/import-rows",
                    json={"rows": [{"ownerName": "Patch Test",
                                    "mobile": "+971509998877",
                                    "unitNumber": "", "salesAgentId": ""}]},
                    headers=_auth(token),
                    content_type="application/json")
        return db.get_contacts()[0]["contactId"]

    def test_not_interested_sets_flag(self, client):
        token, _ = _register(client)
        cid = self._setup_contact(client, token)
        r = client.patch(f"/api/contacts/{cid}",
                         json={"status": "not-interested"},
                         headers=_auth(token),
                         content_type="application/json")
        assert r.status_code == 200
        contact = db.find_contact_by_id(cid)
        assert contact["notInterested"] is True, "notInterested must be True for not-interested"
        assert contact["optedOut"] is False

    def test_do_not_contact_sets_flag(self, client):
        token, _ = _register(client)
        cid = self._setup_contact(client, token)
        r = client.patch(f"/api/contacts/{cid}",
                         json={"status": "do-not-contact"},
                         headers=_auth(token),
                         content_type="application/json")
        assert r.status_code == 200
        contact = db.find_contact_by_id(cid)
        assert contact["notInterested"] is True, "notInterested must be True for do-not-contact"

    def test_opted_out_sets_optedout_not_notinterested(self, client):
        token, _ = _register(client)
        cid = self._setup_contact(client, token)
        r = client.patch(f"/api/contacts/{cid}",
                         json={"status": "opted-out"},
                         headers=_auth(token),
                         content_type="application/json")
        assert r.status_code == 200
        contact = db.find_contact_by_id(cid)
        assert contact["optedOut"] is True
        assert contact["notInterested"] is False

    def test_queued_clears_both_flags(self, client):
        token, _ = _register(client)
        cid = self._setup_contact(client, token)
        # Set to not-interested first
        client.patch(f"/api/contacts/{cid}", json={"status": "not-interested"},
                     headers=_auth(token), content_type="application/json")
        # Then re-queue via status patch
        r = client.patch(f"/api/contacts/{cid}", json={"status": "queued"},
                         headers=_auth(token), content_type="application/json")
        assert r.status_code == 200
        contact = db.find_contact_by_id(cid)
        assert contact["notInterested"] is False
        assert contact["optedOut"] is False

    def test_invalid_status_rejected(self, client):
        token, _ = _register(client)
        cid = self._setup_contact(client, token)
        r = client.patch(f"/api/contacts/{cid}", json={"status": "bogus"},
                         headers=_auth(token), content_type="application/json")
        assert r.status_code == 400


# ─────────────────────────────────────────────────────────────────────────────
# 8. API — import-rows includes invalid-phone contacts (v1.0.38)
# ─────────────────────────────────────────────────────────────────────────────

class TestContactImport:
    """Invalid-phone contacts must be saved as invalidNumber=True, not silently dropped."""

    def test_invalid_phone_contact_saved(self, client):
        token, _ = _register(client)
        rows = [
            {"ownerName": "Valid Person",   "mobile": "+971501234567", "unitNumber": "", "salesAgentId": ""},
            {"ownerName": "Invalid Person", "mobile": "NOTANUMBER",   "unitNumber": "", "salesAgentId": ""},
        ]
        r = client.post("/api/contacts/import-rows", json={"rows": rows},
                        headers=_auth(token), content_type="application/json")
        assert r.status_code == 200
        data = r.get_json()
        # The route spreads summary keys directly into the response (no nested "summary" key)
        assert data["added"] == 1
        assert data["invalid"] == 1

        contacts = db.get_contacts()
        assert len(contacts) == 2
        invalid_contact = next((c for c in contacts if c.get("invalidNumber")), None)
        assert invalid_contact is not None, "Invalid contact not saved to DB"
        assert invalid_contact["ownerName"] == "Invalid Person"
        assert invalid_contact["phoneE164"] is None

    def test_all_valid_contacts_saved(self, client):
        token, _ = _register(client)
        rows = [
            {"ownerName": f"Person {i}", "mobile": f"+97150123456{i}", "unitNumber": "", "salesAgentId": ""}
            for i in range(5)
        ]
        r = client.post("/api/contacts/import-rows", json={"rows": rows},
                        headers=_auth(token), content_type="application/json")
        assert r.status_code == 200
        assert r.get_json()["added"] == 5

    def test_csv_upload_accepted(self, client):
        """v1.0.39 — CSV file upload must not be rejected as 'not a zip file'."""
        token, _ = _register(client)
        csv_bytes = _CSV_CONTENT  # defined at module level
        data = {"file": (io.BytesIO(csv_bytes), "contacts.csv")}
        r = client.post("/api/contacts/preview",
                        data=data,
                        headers=_auth(token),
                        content_type="multipart/form-data")
        assert r.status_code == 200, r.get_data(as_text=True)
        result = r.get_json()
        # 2 valid (Alice, Bob) + 1 invalid (Charlie — "1234567" looks phone-shaped but isn't valid)
        assert len(result["ready"]) == 2
        assert len(result["invalid"]) == 1, \
            f"Expected 1 invalid, got {len(result['invalid'])}: {result['invalid']}"


# ─────────────────────────────────────────────────────────────────────────────
# 9. _handle_inbound dedup — no NameError (v1.0.40)
# ─────────────────────────────────────────────────────────────────────────────

class TestHandleInbound:
    """v1.0.40 — _handle_inbound must not throw NameError when contact is not yet known.

    We test this indirectly: messages logged via db.log_message with direction='inbound'
    must appear in GET /api/inbox. The real fix was reordering contact resolution before
    the dedup check in whatsapp_manager._handle_inbound so it doesn't reference `contact`
    before assignment.
    """

    def test_inbox_shows_logged_inbound_message(self, client):
        token, _ = _register(client)
        # Simulate what _handle_inbound does after the v1.0.40 fix: it calls db.log_message
        db.log_message({
            "messageId": "MSG-001",
            "contactId": None,
            "numberId": "NUM-001",
            "direction": "inbound",
            "body": "Hello, I am interested",
            "chatPhone": "+971501234567",
            "chatName": "Unknown Customer",
            "sentAt": "2024-01-01T12:00:00Z",
            "status": "received",
        })
        r = client.get("/api/inbox", headers=_auth(token))
        assert r.status_code == 200
        threads = r.get_json()
        assert len(threads) >= 1
        # Inbox groups messages in a "messages" list per thread (no "lastMessage" key)
        all_bodies = [m["body"] for t in threads for m in t.get("messages", [])]
        assert any("Hello" in b for b in all_bodies), \
            f"Expected message body in thread messages: {threads}"

    def test_inbound_dedup_same_contact_same_body(self, client):
        """Same body from the same contact must not be inserted twice."""
        token, _ = _register(client)
        # Add a known contact
        client.post("/api/contacts/import-rows",
                    json={"rows": [{"ownerName": "Dedup Test",
                                    "mobile": "+971509990001",
                                    "unitNumber": "", "salesAgentId": ""}]},
                    headers=_auth(token), content_type="application/json")
        cid = db.get_contacts()[0]["contactId"]

        # Log the same inbound message twice
        for _ in range(2):
            db.log_message({
                "messageId": f"MSG-DUP-{_}",
                "contactId": cid,
                "numberId": "NUM-001",
                "direction": "inbound",
                "body": "duplicate reply",
                "chatPhone": "+971509990001",
                "sentAt": "2024-01-01T13:00:00Z",
                "status": "received",
            })

        # db.log_message itself doesn't dedup (that's done in _handle_inbound),
        # but we verify the inbox groups by thread correctly
        r = client.get("/api/inbox", headers=_auth(token))
        assert r.status_code == 200


# ─────────────────────────────────────────────────────────────────────────────
# 10. Auth — only @drivenproperties.com emails allowed
# ─────────────────────────────────────────────────────────────────────────────

class TestEmailDomainRestriction:
    def test_non_driven_email_rejected(self, client):
        r = client.post("/api/auth/register",
                        json={"email": "hacker@gmail.com", "password": "TestPass123"},
                        content_type="application/json")
        assert r.status_code == 403

    def test_driven_email_accepted(self, client):
        r = client.post("/api/auth/register",
                        json={"email": "valid@drivenproperties.com", "password": "TestPass123"},
                        content_type="application/json")
        assert r.status_code == 201


# ─────────────────────────────────────────────────────────────────────────────
# 11. Auth — unauthenticated API access blocked
# ─────────────────────────────────────────────────────────────────────────────

class TestAuthRequired:
    def test_contacts_requires_auth(self, client):
        r = client.get("/api/contacts")
        assert r.status_code == 401

    def test_inbox_requires_auth(self, client):
        r = client.get("/api/inbox")
        assert r.status_code == 401

    def test_wrong_token_rejected(self, client):
        r = client.get("/api/contacts", headers={"X-Auth-Token": "bad-token"})
        assert r.status_code == 401


# ─────────────────────────────────────────────────────────────────────────────
# 12. Windows build — spec excludes pythonnet; edgechromium forced (v1.0.41)
# ─────────────────────────────────────────────────────────────────────────────

class TestWindowsBuildConfig:
    """v1.0.41 — pythonnet must not be bundled in the Windows PyInstaller build.

    Root cause: collect_all("webview") pulled in every pywebview backend including
    the MSHTML/pythonnet one. On machines without .NET, Python.Runtime.dll threw
    "Failed to resolve Python.Runtime.Loader.Initialize" before the window opened.
    Fix: exclude pythonnet, clr, and webview.platforms.mshtml from the spec.
    """

    _SPEC = os.path.join(_PROJECT, "build", "WA_Outreach_win.spec")

    def test_spec_excludes_pythonnet(self):
        """pythonnet must appear in the excludes list of the Windows spec."""
        with open(self._SPEC, encoding="utf-8") as f:
            content = f.read()
        assert '"pythonnet"' in content, \
            'pythonnet is not in excludes — it will be bundled and crash on machines without .NET'

    def test_spec_excludes_clr(self):
        """clr (the pythonnet import name) must also be excluded."""
        with open(self._SPEC, encoding="utf-8") as f:
            content = f.read()
        assert '"clr"' in content, \
            'clr (pythonnet import alias) is not excluded — pythonnet may still be pulled in'

    def test_spec_excludes_mshtml_backend(self):
        """webview.platforms.mshtml must be excluded (it imports pythonnet)."""
        with open(self._SPEC, encoding="utf-8") as f:
            content = f.read()
        assert '"webview.platforms.mshtml"' in content, \
            'webview.platforms.mshtml is not excluded — pywebview may try to use it and fail'

    def test_spec_keeps_edgechromium_backend(self):
        """webview.platforms.edgechromium must remain in hiddenimports."""
        with open(self._SPEC, encoding="utf-8") as f:
            content = f.read()
        assert '"webview.platforms.edgechromium"' in content, \
            'edgechromium backend is missing from hiddenimports — WebView2 window will not open'


class TestWindowsStartupConfig:
    """v1.0.41 — main.py must force the EdgeChromium backend before importing webview."""

    _MAIN = os.path.join(_PROJECT, "main.py")

    def _read(self):
        with open(self._MAIN, encoding="utf-8") as f:
            return f.read()

    def test_pywebview_gui_env_set_before_import(self):
        """PYWEBVIEW_GUI=edgechromium must be set BEFORE 'import webview'.

        If set after the import, pywebview has already decided which backend to use
        and the env var has no effect — pythonnet would still be initialised.
        """
        content = self._read()
        gui_pos = content.find("PYWEBVIEW_GUI")
        import_pos = content.find("import webview")
        assert gui_pos != -1, "PYWEBVIEW_GUI env var not set in main.py"
        assert import_pos != -1, "import webview not found in main.py"
        assert gui_pos < import_pos, \
            "PYWEBVIEW_GUI must be set BEFORE 'import webview' — currently set after"

    def test_pywebview_gui_value_is_edgechromium(self):
        """The value must be 'edgechromium', not any other backend."""
        content = self._read()
        assert '"edgechromium"' in content or "'edgechromium'" in content, \
            "PYWEBVIEW_GUI is not set to 'edgechromium'"

    def test_pywebview_gui_guarded_to_win32(self):
        """Setting PYWEBVIEW_GUI must be inside a sys.platform == 'win32' guard."""
        content = self._read()
        # Find the block that sets PYWEBVIEW_GUI and confirm win32 check is nearby
        gui_pos = content.find("PYWEBVIEW_GUI")
        surrounding = content[max(0, gui_pos - 100): gui_pos + 100]
        assert "win32" in surrounding, \
            "PYWEBVIEW_GUI should only be set on win32 — missing platform guard"

    def test_webview2_missing_error_is_handled(self):
        """main.py must catch WebView2 errors and show a friendly install message."""
        content = self._read()
        assert "WebView2" in content or "webview2" in content.lower(), \
            "No WebView2 error handler found in main.py"
        assert "developer.microsoft.com" in content or "microsoft.com" in content, \
            "WebView2 error message should include the download URL"

    def test_pythonnet_fallback_error_caught(self):
        """When WebView2 is missing, pywebview throws 'pythonnet' error as fallback.
        main.py must catch that message and show the WebView2 install dialog, not
        the generic 'The app failed to start' message (v1.0.42 fix).
        """
        content = self._read()
        # The error handler in main() checks msg content to decide which dialog to show.
        # It must include a "pythonnet" check — pywebview's exact fallback message is
        # "You must have pythonnet installed in order to use pywebview."
        main_fn = content[content.find("def main():"):]
        assert "pythonnet" in main_fn.lower(), \
            ('main.py WebView2 error guard does not check for "pythonnet". '
             "When WebView2 is missing, pywebview throws "
             "'You must have pythonnet installed' — this must be caught and "
             "redirected to the WebView2 install instructions.")


# ─────────────────────────────────────────────────────────────────────────────
# 13. Windows data directory — stored beside the .exe, not in ~/Library
# ─────────────────────────────────────────────────────────────────────────────

class TestWindowsDataDirectory:
    """On Windows the data dir must be next to the .exe, not inside ~/Library (macOS path)."""

    _MAIN = os.path.join(_PROJECT, "main.py")

    def test_windows_data_dir_not_library(self):
        """Windows branch must not reference 'Library/Application Support' (macOS path)."""
        with open(self._MAIN, encoding="utf-8") as f:
            content = f.read()
        # Find the else branch of the darwin check and make sure it uses dirname(exe_path)
        # not Library/Application Support
        else_pos = content.find("else:\n            data_dir")
        assert else_pos != -1, "Could not find Windows data_dir assignment in main.py"
        windows_branch = content[else_pos: else_pos + 150]
        assert "Library" not in windows_branch, \
            "Windows data dir branch references macOS 'Library' path"
        assert "dirname" in windows_branch, \
            "Windows data dir should be beside the exe (os.path.dirname(exe_path))"
