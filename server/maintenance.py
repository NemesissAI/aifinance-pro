"""server/maintenance.py: Maintenance mode state, ASGI middleware, and endpoints.

Provides zero-downtime perimeter defense for deployments and database migrations.
Intercepts all non-allowlisted HTTP requests with an RFC 9110 compliant 503
Service Unavailable response before session decryption, route matching, or static
file mounts occur.
"""

from __future__ import annotations

import os
import posixpath
import sys
from typing import Any

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

# Guarantee singleton instance whether imported as 'server.maintenance' or 'maintenance'
sys.modules.setdefault("server.maintenance", sys.modules[__name__])
sys.modules.setdefault("maintenance", sys.modules[__name__])

DEFAULT_ALLOWLIST: frozenset[str] = frozenset({"/api/health", "/api/maintenance"})
DEFAULT_RETRY_AFTER: int = 60
DEFAULT_MESSAGE: str = "Service is temporarily unavailable due to maintenance."

# In-memory runtime override state for dynamic zero-downtime control and tests
_runtime_override: bool | None = None
_runtime_message: str | None = None
_runtime_retry_after: int | None = None


def is_maintenance_active() -> bool:
    """Check whether maintenance mode is active.

    Evaluates dynamic in-memory override first; falls back to environment
    variables MAINTENANCE_MODE, AIFP_MAINTENANCE, or MAINTENANCE.
    """
    if _runtime_override is not None:
        return _runtime_override
    val = (
        os.environ.get("MAINTENANCE_MODE")
        or os.environ.get("AIFP_MAINTENANCE")
        or os.environ.get("MAINTENANCE")
        or ""
    ).strip().lower()
    return val in ("1", "true", "yes", "on", "enabled")


def get_retry_after() -> int:
    """Return Retry-After interval in seconds (RFC 9110 non-negative integer)."""
    if _runtime_retry_after is not None:
        try:
            val = int(_runtime_retry_after)
            return val if val > 0 else DEFAULT_RETRY_AFTER
        except (ValueError, TypeError):
            return DEFAULT_RETRY_AFTER
    raw = os.environ.get("MAINTENANCE_RETRY_AFTER") or os.environ.get("AIFP_MAINTENANCE_RETRY_AFTER")
    if raw:
        try:
            val = int(raw.strip())
            return val if val > 0 else DEFAULT_RETRY_AFTER
        except (ValueError, TypeError):
            pass
    return DEFAULT_RETRY_AFTER


def get_operator_message() -> str | None:
    """Return configured operator status message if set, else None."""
    if _runtime_message is not None:
        return _runtime_message
    msg = os.environ.get("MAINTENANCE_MESSAGE") or os.environ.get("AIFP_MAINTENANCE_MESSAGE")
    return msg.strip() if msg and msg.strip() else None


def normalize_path(path: str) -> str:
    """Normalize request path for strict exact allowlist matching.

    Strips query parameters if present, resolves relative traversal sequences
    via posixpath.normpath, collapses duplicate slashes, and removes trailing
    slashes except for the root path ('/').
    """
    if not path:
        return "/"
    # Strip query parameters if client embedded query string in raw path
    path = path.split("?")[0]
    norm = posixpath.normpath(path)
    if norm.startswith("//"):
        norm = "/" + norm.lstrip("/")
    if norm != "/" and norm.endswith("/"):
        norm = norm.rstrip("/")
    return norm or "/"


def get_allowlist() -> frozenset[str]:
    """Return canonical set of allowlisted path strings."""
    extra_raw = os.environ.get("MAINTENANCE_ALLOWLIST") or os.environ.get("AIFP_MAINTENANCE_ALLOWLIST") or ""
    if not extra_raw.strip():
        return DEFAULT_ALLOWLIST
    extra = {normalize_path(p.strip()) for p in extra_raw.split(",") if p.strip()}
    return DEFAULT_ALLOWLIST | frozenset(extra)


def set_maintenance_mode(
    active: bool | None,
    message: str | None = None,
    retry_after: int | None = None,
) -> None:
    """Set in-memory override for testing or zero-downtime operational toggling."""
    global _runtime_override, _runtime_message, _runtime_retry_after
    _runtime_override = active
    _runtime_message = message
    _runtime_retry_after = retry_after


def reset_maintenance_mode() -> None:
    """Reset in-memory override to fall back to environment variables."""
    set_maintenance_mode(None, None, None)


# Aliases for interface compatibility
set_maintenance = set_maintenance_mode
reset_maintenance = reset_maintenance_mode
is_maintenance = is_maintenance_active


def get_maintenance_status_payload() -> dict[str, Any]:
    """Return JSON payload for GET /api/maintenance.

    Returns {"active": False} when inactive.
    Returns {"active": True, "message": str, "retry_after": int} when active.
    """
    active = is_maintenance_active()
    if not active:
        return {"active": False}
    message = get_operator_message() or DEFAULT_MESSAGE
    return {
        "active": True,
        "message": message,
        "retry_after": get_retry_after(),
    }


get_status_payload = get_maintenance_status_payload


class MaintenanceMiddleware:
    """Pure ASGI middleware gating all HTTP traffic during maintenance mode.

    Wraps outermost around the ASGI stack to intercept incoming HTTP requests
    before session decryption, route matching, or static file mounts.
    Returns 503 Service Unavailable with RFC 9110 Retry-After header for
    all non-allowlisted paths.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope_type = scope.get("type")

        # Clean rejection of WebSockets during maintenance mode
        if scope_type == "websocket":
            if is_maintenance_active():
                # RFC 6455 1013: Try Again Later
                await send({"type": "websocket.close", "code": 1013})
                return
            await self.app(scope, receive, send)
            return

        # Pass through non-HTTP scopes (e.g. lifespan startup/shutdown)
        if scope_type != "http":
            await self.app(scope, receive, send)
            return

        raw_path = scope.get("path") or "/"
        norm_path = normalize_path(raw_path)
        allowlist = get_allowlist()

        # If allowlisted, normalize scope path so Starlette router matches cleanly
        # regardless of multiple trailing slashes or traversal sequences
        if norm_path in allowlist:
            scope["path"] = norm_path
            if "raw_path" in scope:
                scope["raw_path"] = norm_path.encode("ascii")
            await self.app(scope, receive, send)
            return

        # Maintenance gate evaluation for non-allowlisted requests
        if is_maintenance_active():
            retry_after = get_retry_after()
            message = get_operator_message() or DEFAULT_MESSAGE
            response = JSONResponse(
                content={
                    "error": "maintenance",
                    "message": message,
                    "detail": message,
                    "retry_after": retry_after,
                },
                status_code=503,
                headers={"Retry-After": str(retry_after)},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


__all__ = [
    "DEFAULT_ALLOWLIST",
    "DEFAULT_MESSAGE",
    "DEFAULT_RETRY_AFTER",
    "MaintenanceMiddleware",
    "get_allowlist",
    "get_maintenance_status_payload",
    "get_operator_message",
    "get_retry_after",
    "get_status_payload",
    "is_maintenance",
    "is_maintenance_active",
    "normalize_path",
    "reset_maintenance",
    "reset_maintenance_mode",
    "set_maintenance",
    "set_maintenance_mode",
]
