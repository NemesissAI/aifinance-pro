"""Shared test configuration, database isolation fixtures, and client helpers.

CRITICAL DATA SAFETY REQUIREMENT:
DATABASE_URL must be configured to point to an isolated temporary SQLite database
BEFORE server.app or server.db is imported, guaranteeing that the live aifinance.db
file in the project root is never touched or mutated by test runs.
"""

from __future__ import annotations

import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any, Generator, Tuple

# 1. CRITICAL: Initialize temporary isolated database directory BEFORE server imports
_TEST_TEMP_DIR = tempfile.TemporaryDirectory()
_TEST_DB_PATH = Path(_TEST_TEMP_DIR.name) / "test_aifinance.db"

os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB_PATH.resolve()}"
os.environ["SESSION_SECRET"] = "test-session-secret-key-32-chars-long-minimum"
os.environ["AIFP_ENV"] = "testing"

# Ensure repo root and sub-packages are on sys.path
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_REPO_ROOT / "tools") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "tools"))
if str(_REPO_ROOT / "server") not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT / "server"))
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Now import server modules safely
import server.db as db
import server.passwords as passwords
from fastapi.testclient import TestClient
from server.app import app

# Assert absolute database isolation
_engine_url = str(db.engine.url)
assert "test_aifinance.db" in _engine_url, (
    f"CRITICAL SAFETY VIOLATION: db.engine points to {_engine_url} instead of "
    f"isolated temporary database {_TEST_DB_PATH}!"
)

# Initialize schema in the temporary database
db.init()


# ── Maintenance Mode Helpers ──────────────────────────────────────────────────

def set_maintenance_mode(
    active: bool | None = True,
    message: str | None = None,
    retry_after: int | None = None,
) -> None:
    """Set maintenance mode state at runtime, delegating to server.maintenance if present."""
    try:
        m = importlib.import_module("server.maintenance")
        if hasattr(m, "set_maintenance_mode"):
            m.set_maintenance_mode(active, message=message, retry_after=retry_after)
            return
    except (ImportError, AttributeError):
        pass

    # Fallback to environment variables if server.maintenance is not yet active
    if active is True:
        os.environ["MAINTENANCE_MODE"] = "1"
    elif active is False:
        os.environ["MAINTENANCE_MODE"] = "0"
    else:
        os.environ.pop("MAINTENANCE_MODE", None)

    if message is not None:
        os.environ["MAINTENANCE_MESSAGE"] = message
    else:
        os.environ.pop("MAINTENANCE_MESSAGE", None)

    if retry_after is not None:
        os.environ["MAINTENANCE_RETRY_AFTER"] = str(retry_after)
    else:
        os.environ.pop("MAINTENANCE_RETRY_AFTER", None)


def reset_maintenance_mode() -> None:
    """Reset maintenance mode runtime override (in-memory state only).

    Does NOT touch environment variables — tests that set MAINTENANCE_MODE
    via os.environ expect it to survive a reset.  The env-var cleanup belongs
    in each test's own tearDown.
    """
    try:
        m = importlib.import_module("server.maintenance")
        if hasattr(m, "reset_maintenance_mode"):
            m.reset_maintenance_mode()
    except (ImportError, AttributeError):
        pass


def is_maintenance_active() -> bool:
    """Check whether maintenance mode is currently reported as active."""
    try:
        m = importlib.import_module("server.maintenance")
        if hasattr(m, "is_maintenance_active"):
            return bool(m.is_maintenance_active())
    except (ImportError, AttributeError):
        pass

    val = os.environ.get("MAINTENANCE_MODE", "").strip().lower()
    return val in ("1", "true", "yes", "on")


# ── Test Client & Auth Helpers ───────────────────────────────────────────────

def get_test_client() -> TestClient:
    """Return a new TestClient instance wired to the FastAPI application."""
    return TestClient(app, raise_server_exceptions=False)


def create_test_user(
    email: str = "testuser@example.com",
    password: str = "SecurePass123!",
    name: str = "Test User",
) -> dict[str, Any]:
    """Create a persistent user in the isolated test database."""
    from argon2 import PasswordHasher
    hasher = PasswordHasher()
    with db.session() as s:
        existing = db.user_by_email(s, email)
        if existing:
            return {
                "id": existing.id,
                "email": existing.email,
                "name": existing.name,
                "password": password,
            }
        u = db.User(
            email=email,
            name=name,
            password_hash=hasher.hash(passwords.normalize(password)),
            profile={"account_holder_keys": ["TEST"], "regular_income": []},
        )
        s.add(u)
        s.commit()
        s.refresh(u)
        return {
            "id": u.id,
            "email": u.email,
            "name": u.name,
            "password": password,
        }


def get_auth_client(
    email: str = "auth_tester@example.com",
    password: str = "SecurePass123!",
    name: str = "Auth Tester",
) -> Tuple[TestClient, dict[str, Any]]:
    """Create a user and return a TestClient with an authenticated 'aifp' session cookie."""
    user = create_test_user(email=email, password=password, name=name)
    client = get_test_client()

    # Ensure maintenance is temporarily disabled while authenticating
    was_active = is_maintenance_active()
    if was_active:
        reset_maintenance_mode()

    try:
        login_resp = client.post(
            "/api/login",
            data={"email": email, "password": password},
        )
        assert login_resp.status_code == 200, (
            f"Failed to log in test user: {login_resp.status_code} {login_resp.text}"
        )
        assert "aifp" in client.cookies, "Login did not set session cookie 'aifp'!"
    finally:
        if was_active:
            set_maintenance_mode(True)

    return client, user


# ── Base TestCase for unittest discovery ────────────────────────────────────

class MaintenanceTestCase(unittest.TestCase):
    """Base TestCase providing TestClient, clean teardown, and database isolation.

    Compatible with both `python -m unittest` and `pytest`.
    """

    def setUp(self) -> None:
        super().setUp()
        reset_maintenance_mode()
        self.client = get_test_client()

    def tearDown(self) -> None:
        reset_maintenance_mode()
        super().tearDown()


# ── Pytest Fixtures (if pytest is executed) ───────────────────────────────────

try:
    import pytest

    @pytest.fixture(scope="session", autouse=True)
    def ensure_test_database():
        db.init()
        yield

    @pytest.fixture(autouse=True)
    def auto_reset_maintenance():
        reset_maintenance_mode()
        yield
        reset_maintenance_mode()

    @pytest.fixture
    def client() -> Generator[TestClient, None, None]:
        with TestClient(app, raise_server_exceptions=False) as c:
            yield c

    @pytest.fixture
    def auth_user() -> Tuple[TestClient, dict[str, Any]]:
        return get_auth_client()

except ImportError:
    pass
