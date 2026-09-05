# E2E Test Infra: FastAPI Maintenance Mode

## Test Philosophy
- Opaque-box, requirement-driven. Tests treat the FastAPI server as a black-box HTTP service.
- Methodology: Category-Partition + Boundary Value Analysis + Cross-Feature Pairwise + Real-World Scenarios.
- Critical Data Protection Rule: `conftest.py` must point `DATABASE_URL` to an isolated temporary SQLite database before importing `server.app`, protecting `aifinance.db` from test mutations.

## Feature Inventory
| # | Feature | Source | Tier 1 | Tier 2 | Tier 3 | Tier 4 |
|---|---------|--------|:------:|:------:|:------:|:------:|
| 1 | Allowlist Health Check (`/api/health`) | ORIGINAL_REQUEST §R2 | 5 | 5 | ✓ | ✓ |
| 2 | Allowlist Maintenance Status (`/api/maintenance`) | ORIGINAL_REQUEST §R2 | 5 | 5 | ✓ | ✓ |
| 3 | 503 Rejection for User Routes (`/api/login`, `/api/upload`, etc.) | ORIGINAL_REQUEST §R1 | 7 | 5 | ✓ | ✓ |
| 4 | Static File Blocking (`/`, `/index.html`, assets) | ORIGINAL_REQUEST §R1 | 3 | 3 | ✓ | ✓ |
| 5 | Session Cookie Invariance | ORIGINAL_REQUEST §R3 | 2 | 2 | ✓ | ✓ |
| 6 | Dynamic Runtime & Env Var Toggling | ORIGINAL_REQUEST §Verification | 3 | 3 | ✓ | ✓ |

## Test Architecture
- Test runner: `pytest server/tests/ -v` or `python -m unittest discover -s server/tests -v` or `python server/tests/run_tests.py`
- Test client: `fastapi.testclient.TestClient` / `starlette.testclient.TestClient`
- Test cases location: `server/tests/`
- Directory layout:
  - `server/tests/conftest.py`
  - `server/tests/run_tests.py`
  - `server/tests/test_maintenance_matrix.py`
  - `server/tests/test_maintenance_allowlist.py`
  - `server/tests/test_maintenance_session.py`
  - `server/tests/test_maintenance_dynamic.py`
  - `server/tests/test_maintenance_adversarial.py`

## Real-World Application Scenarios (Tier 4)
| # | Scenario | Features Exercised | Complexity |
|---|----------|--------------------|------------|
| 1 | Production DB Migration: Enable maintenance mode on running instance, verify dashboard and uploads blocked while health probe remains 200, perform migration, disable maintenance, verify active session retained | F1, F2, F3, F4, F5, F6 | High |
| 2 | Load Balancer Traffic Shifting: Load balancer probes `/api/health` with GET and HEAD under heavy simulated traffic during maintenance, receiving 200 OK without container restart | F1, F6 | Medium |
| 3 | Frontend Maintenance Screen: Client frontend polls `/api/maintenance`, reads active state and retry_after, displays operator countdown | F2, F6 | Medium |
| 4 | Attack Smuggling Prevention: Malicious requests attempting prefix match (`/api/health_admin`), traversal (`/api/health/../me`), or large file upload during maintenance are rejected with 503 | F3, F4, F6 | High |

## Coverage Thresholds
- Tier 1: ≥ 25 test cases (ON/OFF matrix across all endpoints)
- Tier 2: ≥ 20 test cases (boundary value, slashes, query params, headers)
- Tier 3: ≥ 10 test cases (session cookies, large uploads, static assets)
- Tier 4: ≥ 5 test cases (dynamic state toggles, operator messages)
- Tier 5: Adversarial hardening test cases
