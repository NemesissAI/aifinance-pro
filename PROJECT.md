# Project: Maintenance Mode for Multi-User FastAPI Server

## Architecture
- **Perimeter Gate Pattern**: A pure ASGI `MaintenanceMiddleware` wrapped around the FastAPI/Starlette application as the outermost user middleware (registered after `SessionMiddleware`).
- **LIFO Middleware Stack**: Starlette applies middlewares in reverse order of addition. `MaintenanceMiddleware` wraps `SessionMiddleware`, intercepting all incoming HTTP traffic before session decryption, cookie parsing, route dispatch, or static file serving (`/`, `/index.html`).
- **Allowlist Policy**: Strict exact path matching with trailing slash normalization (`posixpath.normpath`) allowing only `/api/health` and `/api/maintenance`. Prefix matching is prohibited to prevent smuggling.
- **503 Protocol Semantics**: Non-allowlisted requests receive HTTP 503 `JSONResponse` with payload `{"error": "maintenance", "message": ..., "retry_after": int}` and RFC 9110 compliant `Retry-After: <seconds>` header.
- **Database Decoupling**: `/api/health` and `/api/maintenance` are zero-DB in-memory operations that never query `db.session()`, ensuring availability during schema migrations or DB locks.
- **Session Cookie Invariance**: 503 responses never emit `Set-Cookie`, preserving user session cookies intact across maintenance periods.
- **Dynamic Configuration & Zero-Downtime Toggling**: Dual-source configuration supporting environment variables (`MAINTENANCE_MODE`, `MAINTENANCE_MESSAGE`, `MAINTENANCE_RETRY_AFTER`) and in-memory runtime overrides (`set_maintenance_mode`, `reset_maintenance_mode`) enabling seamless dynamic toggles in testing and operations.

## Feature Inventory
Every feature from the Survey phase is assigned to a milestone below:

| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Maintenance ASGI Middleware | Pure ASGI middleware intercepting non-allowlisted HTTP requests with 503 | M2 | ORIGINAL_REQUEST §R1 |
| 2 | Allowlist Path Matching | Exact matching with trailing-slash normalization for `/api/health` and `/api/maintenance` | M2 | ORIGINAL_REQUEST §R1 |
| 3 | 503 JSON Body Contract | JSON payload containing literal string `"maintenance"`, message, and retry_after | M2 | ORIGINAL_REQUEST §R1, AC |
| 4 | Retry-After Header | Header with positive integer seconds format matching RFC 9110 | M2 | ORIGINAL_REQUEST §R1, AC |
| 5 | Liveness Endpoint `/api/health` | Returns 200 OK `{"status": "ok"}` without DB access in all modes | M3 | ORIGINAL_REQUEST §R2 |
| 6 | Status Endpoint `/api/maintenance` | Returns 200 OK with `{active: bool, ...}` in both ON and OFF modes | M3 | ORIGINAL_REQUEST §R2 |
| 7 | Static Dashboard Shielding | Blocks `/`, `/index.html`, and static assets during maintenance with 503 | M3 | ORIGINAL_REQUEST §R1, AC |
| 8 | Large Payload Shield | Immediate 503 rejection for large uploads (`/api/upload`) without buffering | M2 | Arch Explorer & Spec Miner |
| 9 | Session Cookie Non-Interference | Preserves user session cookie `aifp` without clearing during maintenance | M2 | ORIGINAL_REQUEST §R3, AC |
| 10 | Environment Configuration | Reads `MAINTENANCE_MODE`, `MAINTENANCE_MESSAGE`, `MAINTENANCE_RETRY_AFTER` | M2 | ORIGINAL_REQUEST §Verification |
| 11 | Dynamic Runtime Override | In-memory functions `set_maintenance_mode`, `reset_maintenance_mode` | M2 | Testing Explorer & Spec Miner |
| 12 | Test Infrastructure & DB Isolation | `server/tests/conftest.py` with isolated temp SQLite database fixture | M1 | Testing Explorer |
| 13 | Tier 1 Tests (Matrix Coverage) | Complete ON/OFF status matrix across all endpoints and static files | M1 | Spec Miner & AC |
| 14 | Tier 2 Tests (Boundaries & Slashes) | Trailing slash, query strings, prefix bypass attempts, HEAD probes | M1 | Spec Miner |
| 15 | Tier 3 Tests (Cross-Feature & State) | Session cookie preservation, static assets blocking, large upload abort | M1 | Spec Miner & Testing Explorer |
| 16 | Tier 4 Tests (Dynamic Scenarios) | Runtime toggle flipping without restart, custom message updates | M1 | Testing Explorer & Spec Miner |
| 17 | Zero-Regression Existing Routes | All existing routes operate identically when maintenance mode is OFF | M3 | ORIGINAL_REQUEST §R3, AC |
| 18 | Tier 5 Tests (Adversarial Hardening) | Traversal smuggling, unicode headers, rapid toggling, concurrency | M5 | Testing Explorer & Challenger |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | E2E Testing Suite (Tiers 1-4) | Build test harness, database isolation fixture, and test cases covering Tiers 1-4. Publish TEST_READY.md. | None | PLANNED |
| M2 | Maintenance Core Module | Implement `server/maintenance.py` with state, configuration, path normalization, and pure ASGI middleware. | None | PLANNED |
| M3 | Server Integration & Endpoints | Integrate `MaintenanceMiddleware` and endpoints (`/api/health`, `/api/maintenance`) into `server/app.py`. | M2 | PLANNED |
| M4 | E2E Integration Verification | Run full E2E test suite against integrated server; verify 100% pass rate across Tiers 1-4. | M1, M3 | PLANNED |
| M5 | Adversarial Coverage Hardening | White-box stress testing, bypass attempts, edge cases (Tier 5), and Forensic Audit. | M4 | PLANNED |

## Interface Contracts
### `server.maintenance` ↔ `server.app`
- `MaintenanceMiddleware(app: ASGIApp)`: Pure ASGI middleware.
- `is_maintenance_active() -> bool`: Returns current maintenance state (checks runtime override first, then env vars).
- `get_retry_after() -> int`: Returns retry delay in seconds (default 60).
- `get_operator_message() -> str | None`: Returns optional operator message.
- `get_maintenance_status_payload() -> dict[str, Any]`:
  - When OFF: `{"active": False}`
  - When ON: `{"active": True, "message": str, "retry_after": int}`
- `set_maintenance_mode(active: bool | None, message: str | None = None, retry_after: int | None = None) -> None`: Dynamic override.
- `reset_maintenance_mode() -> None`: Clears dynamic override.

### HTTP Endpoints
- `GET /api/health` → `200 OK`, JSON `{"status": "ok"}`. (Always 200, zero DB access).
- `GET /api/maintenance` → `200 OK`, JSON payload from `get_maintenance_status_payload()`.
- Non-allowlisted paths when maintenance is ON → `503 Service Unavailable`, `Retry-After: <seconds>`, JSON `{"error": "maintenance", "message": ..., "retry_after": ...}`.

## Code Layout
- `server/maintenance.py`: Core maintenance state, ASGI middleware, config parsers, helper functions.
- `server/app.py`: Register `MaintenanceMiddleware` after `SessionMiddleware`, mount `/api/health` and `/api/maintenance` routes.
- `server/tests/`:
  - `__init__.py`: Package marker.
  - `conftest.py`: Isolated temp database fixture, `TestClient` fixture, auth helper fixtures.
  - `run_tests.py`: Zero-dependency test runner supporting unittest discovery and pytest.
  - `test_maintenance_matrix.py`: Tier 1 ON/OFF matrix.
  - `test_maintenance_allowlist.py`: Tier 2 Trailing slashes, prefixes, query params, HEAD.
  - `test_maintenance_session.py`: Tier 3 Session cookie preservation & static blocking.
  - `test_maintenance_dynamic.py`: Tier 4 Runtime toggle and env var switching.
  - `test_maintenance_adversarial.py`: Tier 5 Adversarial attacks and stress cases.
