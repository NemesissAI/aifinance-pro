# Original User Request

## 2026-09-05T20:49:15Z

Add a maintenance mode to an existing multi-user FastAPI server (`server/app.py`) so the operator can block all user-facing traffic during deployments or database migrations, while keeping health and status endpoints alive. Production quality — tests, edge cases, and clean integration with the existing codebase.

Working directory: c:\Users\Talha-PC\Desktop\aylık finansal analiz
Integrity mode: development

## Context

The server is a FastAPI app (`server/app.py`, ~280 lines) serving a personal finance dashboard. It uses:
- SQLAlchemy ORM (`server/db.py`) with SQLite locally / Postgres in production
- Starlette `SessionMiddleware` for auth (signed cookies)
- `argon2` password hashing (`server/passwords.py`)
- An onboarding module (`server/onboarding.py`)
- Static file serving of `index.html` mounted last so `/api/*` routes win

Existing modules in `server/`: `app.py`, `db.py`, `passwords.py`, `onboarding.py`.
Existing routes: `/api/register`, `/api/login`, `/api/logout`, `/api/me`, `/api/upload`, `/api/statements`, `/api/data`, `/api/state`, `/api/profile`, `/api/onboarding/cycle-day`, `/api/auth/google`.

Read `CLAUDE.md` in the project root before making any changes — it is the project's handoff document containing architecture decisions, known traps, and conventions.

## Requirements

### R1. Maintenance middleware

When maintenance mode is active, every HTTP request except a short allow-list must receive a `503 Service Unavailable` response with a JSON body and a `Retry-After` header. The allow-list must include at least a health-check path and a maintenance-status path. Static file serving (`/`, `/index.html`) must also be blocked — a half-migrated database must not be queryable through the dashboard.

### R2. Health and status endpoints

A `GET /api/health` endpoint that always returns 200 (even during maintenance) for load-balancer probes. A `GET /api/maintenance` endpoint that reports whether maintenance mode is active, an optional operator message, and the retry interval — so a frontend can show a maintenance screen without guessing.

### R3. No regressions on existing behaviour

Every existing route (`/api/login`, `/api/upload`, `/api/statements`, `/api/data`, `/api/state`, `/api/profile`, `/api/me`, `/api/logout`, `/api/register`, `/api/onboarding/cycle-day`, `/api/auth/google`) must continue to work identically when maintenance mode is **off**. The middleware must not interfere with session cookies, CORS, or the static file mount.

## Acceptance Criteria

### Maintenance mode ON
- [ ] `GET /api/health` → 200
- [ ] `GET /api/maintenance` → 200 with `{"active": true, ...}`
- [ ] `POST /api/login` → 503 with JSON body containing `"maintenance"` and a `Retry-After` header
- [ ] `POST /api/upload` → 503
- [ ] `GET /api/me` → 503
- [ ] `GET /` (static dashboard) → 503
- [ ] Any other path → 503

### Maintenance mode OFF
- [ ] `GET /api/health` → 200
- [ ] `GET /api/maintenance` → 200 with `{"active": false}`
- [ ] `POST /api/login` with valid credentials → 200 (existing behaviour)
- [ ] `GET /api/me` with valid session → 200 (existing behaviour)
- [ ] `GET /` → serves `index.html` (existing behaviour)

### Code quality
- [ ] New code lives in `server/` alongside the existing modules
- [ ] The middleware is added to `app.py` cleanly (not a monolithic rewrite)
- [ ] Tests exist and pass — at minimum covering the ON/OFF matrix above

### Verification

Run the server in both modes and hit every endpoint listed above. A test script or `pytest` suite that automates this is the expected verification mechanism.

```bash
# Maintenance OFF
.venv/Scripts/python.exe -m pytest server/tests/ -v

# Maintenance ON (the test suite should handle setting the env var internally)
```
