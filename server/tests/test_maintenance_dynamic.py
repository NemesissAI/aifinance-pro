"""Tier 4: Dynamic Runtime Toggles, Custom Operator Messages & Environment Configuration.

Authoritative Source:
- ORIGINAL_REQUEST.md § Verification (testing env var startup and toggles)
- PROJECT.md § Architecture (Dynamic Configuration & Zero-Downtime Toggling)
- PROJECT.md § Interface Contracts (`set_maintenance_mode`, `reset_maintenance_mode`, `is_maintenance_active`)

Verifies:
- Runtime dynamic toggle flipping without restarting the server process
- Custom operator message setting, updating, and propagation to API responses
- Custom Retry-After interval configuration at runtime
- Environment variable configuration (MAINTENANCE_MODE, MAINTENANCE_MESSAGE, MAINTENANCE_RETRY_AFTER)
- Runtime overrides take precedence over environment variables
- Resetting maintenance mode restores configuration to environment defaults
- Truthy and falsy string parsing for environment variables
"""

from __future__ import annotations

import os
import unittest
from server.tests.conftest import (
    MaintenanceTestCase,
    is_maintenance_active,
    reset_maintenance_mode,
    set_maintenance_mode,
)


class TestRuntimeDynamicToggles(MaintenanceTestCase):
    """Verify runtime toggling via set_maintenance_mode and reset_maintenance_mode."""

    def test_runtime_toggle_flipping(self) -> None:
        """Verify seamless transitions between OFF -> ON -> OFF at runtime."""
        # 1. Initially OFF
        reset_maintenance_mode()
        resp_off = self.client.get("/api/maintenance")
        self.assertEqual(resp_off.status_code, 200)
        self.assertFalse(resp_off.json().get("active"))
        self.assertEqual(self.client.get("/api/me").status_code, 401)

        # 2. Toggle ON
        set_maintenance_mode(True)
        self.assertTrue(is_maintenance_active())
        resp_on = self.client.get("/api/maintenance")
        self.assertEqual(resp_on.status_code, 200)
        self.assertTrue(resp_on.json().get("active"))
        self.assertEqual(self.client.get("/api/me").status_code, 503)

        # 3. Toggle OFF
        set_maintenance_mode(False)
        self.assertFalse(is_maintenance_active())
        resp_off_again = self.client.get("/api/maintenance")
        self.assertEqual(resp_off_again.status_code, 200)
        self.assertFalse(resp_off_again.json().get("active"))
        self.assertEqual(self.client.get("/api/me").status_code, 401)

    def test_runtime_reset_clears_override(self) -> None:
        """Calling reset_maintenance_mode() clears active override and returns to default inactive state."""
        set_maintenance_mode(True, message="Temporary override", retry_after=45)
        self.assertTrue(is_maintenance_active())

        reset_maintenance_mode()
        self.assertFalse(is_maintenance_active())

        resp = self.client.get("/api/maintenance")
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(resp.json().get("active"))

    def test_rapid_alternating_toggles(self) -> None:
        """Flipping state rapidly 10 times in a loop maintains immediate consistency."""
        for i in range(10):
            state = (i % 2 == 0)
            set_maintenance_mode(state)
            self.assertEqual(is_maintenance_active(), state)

            maint_resp = self.client.get("/api/maintenance")
            self.assertEqual(maint_resp.json().get("active"), state)

            me_resp = self.client.get("/api/me")
            expected_code = 503 if state else 401
            self.assertEqual(me_resp.status_code, expected_code)


class TestOperatorMessagesAndIntervals(MaintenanceTestCase):
    """Verify operator messages and custom retry intervals update dynamically."""

    def test_custom_operator_message_propagation(self) -> None:
        """Custom operator message appears in both /api/maintenance and 503 error payloads."""
        custom_msg = "Database schema migration in progress. Expected completion in 15 minutes."
        set_maintenance_mode(True, message=custom_msg)

        # Check /api/maintenance
        status_resp = self.client.get("/api/maintenance")
        self.assertEqual(status_resp.status_code, 200)
        self.assertEqual(status_resp.json().get("message"), custom_msg)

        # Check 503 payload on protected endpoint
        blocked_resp = self.client.get("/api/me")
        self.assertEqual(blocked_resp.status_code, 503)
        self.assertEqual(blocked_resp.json().get("message"), custom_msg)

    def test_dynamic_operator_message_update(self) -> None:
        """Operator updates message mid-maintenance; client sees the new message immediately."""
        msg1 = "Step 1 of 3: Exporting statements backup..."
        set_maintenance_mode(True, message=msg1)
        resp1 = self.client.get("/api/maintenance")
        self.assertEqual(resp1.json().get("message"), msg1)

        msg2 = "Step 2 of 3: Applying database column indexes..."
        set_maintenance_mode(True, message=msg2)
        resp2 = self.client.get("/api/maintenance")
        self.assertEqual(resp2.json().get("message"), msg2)

    def test_custom_retry_after_interval(self) -> None:
        """Custom retry_after interval propagates to Retry-After header and JSON bodies."""
        set_maintenance_mode(True, retry_after=300)

        # /api/maintenance
        status_resp = self.client.get("/api/maintenance")
        self.assertEqual(status_resp.json().get("retry_after"), 300)

        # 503 response
        blocked_resp = self.client.get("/api/me")
        self.assertEqual(blocked_resp.status_code, 503)
        self.assertEqual(blocked_resp.headers.get("Retry-After"), "300")
        self.assertEqual(blocked_resp.json().get("retry_after"), 300)


class TestEnvironmentVariableConfiguration(MaintenanceTestCase):
    """Verify server configuration via MAINTENANCE_* environment variables."""

    def tearDown(self) -> None:
        os.environ.pop("MAINTENANCE_MODE", None)
        os.environ.pop("MAINTENANCE_MESSAGE", None)
        os.environ.pop("MAINTENANCE_RETRY_AFTER", None)
        reset_maintenance_mode()
        super().tearDown()

    def test_env_var_maintenance_mode_on(self) -> None:
        """MAINTENANCE_MODE=1 activates maintenance mode when no runtime override is set."""
        os.environ["MAINTENANCE_MODE"] = "1"
        reset_maintenance_mode()  # Ensure runtime override is cleared

        self.assertTrue(is_maintenance_active())
        resp = self.client.get("/api/maintenance")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json().get("active"))

        blocked = self.client.get("/api/me")
        self.assertEqual(blocked.status_code, 503)

    def test_env_var_message_and_retry_after(self) -> None:
        """MAINTENANCE_MESSAGE and MAINTENANCE_RETRY_AFTER are read from environment."""
        os.environ["MAINTENANCE_MODE"] = "1"
        os.environ["MAINTENANCE_MESSAGE"] = "Env-configured maintenance window"
        os.environ["MAINTENANCE_RETRY_AFTER"] = "400"
        reset_maintenance_mode()

        resp = self.client.get("/api/maintenance")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json().get("message"), "Env-configured maintenance window")
        self.assertEqual(resp.json().get("retry_after"), 400)

        blocked = self.client.get("/api/me")
        self.assertEqual(blocked.status_code, 503)
        self.assertEqual(blocked.headers.get("Retry-After"), "400")

    def test_runtime_override_precedence_over_env(self) -> None:
        """Runtime override takes precedence over environment variable settings."""
        os.environ["MAINTENANCE_MODE"] = "1"
        reset_maintenance_mode()
        self.assertTrue(is_maintenance_active())

        # Runtime override deactivates maintenance despite env var being 1
        set_maintenance_mode(False)
        self.assertFalse(is_maintenance_active())
        resp = self.client.get("/api/maintenance")
        self.assertFalse(resp.json().get("active"))
        self.assertEqual(self.client.get("/api/me").status_code, 401)

        # Resetting restores the env var state
        reset_maintenance_mode()
        self.assertTrue(is_maintenance_active())
        self.assertEqual(self.client.get("/api/me").status_code, 503)

    def test_env_var_truthy_falsy_parsing(self) -> None:
        """Verify various truthy and falsy strings in MAINTENANCE_MODE."""
        truthy_values = ["1", "true", "True", "TRUE", "yes", "YES", "on", "ON"]
        for val in truthy_values:
            os.environ["MAINTENANCE_MODE"] = val
            reset_maintenance_mode()
            self.assertTrue(is_maintenance_active(), f"Failed to parse truthy value: {val!r}")

        falsy_values = ["0", "false", "False", "FALSE", "no", "NO", "off", "OFF", ""]
        for val in falsy_values:
            os.environ["MAINTENANCE_MODE"] = val
            reset_maintenance_mode()
            self.assertFalse(is_maintenance_active(), f"Failed to parse falsy value: {val!r}")


if __name__ == "__main__":
    unittest.main()
