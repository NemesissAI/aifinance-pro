"""Tier 3: Session Cookie Preservation, Static File Shielding & Payload Rejection.

Authoritative Source:
- ORIGINAL_REQUEST.md § Requirements R1, R3 & Acceptance Criteria
- PROJECT.md § Architecture (Perimeter Gate Pattern, Session Cookie Invariance, Database Decoupling)
- TEST_INFRA.md § Feature Inventory (Session Cookie Invariance, Large Payload Shield)

Verifies:
- User session cookie `aifp` is completely untouched by 503 maintenance responses (no Set-Cookie emitted)
- An authenticated user remains authenticated when maintenance mode ends without re-login
- Static dashboard files (/, /index.html) and assets return JSON 503 instead of HTML
- Immediate perimeter rejection of large file uploads without buffering, parsing, or database mutation
- State/profile mutations are cleanly rejected with zero changes to persistent data
"""

from __future__ import annotations

import unittest
from server.tests.conftest import (
    MaintenanceTestCase,
    create_test_user,
    get_auth_client,
    get_test_client,
    reset_maintenance_mode,
    set_maintenance_mode,
)
import server.db as db


class TestSessionCookiePreservation(MaintenanceTestCase):
    """Verify signed session cookie 'aifp' is preserved across maintenance toggles."""

    def test_session_preserved_across_maintenance_lifecycle(self) -> None:
        """User logs in when OFF -> maintenance ON -> receives 503 -> maintenance OFF -> still authenticated."""
        email = "lifecycle_user@example.com"
        password = "UserSecret123!"

        # 1. Start with maintenance OFF and log in
        set_maintenance_mode(False)
        auth_client, user = get_auth_client(email, password, "Lifecycle Tester")
        self.assertIn("aifp", auth_client.cookies)
        initial_cookie_val = auth_client.cookies["aifp"]

        # Confirm 200 OK while OFF
        me_resp = auth_client.get("/api/me")
        self.assertEqual(me_resp.status_code, 200)
        self.assertEqual(me_resp.json().get("email"), email)

        # 2. Toggle maintenance ON
        set_maintenance_mode(True)

        # 3. Request protected endpoint during maintenance
        maint_resp = auth_client.get("/api/me")
        self.assertEqual(maint_resp.status_code, 503)
        self.assertEqual(maint_resp.json().get("error"), "maintenance")

        # 4. CRITICAL: Verify response did NOT alter or clear the session cookie
        self.assertNotIn("set-cookie", maint_resp.headers)
        self.assertEqual(auth_client.cookies.get("aifp"), initial_cookie_val)

        # 5. Toggle maintenance back OFF
        set_maintenance_mode(False)

        # 6. Verify user session is STILL valid and functional without re-login
        after_resp = auth_client.get("/api/me")
        self.assertEqual(after_resp.status_code, 200)
        self.assertEqual(after_resp.json().get("email"), email)

    def test_no_set_cookie_on_503_for_multiple_routes(self) -> None:
        """Multiple protected routes called during maintenance must never issue Set-Cookie."""
        auth_client, _ = get_auth_client("multi_cookie@example.com", "Password123!")
        set_maintenance_mode(True)

        endpoints = [
            "/api/me",
            "/api/statements",
            "/api/data",
            "/api/state",
            "/api/profile",
            "/api/onboarding/cycle-day",
        ]

        for ep in endpoints:
            resp = auth_client.get(ep)
            self.assertEqual(resp.status_code, 503, f"Endpoint {ep} should be 503")
            self.assertNotIn("set-cookie", resp.headers, f"Endpoint {ep} emitted Set-Cookie header")

    def test_unauthenticated_request_during_maintenance_receives_no_cookie(self) -> None:
        """Anonymous client requesting protected route during maintenance receives 503 and no cookie."""
        client = get_test_client()
        set_maintenance_mode(True)

        resp = client.get("/api/me")
        self.assertEqual(resp.status_code, 503)
        self.assertNotIn("set-cookie", resp.headers)
        self.assertEqual(len(client.cookies), 0)

    def test_rapid_session_requests_during_maintenance(self) -> None:
        """Stress-testing session cookie stability across 20 rapid requests during maintenance."""
        auth_client, user = get_auth_client("stress_sess@example.com", "Password123!")
        set_maintenance_mode(True)

        for _ in range(20):
            resp = auth_client.get("/api/me")
            self.assertEqual(resp.status_code, 503)
            self.assertNotIn("set-cookie", resp.headers)

        set_maintenance_mode(False)
        final_resp = auth_client.get("/api/me")
        self.assertEqual(final_resp.status_code, 200)
        self.assertEqual(final_resp.json().get("id"), user["id"])


class TestStaticFileShielding(MaintenanceTestCase):
    """Verify static dashboard and assets are strictly blocked with JSON 503 responses."""

    def test_root_path_returns_json_not_html(self) -> None:
        """GET / during maintenance returns JSON error body, never HTML."""
        set_maintenance_mode(True)
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 503)
        self.assertIn("application/json", resp.headers.get("content-type", ""))
        body = resp.json()
        self.assertEqual(body.get("error"), "maintenance")
        self.assertNotIn("<!DOCTYPE html>", resp.text)

    def test_index_html_returns_json_not_html(self) -> None:
        """GET /index.html returns JSON error body, never HTML."""
        set_maintenance_mode(True)
        resp = self.client.get("/index.html")
        self.assertEqual(resp.status_code, 503)
        self.assertIn("application/json", resp.headers.get("content-type", ""))
        body = resp.json()
        self.assertEqual(body.get("error"), "maintenance")
        self.assertNotIn("<!DOCTYPE html>", resp.text)

    def test_arbitrary_static_asset_returns_json(self) -> None:
        """GET /app.css returns JSON 503 during maintenance."""
        set_maintenance_mode(True)
        resp = self.client.get("/app.css")
        self.assertEqual(resp.status_code, 503)
        self.assertIn("application/json", resp.headers.get("content-type", ""))
        self.assertEqual(resp.json().get("error"), "maintenance")


class TestPayloadAndDatabaseShielding(MaintenanceTestCase):
    """Verify uploads and mutations are rejected before parsing, disk I/O, or DB execution."""

    def test_large_upload_immediate_rejection_auth(self) -> None:
        """Posting a large upload (>1MB) during maintenance returns 503 immediately without DB record."""
        auth_client, user = get_auth_client("upload_shield@example.com", "Password123!")

        # Count existing statements before test
        with db.session() as s:
            initial_count = len(db.user_statements(s, user["id"]))

        set_maintenance_mode(True)

        # 2 MB dummy payload
        large_payload = b"%PDF-1.4\n" + (b"0" * (2 * 1024 * 1024))
        resp = auth_client.post(
            "/api/upload",
            files={"file": ("large_statement.pdf", large_payload, "application/pdf")},
        )
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json().get("error"), "maintenance")

        # Confirm no statement was added to database
        with db.session() as s:
            new_count = len(db.user_statements(s, user["id"]))
            self.assertEqual(initial_count, new_count)

    def test_invalid_upload_returns_503_not_422(self) -> None:
        """Uploading malformed non-PDF bytes returns 503, proving parser was never invoked."""
        auth_client, _ = get_auth_client("invalid_pdf@example.com", "Password123!")
        set_maintenance_mode(True)

        resp = auth_client.post(
            "/api/upload",
            files={"file": ("corrupt.pdf", b"NOT_A_PDF_CORRUPT_BYTES", "application/pdf")},
        )
        # If parser had run, it would return 422 ParseError. Maintenance middleware must return 503.
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json().get("error"), "maintenance")

    def test_state_mutation_blocked_without_db_write(self) -> None:
        """POST /api/state is rejected with 503 and existing user state is preserved."""
        auth_client, user = get_auth_client("state_shield@example.com", "Password123!")

        # Set baseline state while OFF
        set_maintenance_mode(False)
        auth_client.post("/api/state", json={"settingA": "originalValue"})

        # Toggle ON
        set_maintenance_mode(True)
        resp = auth_client.post("/api/state", json={"settingA": "maliciousOrAccidentalOverwrite"})
        self.assertEqual(resp.status_code, 503)

        # Toggle OFF and verify state was not mutated
        set_maintenance_mode(False)
        check_resp = auth_client.get("/api/state")
        self.assertEqual(check_resp.status_code, 200)
        self.assertEqual(check_resp.json().get("settingA"), "originalValue")

    def test_profile_mutation_blocked_without_db_write(self) -> None:
        """POST /api/profile is rejected with 503 and user profile is preserved."""
        auth_client, user = get_auth_client("prof_shield@example.com", "Password123!")

        set_maintenance_mode(False)
        auth_client.post("/api/profile", json={"account_holder_keys": ["KEY_ORIG"]})

        set_maintenance_mode(True)
        resp = auth_client.post("/api/profile", json={"account_holder_keys": ["KEY_NEW_MUTATED"]})
        self.assertEqual(resp.status_code, 503)

        set_maintenance_mode(False)
        check_resp = auth_client.get("/api/profile")
        self.assertEqual(check_resp.status_code, 200)
        self.assertEqual(check_resp.json().get("account_holder_keys"), ["KEY_ORIG"])


if __name__ == "__main__":
    unittest.main()
