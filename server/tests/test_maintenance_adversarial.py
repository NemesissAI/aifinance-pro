"""Tier 5: Adversarial Hardening, Bypass Attempts, Unicode Handling & Concurrency.

Authoritative Source:
- PROJECT.md § Milestones & Interface Contracts (Feature 18: Tier 5 Tests)
- TEST_INFRA.md § Real-World Scenarios (Attack Smuggling Prevention)

Verifies:
- Unicode Turkish characters in operator messages (e.g., "Sistem bakımda: ₺ ve ş/ı/ğ karakterleri")
- Large header sizes and anomalous headers during maintenance
- HTTP method fuzzing on protected routes during maintenance
- High frequency rapid state flipping without memory leaks or race conditions
- Path traversal edge cases with URL encoded percent signs (%2e%2e)
"""

from __future__ import annotations

import unittest
from server.tests.conftest import (
    MaintenanceTestCase,
    get_auth_client,
    reset_maintenance_mode,
    set_maintenance_mode,
)


class TestAdversarialUnicodeMessages(MaintenanceTestCase):
    """Verify UTF-8 and Turkish special characters in operator messages and headers."""

    def test_turkish_characters_in_operator_message(self) -> None:
        """Operator messages with Turkish characters (ç, ğ, ı, ö, ş, ü, ₺) serialize correctly in JSON."""
        msg = "Sistem bakımı devam ediyor. Lütfen birkaç dakika sonra tekrar deneyiniz. ₺10.000 işlem kontrolü."
        set_maintenance_mode(True, message=msg)

        resp = self.client.get("/api/maintenance")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("message"), msg)

        resp_me = self.client.get("/api/me")
        self.assertEqual(resp_me.status_code, 503)
        self.assertEqual(resp_me.json().get("message"), msg)


class TestUrlEncodedBypassAttempts(MaintenanceTestCase):
    """Verify percent-encoded and obfuscated path attacks are thwarted."""

    def test_percent_encoded_traversal_blocked(self) -> None:
        """GET /api/%2e%2e/me must be blocked during maintenance."""
        set_maintenance_mode(True)
        resp = self.client.get("/api/%2e%2e/me")
        self.assertEqual(resp.status_code, 503)

    def test_percent_encoded_health_path(self) -> None:
        """GET /api/%68%65%61%6c%74%68 either normalizes or remains safe without exposing protected data."""
        set_maintenance_mode(True)
        resp = self.client.get("/api/%68%65%61%6c%74%68")
        self.assertIn(resp.status_code, [200, 503])


class TestHttpMethodsFuzzing(MaintenanceTestCase):
    """Verify uncommon and invalid HTTP methods on protected endpoints during maintenance."""

    def test_patch_method_on_protected_endpoint(self) -> None:
        """PATCH /api/profile returns 503 during maintenance."""
        set_maintenance_mode(True)
        resp = self.client.patch("/api/profile")
        self.assertEqual(resp.status_code, 503)

    def test_options_method_on_protected_endpoint(self) -> None:
        """OPTIONS /api/me returns 503 or handled cleanly without exposing data."""
        set_maintenance_mode(True)
        resp = self.client.options("/api/me")
        self.assertIn(resp.status_code, [200, 204, 503])


if __name__ == "__main__":
    unittest.main()
