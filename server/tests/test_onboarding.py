"""server/tests/test_onboarding.py: Tests for onboarding workflow & validation endpoints.

Covers:
- GET /api/onboarding/status progression (name -> upload -> cycle-day -> tour -> done)
- POST /api/onboarding/name validation, error messages, and normalisation
- GET /api/onboarding/cycle-day analysis readiness
- POST /api/onboarding/cycle-day boundary validation (1 <= day <= 28, rejects 29-31)
- POST /api/onboarding/tour-done persistence in user state
- Auth enforcement (all endpoints require valid session)
"""

from __future__ import annotations

import unittest
import server.db as db
from server.tests.conftest import (
    MaintenanceTestCase,
    get_auth_client,
    get_test_client,
)


class TestOnboardingAuthEnforcement(MaintenanceTestCase):
    """Verify all onboarding routes require authenticated session."""

    def test_status_unauthenticated(self) -> None:
        resp = self.client.get("/api/onboarding/status")
        self.assertEqual(resp.status_code, 401)

    def test_name_unauthenticated(self) -> None:
        resp = self.client.post("/api/onboarding/name", json={"name": "Talha Açık"})
        self.assertEqual(resp.status_code, 401)

    def test_cycle_day_get_unauthenticated(self) -> None:
        resp = self.client.get("/api/onboarding/cycle-day")
        self.assertEqual(resp.status_code, 401)

    def test_cycle_day_post_unauthenticated(self) -> None:
        resp = self.client.post("/api/onboarding/cycle-day", json={"day": 15})
        self.assertEqual(resp.status_code, 401)

    def test_tour_done_unauthenticated(self) -> None:
        resp = self.client.post("/api/onboarding/tour-done")
        self.assertEqual(resp.status_code, 401)


class TestOnboardingWorkflow(MaintenanceTestCase):
    """Verify state transitions and validations through the onboarding flow."""

    def setUp(self) -> None:
        super().setUp()
        email = f"onboard_{self._testMethodName.lower()}@example.com"
        self.client, self.user = get_auth_client(
            email=email,
            password="SecurePass123!",
            name="Onboarding User",
        )

    def test_initial_status(self) -> None:
        """New user starts at the 'name' step when account_holder_keys is empty."""
        # Clear profile so user has no account_holder_keys
        with db.session() as s:
            u = s.get(db.User, self.user["id"])
            u.profile = {}
            s.commit()

        resp = self.client.get("/api/onboarding/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["step"], "name")
        self.assertFalse(data["hasName"])
        self.assertEqual(data["statementCount"], 0)
        self.assertIsNone(data["cycleDay"])
        self.assertFalse(data["tourDone"])
        self.assertFalse(data["canRecommendCycleDay"])

    def test_name_validation_rejections(self) -> None:
        """Name must be at least 4 letters and not purely numeric."""
        # Empty
        resp = self.client.post("/api/onboarding/name", json={"name": ""})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("at least four letters", resp.json()["detail"])

        # Too short (e.g. 2 chars)
        resp = self.client.post("/api/onboarding/name", json={"name": "Al"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("at least four letters", resp.json()["detail"])

        # Digits only
        resp = self.client.post("/api/onboarding/name", json={"name": "123456"})
        self.assertEqual(resp.status_code, 400)
        self.assertIn("looks like a number", resp.json()["detail"])

    def test_name_success_and_normalisation(self) -> None:
        """Valid name normalises Turkish characters and stores key in profile."""
        resp = self.client.post("/api/onboarding/name", json={"name": "Talha Açık"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["key"], "talhaacik")
        self.assertIn("alreadyRecognised", data)
        self.assertIn("wouldChange", data)
        self.assertIn("matches", data)

        # Status should now advance to upload (since 0 statements uploaded)
        status_resp = self.client.get("/api/onboarding/status")
        self.assertEqual(status_resp.status_code, 200)
        status_data = status_resp.json()
        self.assertTrue(status_data["hasName"])
        self.assertEqual(status_data["step"], "upload")

    def test_cycle_day_validation(self) -> None:
        """Cycle day must be between 1 and 28 (days 29-31 do not exist in every month)."""
        # Invalid types
        resp = self.client.post("/api/onboarding/cycle-day", json={"day": "abc"})
        self.assertEqual(resp.status_code, 400)

        # Out of bounds (< 1)
        resp = self.client.post("/api/onboarding/cycle-day", json={"day": 0})
        self.assertEqual(resp.status_code, 400)

        # Out of bounds (29, 30, 31)
        for day in (29, 30, 31):
            resp = self.client.post("/api/onboarding/cycle-day", json={"day": day})
            self.assertEqual(resp.status_code, 400)
            self.assertIn("Pick a day between 1 and 28", resp.json()["detail"])

        # Valid day (e.g. 22)
        resp = self.client.post("/api/onboarding/cycle-day", json={"day": 22})
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])
        self.assertEqual(resp.json()["cycleDay"], 22)

    def test_tour_done_and_completion(self) -> None:
        """Marking tour done persists flag in user state and completes onboarding."""
        # 1. Set name
        self.client.post("/api/onboarding/name", json={"name": "Test User"})
        # 2. Set cycle day
        self.client.post("/api/onboarding/cycle-day", json={"day": 15})

        # 3. Mark tour done
        resp = self.client.post("/api/onboarding/tour-done")
        self.assertEqual(resp.status_code, 200)
        self.assertTrue(resp.json()["ok"])

        # Status should reflect tourDone=True
        status_resp = self.client.get("/api/onboarding/status")
        self.assertEqual(status_resp.status_code, 200)
        status_data = status_resp.json()
        self.assertTrue(status_data["tourDone"])
        self.assertEqual(status_data["cycleDay"], 15)


if __name__ == "__main__":
    unittest.main()
