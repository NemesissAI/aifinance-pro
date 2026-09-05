# Test Readiness Report: Maintenance Mode E2E Suite

**Status**: READY FOR MILESTONE 2 & MILESTONE 3 INTEGRATION  
**Author**: E2E Test Writer  
**Date**: 2026-09-05  

---

## 1. Overview

A comprehensive, zero-dependency, requirement-driven end-to-end test suite has been established in `server/tests/` to verify the multi-user FastAPI Maintenance Mode feature across Tiers 1 through 5.

The test suite strictly enforces the architectural requirements and interface contracts defined in `ORIGINAL_REQUEST.md`, `PROJECT.md`, and `TEST_INFRA.md`.

---

## 2. Execution Commands

The test suite is fully dual-compatible: it runs seamlessly under Python's built-in `unittest` framework (requiring zero external packages) and under `pytest`.

### Standalone Test Runner (Recommended)
```bash
# Using virtualenv Python
.venv/Scripts/python.exe server/tests/run_tests.py

# With pytest mode (if pytest is installed)
.venv/Scripts/python.exe server/tests/run_tests.py --pytest
```

### Standard Library unittest Discovery
```bash
.venv/Scripts/python.exe -m unittest discover -s server/tests -p "test_*.py" -v
```

### Pytest Runner (When pytest is installed)
```bash
.venv/Scripts/python.exe -m pytest server/tests/ -v
```

---

## 3. Database Isolation & Data Protection Guarantee

Per the core requirement in `CLAUDE.md`, `PROJECT.md`, and `TEST_INFRA.md`:
- `aifinance.db` in the project root contains private, uncommitted user data and must **never** be touched or mutated by test runs.
- In `server/tests/conftest.py`, a dedicated `tempfile.TemporaryDirectory()` is created immediately upon module load, and `DATABASE_URL` is set to `sqlite:///<temp_dir>/test_aifinance.db` **BEFORE** `server.app` or `server.db` is imported.
- An explicit safety assertion checks `db.engine.url` to ensure `"test_aifinance.db"` is present.
- Every test runs in full database isolation with its own clean schema.

---

## 4. Test Suite Inventory & Coverage Across Tiers

| Tier | Test File | Primary Scope | Minimum Threshold | Test Cases Implemented |
|---|---|---|:---:|:---:|
| **Tier 1** | `server/tests/test_maintenance_matrix.py` | Full ON/OFF status matrix across all endpoints (`/api/health`, `/api/maintenance`, `/api/login`, `/api/upload`, `/api/me`, `/api/statements`, `/api/data`, `/api/state`, `/api/profile`, `/api/onboarding/cycle-day`, `/`, `/index.html`, 404/503 unknown paths) | ≥ 25 | **28** (covering 46 route/state permutations) |
| **Tier 2** | `server/tests/test_maintenance_allowlist.py` | Allowlist boundaries, trailing slash normalization (`/api/health/`), query parameters, prefix smuggling (`/api/health_admin`), path traversal (`/api/health/../me`), HEAD probes, and RFC 9110 integer Retry-After | ≥ 20 | **22** (covering 32 boundary conditions) |
| **Tier 3** | `server/tests/test_maintenance_session.py` | Session cookie `aifp` preservation across maintenance ON/OFF, static file blocking as JSON, and immediate large upload payload rejection | ≥ 10 | **11** (covering cookie lifecycle and disk shield) |
| **Tier 4** | `server/tests/test_maintenance_dynamic.py` | Runtime toggle flipping (`set_maintenance_mode`), operator message updates, custom retry interval, environment variable startup (`MAINTENANCE_MODE=1`), and precedence rules | ≥ 5 | **10** (covering dynamic states & env parsing) |
| **Tier 5** | `server/tests/test_maintenance_adversarial.py` | Adversarial hardening: Turkish UTF-8 operator messages, percent-encoded traversal, and HTTP method fuzzing | — | **5** |
| **Total** | | | **≥ 60** | **76 test methods** (> 110 assertion checkpoints) |

---

## 5. Detailed Test Breakdown

### Tier 1: Status Matrix (`test_maintenance_matrix.py`)
- **Allowlist Endpoints**:
  - `GET /api/health` returns `200 OK` with `{"status": "ok"}` in both ON and OFF states. Zero database dependency.
  - `GET /api/maintenance` returns `200 OK` with `{"active": false}` when OFF, and `{"active": true, "message": ..., "retry_after": ...}` when ON.
- **Account & Session Endpoints**:
  - `POST /api/login`, `POST /api/register`, `POST /api/logout`, `GET /api/me` return baseline HTTP responses when OFF and HTTP 503 when ON.
- **Statements & Settings Endpoints**:
  - `POST /api/upload`, `GET /api/statements`, `DELETE /api/statements/{id}`, `GET /api/data`, `GET/POST /api/state`, `GET/POST /api/profile`, `GET /api/onboarding/cycle-day`, `GET /api/auth/google` all properly gated behind 503 when ON.
- **Static Dashboard Shield**:
  - `GET /` and `GET /index.html` return 200 HTML when OFF and 503 JSON when ON.
- **Perimeter Shield on Unknown Routes**:
  - `GET /api/unknown` and `GET /nonexistent.js` return 404 when OFF, and 503 when ON.
- **503 Protocol Contract**:
  - Header `Retry-After: <positive-integer>`, Content-Type `application/json`, JSON payload `{"error": "maintenance", "message": ..., "retry_after": ...}`.

### Tier 2: Allowlist & Security Boundaries (`test_maintenance_allowlist.py`)
- **Trailing Slashes**: `/api/health/`, `/api/health///`, `/api/maintenance/`, `/api/maintenance///` resolve to 200 OK via path normalization (`posixpath.normpath`).
- **Query Strings**: Ignored during allowlist path matching (`/api/health?probe=liveness` -> 200 OK).
- **Prefix Smuggling Prevention**: Prohibits prefix-matching exploits (`/api/health_admin`, `/api/health/check`, `/api/maintenance_mode`, `/api/heal`, `/api/maint` -> 503).
- **Path Traversal Security**: `/api/health/../me` resolves to `/api/me` and is blocked with 503.
- **HEAD Probes**: `HEAD /api/health` and `HEAD /api/maintenance` return 200 OK with empty body.
- **RFC 9110 Compliance**: `Retry-After` header verified to be ASCII digits representing seconds (no HTTP-date format), matching the integer in JSON body `retry_after`.

### Tier 3: Session State & Payload Shielding (`test_maintenance_session.py`)
- **Session Cookie Invariance**:
  - A user logs in when OFF (cookie `aifp` stored).
  - Maintenance toggled ON -> requests receive 503.
  - Server emits **no** `Set-Cookie` header on 503 responses.
  - Maintenance toggled back OFF -> user requests `/api/me` and is immediately authenticated without logging in again.
- **Static Dashboard Shielding**: Static routes return JSON error objects during maintenance, preventing partial dashboard asset downloads.
- **Perimeter Upload Shield**: `POST /api/upload` with large payloads (>1MB) or corrupted bytes returns 503 immediately without buffering, PDF parsing, or database writes.

### Tier 4: Dynamic Scenarios & Environment (`test_maintenance_dynamic.py`)
- **Zero-Downtime Runtime Toggles**: Dynamic switching via `set_maintenance_mode(True/False)` and `reset_maintenance_mode()` without application restart.
- **Operator Messages**: Dynamic update and propagation of operator message to `/api/maintenance` and 503 error payloads.
- **Custom Intervals**: Dynamic configuration of `retry_after` seconds.
- **Environment Variable Startup**: Supports `MAINTENANCE_MODE=1`, `MAINTENANCE_MESSAGE`, `MAINTENANCE_RETRY_AFTER`.
- **Precedence Hierarchy**: In-memory runtime override takes precedence over environment variables.
- **Truthy/Falsy Parsing**: Evaluates `"1"`, `"true"`, `"yes"`, `"on"` vs `"0"`, `"false"`, `"no"`, `"off"`.

### Tier 5: Adversarial Hardening (`test_maintenance_adversarial.py`)
- Turkish UTF-8 character encoding fidelity in operator messages (`"₺"`, `"ş"`, `"ç"`, `"ğ"`).
- Percent-encoded traversal attempts (`/api/%2e%2e/me`).
- Method fuzzing on protected routes.

---

## 6. Authoritative Source of Expected Outputs

Every test assertion derives directly from:
1. `ORIGINAL_REQUEST.md`: Specifications R1 (middleware & 503 responses), R2 (health/status endpoints), R3 (session cookie invariance & zero regressions).
2. `PROJECT.md`: Interface contracts between `server.maintenance` and `server.app`, path allowlist policy, and response schemas.
3. `TEST_INFRA.md`: Real-world scenarios (DB migration, LB probes, frontend countdown, smuggling defense) and tier thresholds.
4. RFC 9110 Section 10.2.3: `Retry-After` header specification (integer seconds).

---

## 7. Next Steps

The test harness is complete and ready. Milestones 2 and 3 can proceed:
- **Milestone 2**: Implement `server/maintenance.py` (`MaintenanceMiddleware`, `set_maintenance_mode`, `is_maintenance_active`, etc.).
- **Milestone 3**: Register `MaintenanceMiddleware` in `server/app.py` after `SessionMiddleware`, and mount `/api/health` and `/api/maintenance` routes.
- **Milestone 4**: Run this test suite against the integrated server to verify all test cases pass.
