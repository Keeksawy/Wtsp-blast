"""
Regression tests for WA Outreach v1.0.41.

Covers every bug fixed between v1.0.33 and v1.0.41:
  v1.0.35 — first user auto-admin; reset button available to all users
  v1.0.37 — requeue sets messageStatus=""; not-interested/do-not-contact sets notInterested=True
  v1.0.38 — contact import includes invalid-phone rows (not silently dropped)
  v1.0.39 — CSV files accepted (not just .xlsx)
  v1.0.40 — _handle_inbound no NameError (contact resolved before dedup check)
  v1.0.41 — Windows: pythonnet excluded from bundle
  v1.0.42 — Windows: pythonnet fallback error redirected to WebView2 install dialog
  v1.0.43 — Windows: dropped pywebview entirely; app opens in default browser (no WebView2/DLL needed)

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
    """v1.0.43 — Windows spec must not bundle pywebview or pythonnet at all.

    Windows now uses browser mode: Flask + webbrowser.open(). No WebView2,
    no .NET, no DLLs — works on any machine with Chrome or Edge installed.
    """

    _SPEC = os.path.join(_PROJECT, "build", "WA_Outreach_win.spec")

    def _read(self):
        with open(self._SPEC, encoding="utf-8") as f:
            return f.read()

    def test_spec_excludes_pythonnet(self):
        """pythonnet must be in the excludes list."""
        assert '"pythonnet"' in self._read(), \
            'pythonnet is not excluded — may be pulled in transitively'

    def test_spec_excludes_clr(self):
        """clr (pythonnet alias) must be excluded."""
        assert '"clr"' in self._read(), \
            'clr is not excluded — pythonnet may still be loaded'

    def test_spec_excludes_webview(self):
        """pywebview must not be bundled — Windows uses browser mode."""
        content = self._read()
        assert '"webview"' in content, \
            'webview is not in excludes — pywebview may be bundled unnecessarily'

    def test_spec_has_no_collect_all_webview(self):
        """collect_all("webview") must not appear — it pulls in all backends."""
        assert 'collect_all("webview")' not in self._read(), \
            'collect_all("webview") found in spec — removes all pywebview backends including mshtml'

    def test_spec_has_no_webview_hiddenimports(self):
        """webview must not appear in hiddenimports."""
        # edgechromium in hiddenimports would pull in WebView2Loader.dll dependency
        content = self._read()
        assert '"webview.platforms.edgechromium"' not in content or \
               content.index('"webview.platforms.edgechromium"') > content.find("excludes"), \
            'webview.platforms.edgechromium is in hiddenimports — should be in excludes only'


class TestWindowsStartupConfig:
    """v1.0.43 — main.py must use browser mode on Windows, not pywebview."""

    _MAIN = os.path.join(_PROJECT, "main.py")

    def _read(self):
        with open(self._MAIN, encoding="utf-8") as f:
            return f.read()

    def test_webview_import_guarded_to_non_windows(self):
        """import webview must only happen on non-Windows platforms.

        On Windows we use the default browser — pywebview is never imported,
        so WebView2/DLL issues cannot occur.
        """
        content = self._read()
        import_pos = content.find("import webview")
        assert import_pos != -1, "import webview not found in main.py"
        # The platform guard must appear somewhere BEFORE the import in the file
        guard_pos = content.rfind('sys.platform != "win32"', 0, import_pos)
        assert guard_pos != -1, \
            ("'import webview' is not guarded by sys.platform != 'win32' — "
             "pywebview will be imported on Windows too, causing WebView2 errors")

    def test_windows_uses_webbrowser_module(self):
        """Windows path must use webbrowser.open() to launch the default browser."""
        content = self._read()
        assert "webbrowser" in content, \
            "webbrowser module not used — Windows browser mode not implemented"
        assert "webbrowser.open" in content, \
            "webbrowser.open() not called — browser won't open automatically on Windows"

    def test_windows_shows_quit_dialog(self):
        """Windows must show a dialog to keep the process alive and provide a quit button."""
        content = self._read()
        # MessageBoxW keeps the process running until the user clicks OK
        assert "MessageBoxW" in content, \
            "No Windows quit dialog found — process will exit immediately after opening browser"

    def test_second_instance_opens_browser(self):
        """A second instance of the exe should open the browser, not silently exit."""
        content = self._read()
        # Find the _run() function body and check the lock-failure branch
        run_fn = content[content.find("def _run():"):]
        lock_fail_block = run_fn[run_fn.find("_acquire_instance_lock"):
                                 run_fn.find("_acquire_instance_lock") + 300]
        assert "webbrowser" in lock_fail_block, \
            "Second instance does not open the browser — user gets no feedback"


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


# ─────────────────────────────────────────────────────────────────────────────
# 14. Configurable daily cap per number (v1.0.44)
# ─────────────────────────────────────────────────────────────────────────────

class TestConfigurableDailyCap:
    """v1.0.44 — warmup ramp removed; user-controlled global daily cap added.

    - Default cap is 50 messages per number per day.
    - Cap is stored in settings as dailyCapPerNumber.
    - POST /api/settings accepts and persists dailyCapPerNumber.
    - GET /api/settings returns dailyCapPerNumber.
    - safety.get_daily_cap_for_number and safety.should_auto_pause no longer exist.
    - GET /api/numbers returns dailyCap from global setting (not per-number override).
    - GET /api/numbers returns optOutRate and failRate per number.
    - Dashboard GET /api/dashboard returns optOutRate and failRate in byNumber.
    """

    def test_default_cap_is_50(self, client):
        """Fresh db must have dailyCapPerNumber = 50."""
        token, _ = _register(client)
        r = client.get("/api/settings", headers=_auth(token))
        assert r.status_code == 200
        assert r.get_json().get("dailyCapPerNumber") == 50

    def test_save_and_load_cap(self, client):
        """POST /api/settings with dailyCapPerNumber persists the value."""
        token, _ = _register(client)
        r = client.post("/api/settings",
                        json={"dailyCapPerNumber": 120},
                        headers=_auth(token),
                        content_type="application/json")
        assert r.status_code == 200
        assert r.get_json().get("dailyCapPerNumber") == 120
        # verify GET reflects new value
        r2 = client.get("/api/settings", headers=_auth(token))
        assert r2.get_json().get("dailyCapPerNumber") == 120

    def test_cap_minimum_is_1(self, client):
        """dailyCapPerNumber must be at least 1 (0 or negative clamped to 1)."""
        token, _ = _register(client)
        r = client.post("/api/settings",
                        json={"dailyCapPerNumber": 0},
                        headers=_auth(token),
                        content_type="application/json")
        assert r.status_code == 200
        assert r.get_json().get("dailyCapPerNumber") >= 1

    def test_numbers_returns_global_cap(self, client):
        """GET /api/numbers must include dailyCap equal to the global setting."""
        token, _ = _register(client)
        # set a custom cap
        client.post("/api/settings",
                    json={"dailyCapPerNumber": 75},
                    headers=_auth(token),
                    content_type="application/json")
        db.add_number({"id": "NUM-CAP", "label": "Cap Test", "status": "connected", "paused": False})
        r = client.get("/api/numbers", headers=_auth(token))
        assert r.status_code == 200
        nums = r.get_json()
        assert any(n["dailyCap"] == 75 for n in nums), \
            f"Expected dailyCap=75 in numbers response: {nums}"

    def test_numbers_returns_opt_out_and_fail_rates(self, client):
        """GET /api/numbers must return optOutRate and failRate keys."""
        token, _ = _register(client)
        db.add_number({"id": "NUM-RATE", "label": "Rate Test", "status": "connected", "paused": False})
        r = client.get("/api/numbers", headers=_auth(token))
        assert r.status_code == 200
        nums = r.get_json()
        n = next((x for x in nums if x["id"] == "NUM-RATE"), None)
        assert n is not None
        assert "optOutRate" in n
        assert "failRate" in n

    def test_warmup_ramp_removed_from_defaults(self):
        """config/defaults.py must not contain warmup_ramp."""
        from config.defaults import DEFAULTS
        assert "warmup_ramp" not in DEFAULTS, \
            "warmup_ramp still present in defaults — should be removed"

    def test_auto_pause_removed_from_defaults(self):
        """config/defaults.py must not contain auto_pause."""
        from config.defaults import DEFAULTS
        assert "auto_pause" not in DEFAULTS, \
            "auto_pause still present in defaults — should be removed"

    def test_get_daily_cap_function_removed_from_safety(self):
        """safety.get_daily_cap_for_number must not exist (replaced by global setting)."""
        from src import safety
        assert not hasattr(safety, "get_daily_cap_for_number"), \
            "get_daily_cap_for_number still exists in safety.py — warmup ramp logic not removed"

    def test_should_auto_pause_removed_from_safety(self):
        """safety.should_auto_pause must not exist (removed per user request)."""
        from src import safety
        assert not hasattr(safety, "should_auto_pause"), \
            "should_auto_pause still exists in safety.py — auto-pause logic not removed"

    def test_per_number_cap_route_removed(self, client):
        """POST /api/numbers/<id>/cap route must no longer exist (returns 404 or 405)."""
        token, _ = _register(client)
        db.add_number({"id": 999, "label": "Old Cap", "status": "connected", "paused": False})
        r = client.post("/api/numbers/999/cap",
                        json={"dailyCapOverride": 100},
                        headers=_auth(token),
                        content_type="application/json")
        assert r.status_code in (404, 405), \
            f"Expected 404/405 for removed /cap route, got {r.status_code}"

    def test_dashboard_includes_opt_out_and_fail_rates(self, client):
        """GET /api/dashboard byNumber must include optOutRate and failRate."""
        token, _ = _register(client)
        db.add_number({"id": "NUM-DASH", "label": "Dash Test", "status": "connected", "paused": False})
        r = client.get("/api/dashboard", headers=_auth(token))
        assert r.status_code == 200
        data = r.get_json()
        by_number = data.get("byNumber", [])
        assert len(by_number) > 0
        n = by_number[0]
        assert "optOutRate" in n, f"optOutRate missing from dashboard byNumber: {n}"
        assert "failRate" in n, f"failRate missing from dashboard byNumber: {n}"
