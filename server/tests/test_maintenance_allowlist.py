"""Tier 2: Allowlist Boundaries, Path Normalization, Prefix Smuggling & Security.

Authoritative Source:
- ORIGINAL_REQUEST.md § Requirements R1, R2
- PROJECT.md § Architecture (Allowlist Policy, Strict exact path matching with posixpath.normpath)
- RFC 9110 § 10.2.3 (Retry-After header specifications)

Verifies:
- Trailing slash handling (/api/health/, /api/maintenance/, multiple slashes)
- Query parameters are ignored during path allowlist evaluation
- Prefix smuggling attempts (/api/health_admin, /api/maintenance_mode) are blocked with 503
- Path traversal attempts (/api/health/../me) resolve to target paths and are blocked
- HEAD requests on allowlisted paths return 200 OK without body
- RFC 9110 compliant Retry-After header format (positive integer seconds, no HTTP-date)
"""

from __future__ import annotations

import re
import unittest
from server.tests.conftest import (
    MaintenanceTestCase,
    reset_maintenance_mode,
    set_maintenance_mode,
)


class TestTrailingSlashes(MaintenanceTestCase):
    """Verify path normalization handles trailing slashes gracefully without breaking allowlist."""

    def test_trailing_slash_health(self) -> None:
        """GET /api/health/ returns 200 OK during maintenance."""
        set_maintenance_mode(True)
        resp = self.client.get("/api/health/")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("status"), "ok")

    def test_multiple_trailing_slashes_health(self) -> None:
        """GET /api/health/// returns 200 OK during maintenance."""
        set_maintenance_mode(True)
        resp = self.client.get("/api/health///")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("status"), "ok")

    def test_trailing_slash_maintenance(self) -> None:
        """GET /api/maintenance/ returns 200 OK during maintenance."""
        set_maintenance_mode(True)
        resp = self.client.get("/api/maintenance/")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("active"))

    def test_multiple_trailing_slashes_maintenance(self) -> None:
        """GET /api/maintenance/// returns 200 OK during maintenance."""
        set_maintenance_mode(True)
        resp = self.client.get("/api/maintenance///")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("active"))


class TestQueryParameters(MaintenanceTestCase):
    """Verify query strings do not alter path allowlist matching."""

    def test_query_params_health_simple(self) -> None:
        """GET /api/health?probe=liveness returns 200 OK during maintenance."""
        set_maintenance_mode(True)
        resp = self.client.get("/api/health?probe=liveness")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("status"), "ok")

    def test_query_params_health_complex(self) -> None:
        """GET /api/health?v=1&k8s=probe&t=123456 returns 200 OK during maintenance."""
        set_maintenance_mode(True)
        resp = self.client.get("/api/health?v=1&k8s=probe&t=123456")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("status"), "ok")

    def test_query_params_maintenance_simple(self) -> None:
        """GET /api/maintenance?full=1 returns 200 OK during maintenance."""
        set_maintenance_mode(True)
        resp = self.client.get("/api/maintenance?full=1")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("active"))

    def test_query_params_maintenance_cache_buster(self) -> None:
        """GET /api/maintenance?_cb=987654321 returns 200 OK during maintenance."""
        set_maintenance_mode(True)
        resp = self.client.get("/api/maintenance?_cb=987654321")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("active"))


class TestPrefixSmugglingPrevention(MaintenanceTestCase):
    """Verify strict path equality prevents partial prefix or subpath smuggling."""

    def test_smuggling_health_admin(self) -> None:
        """GET /api/health_admin must return 503 during maintenance."""
        set_maintenance_mode(True)
        resp = self.client.get("/api/health_admin")
        self.assertEqual(resp.status_code, 503)

    def test_smuggling_health_subpath(self) -> None:
        """GET /api/health/details must return 503 during maintenance."""
        set_maintenance_mode(True)
        resp = self.client.get("/api/health/details")
        self.assertEqual(resp.status_code, 503)

    def test_smuggling_health_status(self) -> None:
        """GET /api/healthstatus must return 503 during maintenance."""
        set_maintenance_mode(True)
        resp = self.client.get("/api/healthstatus")
        self.assertEqual(resp.status_code, 503)

    def test_smuggling_maintenance_mode(self) -> None:
        """GET /api/maintenance_mode must return 503 during maintenance."""
        set_maintenance_mode(True)
        resp = self.client.get("/api/maintenance_mode")
        self.assertEqual(resp.status_code, 503)

    def test_smuggling_maintenance_subpath(self) -> None:
        """GET /api/maintenance/debug must return 503 during maintenance."""
        set_maintenance_mode(True)
        resp = self.client.get("/api/maintenance/debug")
        self.assertEqual(resp.status_code, 503)

    def test_smuggling_maintenance_status(self) -> None:
        """GET /api/maintenancestatus must return 503 during maintenance."""
        set_maintenance_mode(True)
        resp = self.client.get("/api/maintenancestatus")
        self.assertEqual(resp.status_code, 503)

    def test_smuggling_partial_prefix_heal(self) -> None:
        """GET /api/heal must return 503 during maintenance."""
        set_maintenance_mode(True)
        resp = self.client.get("/api/heal")
        self.assertEqual(resp.status_code, 503)

    def test_smuggling_partial_prefix_maint(self) -> None:
        """GET /api/maint must return 503 during maintenance."""
        set_maintenance_mode(True)
        resp = self.client.get("/api/maint")
        self.assertEqual(resp.status_code, 503)


class TestPathTraversalSecurity(MaintenanceTestCase):
    """Verify dot-dot path traversal attempts are safely handled."""

    def test_traversal_health_to_me(self) -> None:
        """GET /api/health/../me normalizes to /api/me and must return 503."""
        set_maintenance_mode(True)
        resp = self.client.get("/api/health/../me")
        self.assertEqual(resp.status_code, 503)

    def test_traversal_maintenance_to_login(self) -> None:
        """GET /api/maintenance/../login normalizes to /api/login and must return 503."""
        set_maintenance_mode(True)
        resp = self.client.get("/api/maintenance/../login")
        self.assertEqual(resp.status_code, 503)

    def test_traversal_dot_dot_root(self) -> None:
        """GET /api/health/../../ normalizes to / and must return 503."""
        set_maintenance_mode(True)
        resp = self.client.get("/api/health/../../")
        self.assertEqual(resp.status_code, 503)

    def test_traversal_resolving_to_health(self) -> None:
        """GET /api/v1/../health normalizes to /api/health and returns 200 OK."""
        set_maintenance_mode(True)
        resp = self.client.get("/api/v1/../health")
        self.assertEqual(resp.status_code, 200)

    def test_dot_slash_in_path(self) -> None:
        """GET /./api/health normalizes to /api/health and returns 200 OK."""
        set_maintenance_mode(True)
        resp = self.client.get("/./api/health")
        self.assertEqual(resp.status_code, 200)


class TestHttpMethodsAndHead(MaintenanceTestCase):
    """Verify HEAD probes and disallowed methods on allowlisted and protected endpoints."""

    def test_head_health_returns_200(self) -> None:
        """HEAD /api/health returns 200 OK (essential for container/LB liveness probes)."""
        set_maintenance_mode(True)
        resp = self.client.head("/api/health")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.text, "")

    def test_head_maintenance_returns_200(self) -> None:
        """HEAD /api/maintenance returns 200 OK."""
        set_maintenance_mode(True)
        resp = self.client.head("/api/maintenance")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.text, "")

    def test_head_blocked_route_returns_503(self) -> None:
        """HEAD /api/me returns 503 with Retry-After header."""
        set_maintenance_mode(True)
        resp = self.client.head("/api/me")
        self.assertEqual(resp.status_code, 503)
        self.assertIn("Retry-After", resp.headers)

    def test_head_root_returns_503(self) -> None:
        """HEAD / returns 503 during maintenance."""
        set_maintenance_mode(True)
        resp = self.client.head("/")
        self.assertEqual(resp.status_code, 503)

    def test_disallowed_post_on_health(self) -> None:
        """POST /api/health returns 405 Method Not Allowed or 503."""
        set_maintenance_mode(True)
        resp = self.client.post("/api/health")
        self.assertIn(resp.status_code, [405, 503])

    def test_disallowed_delete_on_health(self) -> None:
        """DELETE /api/health returns 405 Method Not Allowed or 503."""
        set_maintenance_mode(True)
        resp = self.client.delete("/api/health")
        self.assertIn(resp.status_code, [405, 503])


class TestRFC9110RetryAfter(MaintenanceTestCase):
    """Verify RFC 9110 compliance for the Retry-After response header."""

    def test_retry_after_header_is_strictly_integer(self) -> None:
        """Retry-After header must consist strictly of ASCII digits representing seconds."""
        set_maintenance_mode(True, retry_after=60)
        resp = self.client.get("/api/me")
        self.assertEqual(resp.status_code, 503)

        raw_header = resp.headers.get("Retry-After", "")
        self.assertTrue(
            re.fullmatch(r"[1-9]\d*", raw_header),
            f"Retry-After header '{raw_header}' must be positive integer seconds conforming to RFC 9110.",
        )

    def test_retry_after_header_not_http_date(self) -> None:
        """Retry-After header must NOT use the HTTP-date format (e.g. Wed, 21 Oct ...)."""
        set_maintenance_mode(True)
        resp = self.client.get("/api/me")
        raw_header = resp.headers.get("Retry-After", "")
        self.assertNotIn("GMT", raw_header)
        self.assertNotIn(":", raw_header)

    def test_retry_after_matches_json_payload(self) -> None:
        """Integer value in Retry-After header must exactly match the retry_after in JSON body."""
        set_maintenance_mode(True, retry_after=180)
        resp = self.client.get("/api/me")
        self.assertEqual(resp.status_code, 503)

        header_val = int(resp.headers["Retry-After"])
        body_val = resp.json().get("retry_after")
        self.assertEqual(header_val, body_val)
        self.assertEqual(header_val, 180)

    def test_retry_after_header_case_insensitive_access(self) -> None:
        """Verify headers dictionary allows case-insensitive access to Retry-After."""
        set_maintenance_mode(True)
        resp = self.client.get("/api/me")
        self.assertIn("retry-after", resp.headers)
        self.assertIn("Retry-After", resp.headers)
        self.assertEqual(resp.headers["retry-after"], resp.headers["Retry-After"])


if __name__ == "__main__":
    unittest.main()
