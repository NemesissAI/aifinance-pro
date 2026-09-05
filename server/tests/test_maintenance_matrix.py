"""Tier 1: Full ON/OFF Status Matrix across all endpoints and routes.

Authoritative Source:
- ORIGINAL_REQUEST.md § Requirements R1, R2, R3 & Acceptance Criteria
- PROJECT.md § Feature Inventory & Interface Contracts

Verifies that when maintenance mode is:
- OFF: all existing endpoints operate identically to baseline behavior.
- ON: only allowlisted endpoints (/api/health, /api/maintenance) return 200;
      every other endpoint (user accounts, statement processing, settings, static
      dashboard, and unknown paths) returns HTTP 503 with JSON payload and Retry-After header.
"""

from __future__ import annotations

import unittest
from server.tests.conftest import (
    MaintenanceTestCase,
    create_test_user,
    get_auth_client,
    reset_maintenance_mode,
    set_maintenance_mode,
)


class TestAllowlistEndpoints(MaintenanceTestCase):
    """Test /api/health and /api/maintenance behavior across ON and OFF states."""

    def test_health_endpoint_maintenance_off(self) -> None:
        """GET /api/health returns 200 OK with {"status": "ok"} when maintenance is OFF."""
        set_maintenance_mode(False)
        resp = self.client.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("status"), "ok")

    def test_health_endpoint_maintenance_on(self) -> None:
        """GET /api/health must ALWAYS return 200 OK with {"status": "ok"} when maintenance is ON."""
        set_maintenance_mode(True)
        resp = self.client.get("/api/health")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data.get("status"), "ok")

    def test_maintenance_status_endpoint_off(self) -> None:
        """GET /api/maintenance reports {"active": false} when maintenance is OFF."""
        set_maintenance_mode(False)
        resp = self.client.get("/api/maintenance")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertFalse(data.get("active"))

    def test_maintenance_status_endpoint_on(self) -> None:
        """GET /api/maintenance reports {"active": true, ...} with message and retry_after when ON."""
        set_maintenance_mode(True, message="System upgrading", retry_after=120)
        resp = self.client.get("/api/maintenance")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data.get("active"))
        self.assertIn("message", data)
        self.assertIn("retry_after", data)
        self.assertEqual(data.get("retry_after"), 120)


class TestAccountEndpointsMatrix(MaintenanceTestCase):
    """Test /api/register, /api/login, /api/logout, /api/me in ON and OFF states."""

    def test_register_maintenance_off(self) -> None:
        """POST /api/register executes account validation when maintenance is OFF."""
        set_maintenance_mode(False)
        # Missing fields should return 422 or 400, not 503
        resp = self.client.post("/api/register", data={})
        self.assertIn(resp.status_code, [400, 422])

    def test_register_maintenance_on(self) -> None:
        """POST /api/register returns 503 when maintenance is ON."""
        set_maintenance_mode(True)
        resp = self.client.post("/api/register", data={
            "email": "newuser@example.com",
            "password": "Password123!",
            "name": "New User"
        })
        self.assertEqual(resp.status_code, 503)
        self.assertIn("Retry-After", resp.headers)
        self.assertEqual(resp.json().get("error"), "maintenance")

    def test_login_maintenance_off_invalid_creds(self) -> None:
        """POST /api/login checks credentials when maintenance is OFF."""
        set_maintenance_mode(False)
        resp = self.client.post("/api/login", data={"email": "nobody@example.com", "password": "wrong"})
        self.assertEqual(resp.status_code, 401)

    def test_login_maintenance_off_valid_creds(self) -> None:
        """POST /api/login succeeds with valid credentials when maintenance is OFF."""
        set_maintenance_mode(False)
        user = create_test_user("login_test@example.com", "ValidPass123!")
        resp = self.client.post("/api/login", data={"email": user["email"], "password": user["password"]})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("ok"))
        self.assertIn("aifp", resp.cookies)

    def test_login_maintenance_on(self) -> None:
        """POST /api/login returns 503 with Retry-After when maintenance is ON."""
        set_maintenance_mode(True)
        user = create_test_user("login_on@example.com", "ValidPass123!")
        resp = self.client.post("/api/login", data={"email": user["email"], "password": user["password"]})
        self.assertEqual(resp.status_code, 503)
        self.assertIn("Retry-After", resp.headers)
        self.assertEqual(resp.json().get("error"), "maintenance")

    def test_logout_maintenance_off(self) -> None:
        """POST /api/logout clears session when maintenance is OFF."""
        set_maintenance_mode(False)
        resp = self.client.post("/api/logout")
        self.assertEqual(resp.status_code, 200)

    def test_logout_maintenance_on(self) -> None:
        """POST /api/logout returns 503 when maintenance is ON."""
        set_maintenance_mode(True)
        resp = self.client.post("/api/logout")
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json().get("error"), "maintenance")

    def test_me_maintenance_off_unauthenticated(self) -> None:
        """GET /api/me returns 401 for unauthenticated requests when maintenance is OFF."""
        set_maintenance_mode(False)
        resp = self.client.get("/api/me")
        self.assertEqual(resp.status_code, 401)

    def test_me_maintenance_off_authenticated(self) -> None:
        """GET /api/me returns 200 with user profile when authenticated and maintenance is OFF."""
        set_maintenance_mode(False)
        auth_client, user = get_auth_client("me_off@example.com", "Pass12345!")
        resp = auth_client.get("/api/me")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("email"), user["email"])

    def test_me_maintenance_on_unauthenticated(self) -> None:
        """GET /api/me returns 503 for unauthenticated requests when maintenance is ON."""
        set_maintenance_mode(True)
        resp = self.client.get("/api/me")
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json().get("error"), "maintenance")

    def test_me_maintenance_on_authenticated(self) -> None:
        """GET /api/me returns 503 even for authenticated sessions when maintenance is ON."""
        set_maintenance_mode(False)
        auth_client, _ = get_auth_client("me_on@example.com", "Pass12345!")
        set_maintenance_mode(True)
        resp = auth_client.get("/api/me")
        self.assertEqual(resp.status_code, 503)
        self.assertIn("Retry-After", resp.headers)
        self.assertEqual(resp.json().get("error"), "maintenance")


class TestStatementAndDataEndpointsMatrix(MaintenanceTestCase):
    """Test statement upload, list, delete, and data endpoints in ON and OFF states."""

    def test_upload_maintenance_off_unauth(self) -> None:
        """POST /api/upload returns 401 when unauthenticated and maintenance is OFF."""
        set_maintenance_mode(False)
        resp = self.client.post("/api/upload")
        self.assertEqual(resp.status_code, 401)

    def test_upload_maintenance_off_auth_empty(self) -> None:
        """POST /api/upload returns 422 when authenticated but missing file and maintenance is OFF."""
        set_maintenance_mode(False)
        auth_client, _ = get_auth_client("upload_off@example.com", "Pass12345!")
        resp = auth_client.post("/api/upload")
        self.assertEqual(resp.status_code, 422)

    def test_upload_maintenance_on_unauth(self) -> None:
        """POST /api/upload returns 503 when maintenance is ON."""
        set_maintenance_mode(True)
        resp = self.client.post("/api/upload")
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json().get("error"), "maintenance")

    def test_upload_maintenance_on_auth(self) -> None:
        """POST /api/upload returns 503 for authenticated requests when maintenance is ON."""
        set_maintenance_mode(False)
        auth_client, _ = get_auth_client("upload_on@example.com", "Pass12345!")
        set_maintenance_mode(True)
        resp = auth_client.post(
            "/api/upload",
            files={"file": ("test.pdf", b"%PDF-fake-content", "application/pdf")},
        )
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json().get("error"), "maintenance")

    def test_statements_maintenance_off_unauth(self) -> None:
        """GET /api/statements returns 401 when unauthenticated and maintenance is OFF."""
        set_maintenance_mode(False)
        resp = self.client.get("/api/statements")
        self.assertEqual(resp.status_code, 401)

    def test_statements_maintenance_off_auth(self) -> None:
        """GET /api/statements returns 200 when authenticated and maintenance is OFF."""
        set_maintenance_mode(False)
        auth_client, _ = get_auth_client("stmt_off@example.com", "Pass12345!")
        resp = auth_client.get("/api/statements")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("statements", resp.json())

    def test_statements_maintenance_on(self) -> None:
        """GET /api/statements returns 503 when maintenance is ON."""
        set_maintenance_mode(False)
        auth_client, _ = get_auth_client("stmt_on@example.com", "Pass12345!")
        set_maintenance_mode(True)
        resp = auth_client.get("/api/statements")
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json().get("error"), "maintenance")

    def test_delete_statement_maintenance_off(self) -> None:
        """DELETE /api/statements/{id} returns 404 for missing statement when OFF."""
        set_maintenance_mode(False)
        auth_client, _ = get_auth_client("del_off@example.com", "Pass12345!")
        resp = auth_client.delete("/api/statements/nonexistent-id")
        self.assertEqual(resp.status_code, 404)

    def test_delete_statement_maintenance_on(self) -> None:
        """DELETE /api/statements/{id} returns 503 when maintenance is ON."""
        set_maintenance_mode(False)
        auth_client, _ = get_auth_client("del_on@example.com", "Pass12345!")
        set_maintenance_mode(True)
        resp = auth_client.delete("/api/statements/any-id")
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json().get("error"), "maintenance")

    def test_data_maintenance_off(self) -> None:
        """GET /api/data returns 200 when authenticated and maintenance is OFF."""
        set_maintenance_mode(False)
        auth_client, _ = get_auth_client("data_off@example.com", "Pass12345!")
        resp = auth_client.get("/api/data")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("months", resp.json())

    def test_data_maintenance_on(self) -> None:
        """GET /api/data returns 503 when maintenance is ON."""
        set_maintenance_mode(False)
        auth_client, _ = get_auth_client("data_on@example.com", "Pass12345!")
        set_maintenance_mode(True)
        resp = auth_client.get("/api/data")
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json().get("error"), "maintenance")


class TestUserSettingsEndpointsMatrix(MaintenanceTestCase):
    """Test /api/state, /api/profile, /api/onboarding/cycle-day, /api/auth/google."""

    def test_state_get_maintenance_off(self) -> None:
        """GET /api/state returns 200 when authenticated and maintenance is OFF."""
        set_maintenance_mode(False)
        auth_client, _ = get_auth_client("state_off@example.com", "Pass12345!")
        resp = auth_client.get("/api/state")
        self.assertEqual(resp.status_code, 200)

    def test_state_get_maintenance_on(self) -> None:
        """GET /api/state returns 503 when maintenance is ON."""
        set_maintenance_mode(False)
        auth_client, _ = get_auth_client("state_on@example.com", "Pass12345!")
        set_maintenance_mode(True)
        resp = auth_client.get("/api/state")
        self.assertEqual(resp.status_code, 503)

    def test_state_post_maintenance_off(self) -> None:
        """POST /api/state returns 200 when valid JSON body and maintenance is OFF."""
        set_maintenance_mode(False)
        auth_client, _ = get_auth_client("state_post_off@example.com", "Pass12345!")
        resp = auth_client.post("/api/state", json={"customKey": "customVal"})
        self.assertEqual(resp.status_code, 200)

    def test_state_post_maintenance_on(self) -> None:
        """POST /api/state returns 503 when maintenance is ON."""
        set_maintenance_mode(False)
        auth_client, _ = get_auth_client("state_post_on@example.com", "Pass12345!")
        set_maintenance_mode(True)
        resp = auth_client.post("/api/state", json={"customKey": "customVal"})
        self.assertEqual(resp.status_code, 503)

    def test_profile_get_maintenance_off(self) -> None:
        """GET /api/profile returns 200 when authenticated and maintenance is OFF."""
        set_maintenance_mode(False)
        auth_client, _ = get_auth_client("prof_off@example.com", "Pass12345!")
        resp = auth_client.get("/api/profile")
        self.assertEqual(resp.status_code, 200)

    def test_profile_get_maintenance_on(self) -> None:
        """GET /api/profile returns 503 when maintenance is ON."""
        set_maintenance_mode(False)
        auth_client, _ = get_auth_client("prof_on@example.com", "Pass12345!")
        set_maintenance_mode(True)
        resp = auth_client.get("/api/profile")
        self.assertEqual(resp.status_code, 503)

    def test_profile_post_maintenance_off(self) -> None:
        """POST /api/profile returns 200 when updating profile and maintenance is OFF."""
        set_maintenance_mode(False)
        auth_client, _ = get_auth_client("prof_p_off@example.com", "Pass12345!")
        resp = auth_client.post("/api/profile", json={"account_holder_keys": ["KEY1"]})
        self.assertEqual(resp.status_code, 200)

    def test_profile_post_maintenance_on(self) -> None:
        """POST /api/profile returns 503 when maintenance is ON."""
        set_maintenance_mode(False)
        auth_client, _ = get_auth_client("prof_p_on@example.com", "Pass12345!")
        set_maintenance_mode(True)
        resp = auth_client.post("/api/profile", json={"account_holder_keys": ["KEY1"]})
        self.assertEqual(resp.status_code, 503)

    def test_onboarding_cycle_day_maintenance_off(self) -> None:
        """GET /api/onboarding/cycle-day returns 200 when maintenance is OFF."""
        set_maintenance_mode(False)
        auth_client, _ = get_auth_client("cycle_off@example.com", "Pass12345!")
        resp = auth_client.get("/api/onboarding/cycle-day")
        self.assertEqual(resp.status_code, 200)

    def test_onboarding_cycle_day_maintenance_on(self) -> None:
        """GET /api/onboarding/cycle-day returns 503 when maintenance is ON."""
        set_maintenance_mode(False)
        auth_client, _ = get_auth_client("cycle_on@example.com", "Pass12345!")
        set_maintenance_mode(True)
        resp = auth_client.get("/api/onboarding/cycle-day")
        self.assertEqual(resp.status_code, 503)

    def test_auth_google_maintenance_off(self) -> None:
        """GET /api/auth/google returns not_configured 503 when maintenance is OFF (baseline)."""
        set_maintenance_mode(False)
        resp = self.client.get("/api/auth/google")
        # Baseline returns 503 with error: not_configured (unconfigured Google credentials)
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json().get("error"), "not_configured")

    def test_auth_google_maintenance_on(self) -> None:
        """GET /api/auth/google returns maintenance 503 when maintenance is ON."""
        set_maintenance_mode(True)
        resp = self.client.get("/api/auth/google")
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json().get("error"), "maintenance")


class TestStaticFilesAndDashboardMatrix(MaintenanceTestCase):
    """Test static dashboard and static file shielding in ON and OFF states."""

    def test_root_dashboard_maintenance_off(self) -> None:
        """GET / serves the dashboard HTML when maintenance is OFF."""
        set_maintenance_mode(False)
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers.get("content-type", ""))

    def test_root_dashboard_maintenance_on(self) -> None:
        """GET / must be blocked with 503 JSON when maintenance is ON."""
        set_maintenance_mode(True)
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 503)
        self.assertIn("application/json", resp.headers.get("content-type", ""))
        self.assertEqual(resp.json().get("error"), "maintenance")

    def test_index_html_maintenance_off(self) -> None:
        """GET /index.html serves the dashboard HTML when maintenance is OFF."""
        set_maintenance_mode(False)
        resp = self.client.get("/index.html")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("text/html", resp.headers.get("content-type", ""))

    def test_index_html_maintenance_on(self) -> None:
        """GET /index.html must be blocked with 503 JSON when maintenance is ON."""
        set_maintenance_mode(True)
        resp = self.client.get("/index.html")
        self.assertEqual(resp.status_code, 503)
        self.assertIn("application/json", resp.headers.get("content-type", ""))
        self.assertEqual(resp.json().get("error"), "maintenance")


class TestUnknownRoutesMatrix(MaintenanceTestCase):
    """Test unknown and nonexistent routes in ON and OFF states."""

    def test_unknown_api_path_maintenance_off(self) -> None:
        """GET /api/nonexistent-route returns 404 when maintenance is OFF."""
        set_maintenance_mode(False)
        resp = self.client.get("/api/nonexistent-route")
        self.assertEqual(resp.status_code, 404)

    def test_unknown_api_path_maintenance_on(self) -> None:
        """GET /api/nonexistent-route returns 503 when maintenance is ON (all traffic shielded)."""
        set_maintenance_mode(True)
        resp = self.client.get("/api/nonexistent-route")
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json().get("error"), "maintenance")

    def test_unknown_static_path_maintenance_off(self) -> None:
        """GET /nonexistent-file.js returns 404 when maintenance is OFF."""
        set_maintenance_mode(False)
        resp = self.client.get("/nonexistent-file.js")
        self.assertEqual(resp.status_code, 404)

    def test_unknown_static_path_maintenance_on(self) -> None:
        """GET /nonexistent-file.js returns 503 when maintenance is ON."""
        set_maintenance_mode(True)
        resp = self.client.get("/nonexistent-file.js")
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json().get("error"), "maintenance")


class Test503ResponsePayloadContract(MaintenanceTestCase):
    """Verify exact JSON payload keys and HTTP header contracts on 503 responses."""

    def test_503_response_format_and_headers(self) -> None:
        """Verify status code, headers, and JSON structure of 503 maintenance responses."""
        set_maintenance_mode(True, message="Database upgrade in progress", retry_after=90)
        resp = self.client.get("/api/me")

        self.assertEqual(resp.status_code, 503)
        self.assertIn("application/json", resp.headers.get("content-type", ""))

        # Header Retry-After
        self.assertIn("Retry-After", resp.headers)
        retry_val = resp.headers["Retry-After"]
        self.assertTrue(retry_val.isdigit(), f"Retry-After must be integer digits: {retry_val}")
        self.assertEqual(int(retry_val), 90)

        # JSON body
        body = resp.json()
        self.assertEqual(body.get("error"), "maintenance")
        self.assertEqual(body.get("message"), "Database upgrade in progress")
        self.assertEqual(body.get("retry_after"), 90)


if __name__ == "__main__":
    unittest.main()
