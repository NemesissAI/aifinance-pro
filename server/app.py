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


def current_user(request: Request):
    uid = request.session.get("uid")
    if not uid:
        raise HTTPException(401, "Sign in first.")
    with db.session() as s:
        u = s.get(db.User, uid)
        if not u:
            request.session.clear()
            raise HTTPException(401, "Sign in first.")
        return {"id": u.id, "email": u.email, "name": u.name,
                "profile": dict(u.profile or {}),
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
        return {"ok": True, "email": u.email, "name": u.name}


@app.post("/api/logout")
async def logout(request: Request):
    request.session.clear()
    return {"ok": True}


@app.get("/api/me")
async def me(user=Depends(current_user)):
    return user


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
async def index():
    if not INDEX_HTML.is_file():
        raise HTTPException(404)
    return FileResponse(INDEX_HTML)


if (ROOT / "logos").is_dir():
    app.mount("/logos", StaticFiles(directory=str(ROOT / "logos")), name="logos")
