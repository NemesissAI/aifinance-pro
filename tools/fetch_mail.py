"""
Fetch bank statement PDFs out of Gmail and hand them to the parser.

    .venv\\Scripts\\python.exe tools\\fetch_mail.py            # import anything new
    .venv\\Scripts\\python.exe tools\\fetch_mail.py --check     # say what it would do
    .venv\\Scripts\\python.exe tools\\fetch_mail.py --months 6  # look further back

Why this runs here and not in the chat
--------------------------------------
Claude's Gmail connector returns an attachment's *name*, not its bytes — there
is no download call in it. The button in the dashboard also has to work when
nobody is talking to Claude. So the credential has to live on this machine, and
that means a Google Cloud OAuth client. Setup is written out in --check output
and in tools/README.md; it is one manual step, once.

Nothing is uploaded. The only outbound call is to Google's own API to read the
mailbox, and the PDFs land in statements\\ exactly as if they had been dropped
on the upload panel.

The same statement must never be imported twice
-----------------------------------------------
Three separate guards, because each catches a case the others miss:

  1. the Gmail message id — this mail was already processed
  2. the SHA-256 of the PDF   — same file, forwarded or re-sent under a new id
  3. the statementId the parser derives ("ziraat-bankasi-bankkart-2026-08")
     — the same period from the same account, whatever the file was called

mail-log.json at the project root is the ledger. It sits outside data\\ on
purpose: write_index() globs data\\*.json and would list it as a statement.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STATEMENTS = ROOT / "statements"
DATA = ROOT / "data"
LEDGER = ROOT / "mail-log.json"
SECRETS = ROOT / ".gmail"
CLIENT_SECRET = SECRETS / "credentials.json"
TOKEN = SECRETS / "token.json"

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

# Who sends statements, and what the mail looks like. Senders are matched on the
# domain so a bank changing its "no-reply" prefix does not silently stop the
# import. Add a bank by adding a line.
# What actually arrives by mail, checked against the real mailbox:
#   Ziraat  — "<Ay> Ayı E-Ekstre Servisi", the Bankkart statement, a text PDF
#   Ziraat  — "Hesap_Hareketleri_<ddmmyyyy>", the vadesiz account, always a SCAN
#             with no text layer, so it is reported and typed in by hand
#   Garanti — "Geçmiş Dönem Kredi Kartı Ekstresi" from dekont@, the Bonus card
# Kuveyt Türk sends nothing, and Garanti does not mail the account statement
# (Belge-*.pdf) either — both of those stay manual downloads. kuveytturk is
# listed anyway so the day they start, this picks it up.
SENDERS = [
    "ziraatbank.com.tr",
    "garantibbva.com.tr",
    "kuveytturk.com.tr",
]
SUBJECT_HINTS = ["ekstre", "hesap özeti", "hesap ekstresi", "e-ekstre"]

SETUP = """
Gmail is not connected on this machine yet.

Claude's own Gmail connection cannot do this: it hands back an attachment's
name but never its bytes, and the dashboard button has to work when Claude is
not in the room. So this needs a credential of its own. Once, about five
minutes:

  1. console.cloud.google.com -> create a project (any name)
  2. APIs & Services -> Library -> enable "Gmail API"
  3. APIs & Services -> OAuth consent screen -> External -> add your own
     address (3586957b@gmail.com) under "Test users"
  4. Credentials -> Create credentials -> OAuth client ID -> Desktop app
  5. Download the JSON and save it as:
       .gmail\\credentials.json
  6. Run this once and approve in the browser that opens:
       .venv\\Scripts\\python.exe tools\\fetch_mail.py --check

The scope asked for is gmail.readonly — this can read mail and nothing else.
It cannot send, delete or change anything.
"""


def load_ledger() -> dict:
    if LEDGER.exists():
        try:
            return json.loads(LEDGER.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"seen": []}


def save_ledger(ledger: dict) -> None:
    LEDGER.write_text(json.dumps(ledger, ensure_ascii=False, indent=2), encoding="utf-8")


def known_statement_ids() -> dict[str, str]:
    """statementId -> the file already holding it."""
    out = {}
    for p in DATA.glob("*.json"):
        if p.stem == "index":
            continue
        try:
            j = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        if j.get("statementId"):
            out[j["statementId"]] = p.name
    return out


def service():
    """Authorised Gmail client, or None with the reason printed."""
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        print("The Google client libraries are not installed. Run:\n"
              "  .venv\\Scripts\\python.exe -m pip install "
              "google-api-python-client google-auth-oauthlib", file=sys.stderr)
        return None

    creds = None
    if TOKEN.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
        except Exception:
            creds = None

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception:
            creds = None            # refresh revoked; fall through to consent

    if not creds or not creds.valid:
        if not CLIENT_SECRET.exists():
            print(SETUP, file=sys.stderr)
            return None
        # Opens a browser. Only possible when a person is at the machine, which
        # is why the button reports this rather than hanging on the server.
        flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
        creds = flow.run_local_server(port=0)
        SECRETS.mkdir(exist_ok=True)
        TOKEN.write_text(creds.to_json(), encoding="utf-8")

    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def query(months: int) -> str:
    after = (datetime.now(timezone.utc) - timedelta(days=31 * months)).strftime("%Y/%m/%d")
    senders = " OR ".join(f"from:{s}" for s in SENDERS)
    subjects = " OR ".join(f'subject:"{h}"' for h in SUBJECT_HINTS)
    return f"has:attachment filename:pdf after:{after} (({senders}) OR ({subjects}))"


def safe_name(name: str) -> str:
    """Gmail hands back whatever the bank called it; keep it a plain filename."""
    name = re.sub(r"[^A-Za-z0-9._-]", "_", Path(name).name).lstrip(".")
    return (name or "statement.pdf")[:120]


def parse(pdf: Path) -> tuple[bool, str]:
    out = subprocess.run(
        [str(ROOT / ".venv/Scripts/python.exe"), str(ROOT / "tools/parse_statement_local.py"),
         str(pdf)],
        capture_output=True, text=True, encoding="utf-8", errors="replace", cwd=str(ROOT))
    return out.returncode == 0, (out.stdout or "") + (out.stderr or "")


def main() -> None:
    ap = argparse.ArgumentParser(description="Import bank statements from Gmail.")
    ap.add_argument("--check", action="store_true",
                    help="list what would be imported, download nothing")
    ap.add_argument("--months", type=int, default=3,
                    help="how far back to look (default 3)")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    args = ap.parse_args()

    ledger = load_ledger()
    seen_msgs = {e["messageId"] for e in ledger["seen"]}
    seen_hashes = {e["sha256"] for e in ledger["seen"] if e.get("sha256")}
    have = known_statement_ids()

    report = {"found": [], "imported": [], "skipped": [], "failed": [], "error": None}

    gmail = service()
    if gmail is None:
        report["error"] = "not_configured"
        print(json.dumps(report, ensure_ascii=False) if args.json else SETUP)
        sys.exit(2)

    q = query(args.months)
    msgs = gmail.users().messages().list(userId="me", q=q, maxResults=50).execute().get("messages", [])
    if not args.json:
        print(f"{len(msgs)} mail(s) match:\n  {q}\n", file=sys.stderr)

    for ref in msgs:
        msg = gmail.users().messages().get(userId="me", id=ref["id"], format="full").execute()
        headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
        subject = headers.get("subject", "(no subject)")
        sender = headers.get("from", "")

        # Walk the MIME tree; a bank sometimes nests the PDF one level down.
        parts, stack = [], [msg["payload"]]
        while stack:
            part = stack.pop()
            stack.extend(part.get("parts", []) or [])
            name = part.get("filename") or ""
            if name.lower().endswith(".pdf") and part.get("body", {}).get("attachmentId"):
                parts.append(part)

        for part in parts:
            name = safe_name(part["filename"])
            entry = {"messageId": ref["id"], "filename": name, "subject": subject,
                     "sender": sender, "date": headers.get("date", "")}
            report["found"].append(name)

            if ref["id"] in seen_msgs:
                report["skipped"].append({**entry, "why": "this mail was imported before"})
                continue

            if args.check:
                report["imported"].append({**entry, "why": "would import"})
                continue

            body = gmail.users().messages().attachments().get(
                userId="me", messageId=ref["id"], id=part["body"]["attachmentId"]).execute()
            raw = base64.urlsafe_b64decode(body["data"])
            digest = hashlib.sha256(raw).hexdigest()

            if digest in seen_hashes:
                report["skipped"].append({**entry, "why": "identical file already imported"})
                ledger["seen"].append({**entry, "sha256": digest, "status": "duplicate-file"})
                continue

            STATEMENTS.mkdir(exist_ok=True)
            dest = STATEMENTS / name
            # Never overwrite a different statement that happens to share a name.
            if dest.exists() and hashlib.sha256(dest.read_bytes()).hexdigest() != digest:
                dest = STATEMENTS / f"{dest.stem}__{digest[:8]}{dest.suffix}"
            dest.write_bytes(raw)

            ok, output = parse(dest)
            if not ok:
                # A scan is not a broken parse, it is a statement that has to be
                # typed in — Ziraat's "Hesap_Hareketleri_*" mails are all scans.
                # Saying "parser failed" for those sends you looking for a bug
                # that is not there.
                scan = "no text layer" in output
                why = ("a scan with no text layer — add it to tools/manual_entry.py"
                       if scan else
                       (output.strip().splitlines()[-1] if output.strip() else "parser failed"))
                report["failed"].append({**entry, "why": why, "scan": scan})
                ledger["seen"].append({**entry, "sha256": digest,
                                       "status": "scan" if scan else "parse-failed"})
                continue

            # Which statement did that turn out to be, and did we have it?
            fresh = known_statement_ids()
            new_ids = [k for k in fresh if k not in have]
            sid = new_ids[0] if new_ids else None
            if sid is None:
                # The parser overwrote a period+bank we already held. That is a
                # re-issue of the same statement, not a second one.
                report["skipped"].append({**entry, "why": "same period and account already imported"})
                ledger["seen"].append({**entry, "sha256": digest, "status": "duplicate-period"})
                continue

            have = fresh
            report["imported"].append({**entry, "statementId": sid,
                                       "why": "imported", "file": fresh[sid]})
            ledger["seen"].append({**entry, "sha256": digest, "statementId": sid,
                                   "status": "imported",
                                   "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})

    if not args.check:
        save_ledger(ledger)

    if args.json:
        print(json.dumps(report, ensure_ascii=False))
        return

    print(f"  found     {len(report['found'])}", file=sys.stderr)
    print(f"  imported  {len(report['imported'])}", file=sys.stderr)
    for e in report["imported"]:
        print(f"    + {e.get('statementId') or e['filename']}", file=sys.stderr)
    print(f"  skipped   {len(report['skipped'])}", file=sys.stderr)
    for e in report["skipped"]:
        print(f"    = {e['filename']} — {e['why']}", file=sys.stderr)
    if report["failed"]:
        print(f"  FAILED    {len(report['failed'])}", file=sys.stderr)
        for e in report["failed"]:
            print(f"    ! {e['filename']} — {e['why']}", file=sys.stderr)


if __name__ == "__main__":
    main()
