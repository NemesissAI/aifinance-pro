"""AIFinance — multi-user API.

Every route below is scoped to the signed-in user. There is no route that can
read another person's statements, and no shared folder for two people to
collide in: the single-user design this grew out of kept one `data/` directory
and one `user-state.json`, which for two users means each silently overwrites
the other.

Run locally:
    .venv/Scripts/python.exe -m uvicorn server.app:app --reload --port 8000
"""

from __future__ import annotations

import hashlib
import os
import sys
from datetime import timezone
from pathlib import Path

from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from authlib.integrations.starlette_client import OAuth, OAuthError
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from starlette.middleware.sessions import SessionMiddleware

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "server"))

import db  # noqa: E402
try:
    import server.maintenance as maintenance  # noqa: E402
except ImportError:
    import maintenance  # noqa: E402
import onboarding  # noqa: E402
import parse_api  # noqa: E402
import passwords  # noqa: E402

MAX_PDF_BYTES = 25 * 1024 * 1024
hasher = PasswordHasher()

app = FastAPI(title="AIFinance")

# Signed, http-only session cookie. SESSION_SECRET must be set in production —
# a random default would log everyone out on every restart and, worse, would
# look like it had been configured.
SECRET = os.environ.get("SESSION_SECRET")
if not SECRET:
    if os.environ.get("AIFP_ENV") == "production":
        raise RuntimeError("SESSION_SECRET must be set in production.")
    SECRET = "dev-only-not-a-secret"

app.add_middleware(
    SessionMiddleware, secret_key=SECRET, session_cookie="aifp",
    https_only=os.environ.get("AIFP_ENV") == "production",
    same_site="lax", max_age=60 * 60 * 24 * 30,
)
app.add_middleware(maintenance.MaintenanceMiddleware)

# Google OAuth. authlib reads/writes request.session for the state and nonce
# it uses to stop a forged callback, which is why SessionMiddleware has to be
# registered above this. Registration succeeds even with no client id/secret —
# it only fails the moment a route tries to *use* it — so a missing
# configuration is caught explicitly in each route below, with a message that
# says what to set, rather than surfacing as an opaque 500 partway through.
oauth = OAuth()
oauth.register(
    name="google",
    client_id=os.environ.get("GOOGLE_CLIENT_ID"),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"},
)


def _google_configured() -> bool:
    return bool(os.environ.get("GOOGLE_CLIENT_ID") and os.environ.get("GOOGLE_CLIENT_SECRET"))


# Who may open the creator dashboard. Set AIFP_ADMIN_EMAILS to a
# comma-separated list; anyone else signing in is a plain user.
#
# It has to be configuration rather than a flag someone sets once by hand,
# because the flag lives in the database and the database is recreated on
# every fresh deploy — an admin granted by hand disappears with it, and the
# dashboard then 403s its own owner with no way back in that does not involve
# a SQL client. Reading it from the environment means the answer is restored
# by the same deploy that wipes it.
ADMIN_EMAILS = {e.strip().lower()
                for e in os.environ.get("AIFP_ADMIN_EMAILS", "").split(",")
                if e.strip()}


def _is_admin(email: str | None) -> bool:
    """The environment is the only thing that grants this — never a database row.

    There is an is_admin column, and it is deliberately not consulted here.
    Three leftover test accounts were sitting in it set to 1, from a session
    where somebody flipped the flag by hand to try the page out; a column is
    granted once and stays granted, so those accounts would have kept the
    creator dashboard forever and nothing in the app would ever have said so.
    Reading the list every time means revoking an address is a config change,
    and a stale row cannot grant anything.
    """
    return bool(email) and email.strip().lower() in ADMIN_EMAILS


def current_user(request: Request):
    uid = request.session.get("uid")
    if not uid:
        raise HTTPException(401, "Sign in first.")
    with db.session() as s:
        u = s.get(db.User, uid)
        if not u:
            request.session.clear()
            raise HTTPException(401, "Sign in first.")
        # The session-version check: a cookie signed before the account's
        # last sign-out carries the *old* number here and is rejected, even
        # though the signature itself is still cryptographically valid.
        # Without this, replaying a captured pre-logout cookie kept working —
        # verified against a running server — for the full 30-day cookie
        # lifetime, on an account its owner believed was signed out of.
        if request.session.get("sv") != u.session_version:
            request.session.clear()
            raise HTTPException(401, "Sign in first.")
        return {"id": u.id, "email": u.email, "name": u.name,
                "profile": dict(u.profile or {}),
                # So the page can show the creator dashboard link to the one
                # account that can actually open it, instead of everyone
                # discovering a door that 403s.
                "isAdmin": _is_admin(u.email),
                # A Google-only account has no password yet. The product
                # requires one — see the sign-in route below for why — so the
                # frontend gates everything but the set-password screen on this.
                "needsPassword": not bool(u.password_hash)}


# ── accounts ────────────────────────────────────────────────────────────────

@app.post("/api/register")
async def register(email: str = Form(...), password: str = Form(...),
                   name: str = Form("")):
    email = email.strip().lower()
    if "@" not in email or len(email) < 5:
        raise HTTPException(400, "That does not look like an email address.")
    try:
        passwords.check(password, email=email, name=name)
    except passwords.WeakPassword as e:
        raise HTTPException(400, str(e))
    if await passwords.is_breached(password):
        raise HTTPException(400, "That password appears in a known breach. Pick another.")

    with db.session() as s:
        if db.user_by_email(s, email):
            raise HTTPException(409, "That address already has an account.")
        u = db.User(email=email, name=name.strip()[:120],
                    password_hash=hasher.hash(passwords.normalize(password)),
                    profile={})
        s.add(u)
        s.commit()
        return {"ok": True, "email": u.email}


@app.post("/api/login")
async def login(request: Request, email: str = Form(...), password: str = Form(...)):
    with db.session() as s:
        u = db.user_by_email(s, email)
        # The same answer either way: "no such account" tells an attacker which
        # addresses are registered.
        if not u or not u.password_hash:
            raise HTTPException(401, "Email or password is wrong.")
        try:
            hasher.verify(u.password_hash, passwords.normalize(password))
        except VerifyMismatchError:
            raise HTTPException(401, "Email or password is wrong.")
        if hasher.check_needs_rehash(u.password_hash):
            u.password_hash = hasher.hash(passwords.normalize(password))
        s.commit()
        request.session["uid"] = u.id
        request.session["sv"] = u.session_version
        return {"ok": True, "email": u.email, "name": u.name}


@app.post("/api/logout")
async def logout(request: Request):
    # Bumping session_version is what actually revokes access — clearing this
    # browser's cookie alone left every other copy of it (an earlier device,
    # anything that had captured the value) still valid. This invalidates all
    # of them at once, which is the right default for a finance app even
    # though it means "sign out everywhere" rather than just this device.
    uid = request.session.get("uid")
    if uid:
        with db.session() as s:
            u = s.get(db.User, uid)
            if u:
                u.session_version = (u.session_version or 0) + 1
                s.commit()
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
async def me(user=Depends(current_user)):
    return user


@app.get("/api/auth/google")
@app.get("/api/auth/google/login")
async def google_login(request: Request):
    """A navigation, not an API call — the button should point its href here
    directly rather than fetch() it, since the response is a 302 to Google."""
    if not _google_configured():
        return JSONResponse(
            {"error": "not_configured",
             "detail": "Google sign-in needs GOOGLE_CLIENT_ID and "
                       "GOOGLE_CLIENT_SECRET set on the server. Email and "
                       "password work now."},
            status_code=503)
    redirect_uri = str(request.url_for("google_callback"))
    return await oauth.google.authorize_redirect(request, redirect_uri)


@app.get("/api/auth/google/callback", name="google_callback")
async def google_callback(request: Request):
    if not _google_configured():
        raise HTTPException(503, "Google sign-in is not configured.")
    try:
        token = await oauth.google.authorize_access_token(request)
    except OAuthError:
        # Cancelled consent, an expired state, or a forged callback all land
        # here — none of them are a server fault, so this is a redirect with
        # an error flag the page can show, not a 500.
        return RedirectResponse(url="/?auth_error=google_failed")

    claims = token.get("userinfo") or {}
    sub = claims.get("sub")
    email = (claims.get("email") or "").strip().lower()
    name = (claims.get("name") or "").strip()[:120]
    if not sub or not email:
        return RedirectResponse(url="/?auth_error=google_no_email")

    with db.session() as s:
        u = s.scalar(select(db.User).where(db.User.google_sub == sub))
        if u is None:
            # An email/password account with this address exists already —
            # link the Google identity to it rather than making a duplicate
            # account with the same email, which the unique constraint would
            # reject anyway and which would silently split one person's data
            # across two accounts.
            u = db.user_by_email(s, email)
        if u is None:
            u = db.User(email=email, name=name, google_sub=sub,
                       password_hash=None, profile={})
            s.add(u)
        elif not u.google_sub:
            u.google_sub = sub
        s.commit()
        request.session["uid"] = u.id
        request.session["sv"] = u.session_version

    # The frontend checks /api/me.needsPassword on landing and shows the
    # set-password screen itself — nothing more to signal here.
    return RedirectResponse(url="/")


@app.post("/api/auth/set-password")
async def set_password(request: Request, user=Depends(current_user)):
    """How a Google-only account gets the password this product requires.

    Same policy as /api/register, deliberately: a password chosen here is not
    a lesser one just because it comes second.
    """
    body = await request.json()
    password = str(body.get("password", ""))
    with db.session() as s:
        u = s.get(db.User, user["id"])
        try:
            passwords.check(password, email=u.email, name=u.name)
        except passwords.WeakPassword as e:
            raise HTTPException(400, str(e))
        if await passwords.is_breached(password):
            raise HTTPException(400, "That password appears in a known breach. Pick another.")
        u.password_hash = hasher.hash(passwords.normalize(password))
        # A changed password should mean whoever had the old one is out —
        # every other device's cookie stops here. This session carries the
        # new version so the person doing the changing is not signed out too.
        u.session_version = (u.session_version or 0) + 1
        request.session["sv"] = u.session_version
        s.commit()
    return {"ok": True}


# ── statements ──────────────────────────────────────────────────────────────

@app.post("/api/upload")
async def upload(file: UploadFile = File(...), password: str = Form(""),
                 user=Depends(current_user)):
    data = await file.read()
    if not data:
        raise HTTPException(400, "Empty file.")
    if len(data) > MAX_PDF_BYTES:
        raise HTTPException(413, "That file is larger than 25 MB.")

    digest = hashlib.sha256(data).hexdigest()
    try:
        payload = parse_api.parse_bytes(
            data, password=password or None,
            profile=user["profile"], filename=file.filename or "upload.pdf")
    except parse_api.ParseError as e:
        # The PDF goes out of scope here: nothing was written anywhere.
        return JSONResponse({"error": e.code, "detail": str(e)}, status_code=422)
    except Exception:
        # pikepdf/pdfplumber raise their own exception types for a corrupt or
        # non-PDF file — tested by uploading a PNG renamed to .pdf, which threw
        # unhandled and came back as a bare 500 with a stack trace and no
        # explanation. A user's mis-selected file is routine input, not a
        # server fault, and must never look like one.
        return JSONResponse(
            {"error": "unreadable", "detail": "That file could not be read as a PDF."},
            status_code=422)

    sid = payload["statementId"]
    with db.session() as s:
        existing = next((x for x in db.user_statements(s, user["id"])
                         if x.statement_id == sid), None)
        if existing:
            if existing.sha256 == digest:
                return {"status": "duplicate", "statementId": sid,
                        "detail": "You have already imported this statement."}
            existing.payload, existing.sha256 = payload, digest
            existing.bank = payload["bank"]
            existing.period_end = payload["periodEnd"]
            s.commit()
            return {"status": "replaced", "statementId": sid,
                    "warnings": payload.get("warnings", [])}
        s.add(db.Statement(user_id=user["id"], statement_id=sid,
                           bank=payload["bank"], period_end=payload["periodEnd"],
                           sha256=digest, payload=payload))
        s.commit()
    return {"status": "imported", "statementId": sid,
            "count": len(payload["transactions"]),
            "warnings": payload.get("warnings", [])}


@app.get("/api/statements")
async def statements(user=Depends(current_user)):
    with db.session() as s:
        rows = db.user_statements(s, user["id"])
        return {"statements": [
            {"statementId": r.statement_id, "bank": r.bank,
             "periodEnd": r.period_end,
             "month": r.payload.get("statementMonth", ""),
             "count": len(r.payload.get("transactions", [])),
             "totalExpense": r.payload.get("summary", {}).get("totalExpense", 0),
             "totalIncome": r.payload.get("summary", {}).get("totalIncome", 0)}
            for r in rows]}


@app.get("/api/data")
async def data(user=Depends(current_user)):
    """Every statement this user owns, in the shape the dashboard already reads."""
    with db.session() as s:
        return {"months": {r.statement_id: r.payload
                           for r in db.user_statements(s, user["id"])}}


@app.delete("/api/statements/{statement_id}")
async def delete_statement(statement_id: str, user=Depends(current_user)):
    with db.session() as s:
        row = next((x for x in db.user_statements(s, user["id"])
                    if x.statement_id == statement_id), None)
        if not row:
            raise HTTPException(404, "No such statement.")
        s.delete(row)
        s.commit()
    return {"ok": True}


# ── the user's own decisions and rules ──────────────────────────────────────

@app.get("/api/state")
async def get_state(user=Depends(current_user)):
    with db.session() as s:
        return db.get_state(s, user["id"])


@app.post("/api/state")
async def post_state(request: Request, user=Depends(current_user)):
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "State must be a JSON object.")
    with db.session() as s:
        db.put_state(s, user["id"], body)
    return {"ok": True}


@app.get("/api/profile")
async def get_profile(user=Depends(current_user)):
    return user["profile"]


@app.post("/api/profile")
async def post_profile(request: Request, user=Depends(current_user)):
    """The rules that used to be hard-coded for one person."""
    body = await request.json()
    if not isinstance(body, dict):
        raise HTTPException(400, "Profile must be a JSON object.")
    allowed = {"account_holder_keys", "regular_income", "passthrough",
               "investment", "trust", "internal"}
    with db.session() as s:
        u = s.get(db.User, user["id"])
        u.profile = {k: v for k, v in body.items() if k in allowed}
        s.commit()
        return dict(u.profile)


@app.get("/api/onboarding/cycle-day")
async def cycle_day(user=Depends(current_user)):
    """The one setting a new user cannot answer on day one, measured for them.

    Needs a couple of months of statements before it can say anything, and says
    so rather than guessing — a recommendation from one statement would be a
    coin flip wearing a number.
    """
    with db.session() as s:
        payloads = [r.payload for r in db.user_statements(s, user["id"])]
    return onboarding.analyse(payloads)


# ── operations & health ─────────────────────────────────────────────────────

@app.get("/api/health")
@app.get("/api/health/")
@app.head("/api/health")
@app.head("/api/health/")
async def health():
    return {"status": "ok"}


@app.get("/api/maintenance")
@app.get("/api/maintenance/")
@app.head("/api/maintenance")
@app.head("/api/maintenance/")
async def maintenance_status():
    return maintenance.get_maintenance_status_payload()


@app.get("/api/onboarding/status")
async def onboarding_status(user=Depends(current_user)):
    """Where this user is, so the UI never has to infer it from three calls."""
    with db.session() as s:
        payloads = [r.payload for r in db.user_statements(s, user["id"])]
        state = db.get_state(s, user["id"])
    return onboarding.status(payloads, user["profile"], state)


@app.post("/api/onboarding/name")
async def onboarding_name(request: Request, user=Depends(current_user)):
    """Claim the name the banks print for this person.

    Normalised here rather than in the browser because it has to land on
    exactly what the parser matches against — see onboarding.py. A mismatch
    raises nothing and silently turns the user's own transfers into income.
    """
    body = await request.json()
    try:
        key = onboarding.normalise_holder_name(str(body.get("name", "")))
    except onboarding.NameRejected as e:
        raise HTTPException(400, str(e))

    with db.session() as s:
        payloads = [r.payload for r in db.user_statements(s, user["id"])]
        preview = onboarding.preview_name(payloads, key)
        u = s.get(db.User, user["id"])
        profile = dict(u.profile or {})
        keys = [k for k in profile.get("account_holder_keys", []) if k != key]
        profile["account_holder_keys"] = [key] + keys
        u.profile = profile
        s.commit()
    return {"ok": True, "key": key, **preview}


@app.post("/api/onboarding/cycle-day")
async def onboarding_set_cycle_day(request: Request, user=Depends(current_user)):
    body = await request.json()
    try:
        day = int(body.get("day"))
    except (TypeError, ValueError):
        raise HTTPException(400, "Give a day of the month as a number.")
    # 29-31 do not exist in every month, so the boundary would move about.
    if not 1 <= day <= 28:
        raise HTTPException(400, "Pick a day between 1 and 28 — later days do "
                                 "not exist in every month.")
    with db.session() as s:
        state = db.get_state(s, user["id"])
        state["aifp.cycleStart"] = day
        db.put_state(s, user["id"], state)
    return {"ok": True, "cycleDay": day}


@app.post("/api/onboarding/tour-done")
async def onboarding_tour_done(user=Depends(current_user)):
    with db.session() as s:
        state = db.get_state(s, user["id"])
        state["aifp.tourDone"] = True
        db.put_state(s, user["id"], state)
    return {"ok": True}


# ── the demo account, for presenting the first-run experience ──────────────
#
# Showing someone "this is what a new user sees" needs an account that *is*
# new every time — no name, no statements, no tour flag — and the owner's own
# account is the one thing that must never be wiped to get there. So there
# is a fixed demo login whose state is reset by an admin-only call, and the
# presenter signs in as it. The password comes from the environment: a
# fixed one in the source would ship in a public repo.

DEMO_EMAIL = "demo@aifinance.local"


@app.post("/api/admin/demo/reset")
async def admin_demo_reset(user=Depends(current_user)):
    if not _is_admin(user["email"]):
        raise HTTPException(403, "Forbidden")
    password = os.environ.get("AIFP_DEMO_PASSWORD", "")
    if not password:
        raise HTTPException(503, "Set AIFP_DEMO_PASSWORD on the server to enable the demo account.")
    try:
        passwords.check(password, email=DEMO_EMAIL, name="Demo")
    except passwords.WeakPassword as e:
        raise HTTPException(503, f"AIFP_DEMO_PASSWORD is too weak: {e}")

    with db.session() as s:
        u = db.user_by_email(s, DEMO_EMAIL)
        if u is None:
            u = db.User(email=DEMO_EMAIL, profile={})
            s.add(u)
            s.flush()
        u.name = "Demo Account"
        u.password_hash = hasher.hash(passwords.normalize(password))
        u.profile = {}                       # no holder name → onboarding step 1
        u.google_sub = None
        u.total_time_seconds = 0
        u.features_used = {}
        u.session_count = 0
        u.last_seen_at = None
        # Any browser still signed in as the demo from the last presentation
        # is signed out by this, so two sessions never share the reset.
        u.session_version = (u.session_version or 0) + 1
        s.query(db.Statement).filter_by(user_id=u.id).delete()
        state = s.get(db.UserState, u.id)
        if state is not None:
            s.delete(state)
        s.commit()
    return {"ok": True, "email": DEMO_EMAIL}


@app.on_event("startup")
def _startup() -> None:
    db.init()


# ── static assets ────────────────────────────────────────────────────────────
#
# This used to be `app.mount("/", StaticFiles(directory=str(ROOT)))` — serving
# the *entire project root* with no authentication. Checked directly against a
# running instance: `/profile.json`, `/user-state.json`, `/aifinance.db` (every
# user's password hash and every statement in the database) and `/server/app.py`
# all came back 200, in full. The multi-user rebuild exists specifically so
# personal data lives behind a login; that one line undid it for anyone who
# could type a URL. Two things only are served now, both harmless by design:
# the dashboard shell and the bank logo images.
INDEX_HTML = ROOT / "index.html"


@app.get("/")
@app.get("/index.html")
async def index():
    if not INDEX_HTML.is_file():
        raise HTTPException(404)
    return FileResponse(INDEX_HTML)
if (ROOT / "logos").is_dir():
    app.mount("/logos", StaticFiles(directory=str(ROOT / "logos")), name="logos")

# ── telemetry, and the creator dashboard it feeds ───────────────────────────

from pydantic import BaseModel, Field


class TelemetryData(BaseModel):
    # Capped, because this arrives from the browser and nothing else bounds
    # it. A bug in the page — or anyone with the dev tools open — could
    # otherwise post a decade of "usage" in one request and the dashboard
    # would report it with a straight face. One hour is well above the 30s
    # the page actually sends.
    active_seconds: int = Field(0, ge=0, le=3600)
    features: dict[str, int] = Field(default_factory=dict)
    new_session: bool = False


# Names the page is allowed to report. Without this the feature column is
# whatever a client cares to invent, and one crafted request turns the
# dashboard into a list of strings someone else chose.
KNOWN_FEATURES = {
    "Dashboard", "Transactions", "Analytics", "AI Coach", "Settings",
    "Upload", "Categorise", "Match transfers", "Budgets", "Subscriptions",
    "What-If Simulator", "Export", "Unknown queue", "Filters", "Search",
}


def _feature_counts(raw) -> dict[str, int]:
    """features_used as {name: count}, whatever shape it is on disk.

    It shipped as a list of names first, so accounts created before this exist
    with one. Those are read as "seen once" rather than dropped.
    """
    if isinstance(raw, list):
        return {str(k): 1 for k in raw}
    if isinstance(raw, dict):
        out = {}
        for k, v in raw.items():
            try:
                out[str(k)] = int(v)
            except (TypeError, ValueError):
                out[str(k)] = 1
        return out
    return {}


@app.post("/api/telemetry")
async def api_telemetry(data: TelemetryData, user=Depends(current_user)):
    with db.session() as s:
        u = s.get(db.User, user["id"])
        if not u:
            return {"ok": False}
        u.total_time_seconds = (u.total_time_seconds or 0) + data.active_seconds
        counts = _feature_counts(u.features_used)
        for name, n in data.features.items():
            if name in KNOWN_FEATURES:
                counts[name] = counts.get(name, 0) + max(0, min(int(n), 1000))
        u.features_used = counts
        u.last_seen_at = db.now()
        if data.new_session:
            u.session_count = (u.session_count or 0) + 1
        s.commit()
        return {"ok": True}


@app.get("/api/admin/dashboard")
async def admin_dashboard(user=Depends(current_user)):
    with db.session() as s:
        me_row = s.get(db.User, user["id"])
        if not me_row or not _is_admin(me_row.email):
            raise HTTPException(403, "Forbidden")

        from sqlalchemy import func
        # One grouped query rather than a count per user — the N+1 is
        # harmless at this size but the page refreshes on a button.
        counts = dict(s.query(db.Statement.user_id,
                              func.count(db.Statement.id))
                       .group_by(db.Statement.user_id).all())
        latest = dict(s.query(db.Statement.user_id,
                              func.max(db.Statement.created_at))
                       .group_by(db.Statement.user_id).all())

        def iso(v):
            # SQLite drops the tzinfo a DateTime(timezone=True) column was
            # given, so these come back naive even though db.now() stored UTC.
            # isoformat() then emits no offset, and Date.parse() in the browser
            # reads a bare timestamp as *local* time — an account created
            # seconds ago rendered as "3h ago" here, off by exactly this
            # machine's offset. Stamping the UTC these values actually are is
            # what makes "last seen" mean anything.
            if v is None:
                return None
            if v.tzinfo is None:
                v = v.replace(tzinfo=timezone.utc)
            return v.isoformat()

        return [{
            "id": u.id,
            "email": u.email,
            "name": u.name or "",
            "signedUpAt": iso(u.created_at),
            "lastSeenAt": iso(u.last_seen_at),
            "activeSeconds": u.total_time_seconds or 0,
            "sessionCount": u.session_count or 0,
            "statementCount": counts.get(u.id, 0),
            "lastUploadAt": iso(latest.get(u.id)),
            "features": _feature_counts(u.features_used),
            # Whether they ever got past the one step that cannot be skipped:
            # an account with no holder name parses every own transfer as
            # someone else's money, so "signed up but never finished setup"
            # is the single most useful thing to see in this table.
            "hasProfile": bool((u.profile or {}).get("account_holder_keys")),
            "isAdmin": _is_admin(u.email),
        } for u in s.query(db.User).order_by(db.User.created_at.desc()).all()]

@app.get("/admin")
async def serve_admin(user=Depends(current_user)):
    # The data underneath (/api/admin/dashboard) was already gated on
    # is_admin; this shell page itself was not, so an unauthenticated
    # request could load it and see the empty dashboard chrome before its
    # own fetch to that endpoint 403'd. No secrets live in the page, but
    # there's no reason to hand out "an admin panel exists at /admin, here
    # is its layout" to a signed-out visitor when gating it costs one line.
    with db.session() as s:
        u = s.get(db.User, user["id"])
        if not u or not _is_admin(u.email):
            raise HTTPException(403, "Forbidden")
    # Not the bare filename: that resolves against the working directory the
    # server happened to be started from, which is the project root here and
    # something else entirely under a process manager.
    return FileResponse(ROOT / "admin.html")
