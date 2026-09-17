"""Move the single-user build's data and decisions into a hosted account.

    .venv/Scripts/python.exe server/migrate_local.py someone@example.com

What moves, and why re-uploading the PDFs is not the same thing:

- data/*.json — the parsed statements. Two of them (Ziraat vadesiz) were
  hand-entered from scans that have no text layer, so there is no PDF the
  upload route could ever read; and every one of them was parsed with the
  full profile (regular income, bridges, trust), which a fresh account did
  not have at upload time. The JSON already carries the right `flow` on
  every row; the upload route would have to re-derive it and could not.
- profile.json — the rules themselves, so future uploads parse the same way.
- user-state.json — every decision: categories, matches, recurring rules,
  instalment notes, cycle day, count-from date, preferences.

The one translation: match groups and dismissals are keyed by row, and a row
key is `<months key>#<transaction id>`. Locally the months key is the file
name (`2026-08__ziraat-bankasi-vadesiz-hesap.json`); the hosted page keys
months by statementId (`ziraat-bankasi-vadesiz-hesap-2026-08`). Copied as-is,
every match would point at a row that does not exist and silently vanish —
which is what "all my matches are gone" looks like. They are rewritten here.

Statements already on the account are replaced. The database is backed up
first, next to itself, with a timestamp.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))

import db  # noqa: E402


def load_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8"))


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {argv[0]} EMAIL")
        return 2
    email = argv[1].strip().lower()

    data_dir, profile_p, state_p = ROOT / "data", ROOT / "profile.json", ROOT / "user-state.json"
    files = sorted(p for p in data_dir.glob("*.json") if p.name != "index.json")
    if not files:
        print("Nothing in data/ to move.")
        return 1

    # ── read everything before touching the database ──
    statements = []
    key_map: dict[str, str] = {}           # local months key → hosted months key
    for p in files:
        payload = load_json(p)
        sid = payload.get("statementId")
        if not sid:
            print(f"{p.name}: no statementId — skipped (re-run the parser on it first).")
            continue
        key_map[p.name] = sid
        statements.append((p, sid, payload))

    profile = {k: v for k, v in load_json(profile_p).items() if not k.startswith("_")} \
        if profile_p.is_file() else {}
    state = load_json(state_p) if state_p.is_file() else {}

    # Rewrite row keys inside the matches bundle. The bundle is stored as a
    # JSON *string* (that is how the page keeps it), so decode, rewrite, encode.
    rewritten = 0
    if isinstance(state.get("aifp.matches"), str):
        m = json.loads(state["aifp.matches"])

        def fix(key: str) -> str:
            nonlocal rewritten
            fname, _, tx = key.partition("#")
            if fname in key_map:
                rewritten += 1
                return f"{key_map[fname]}#{tx}"
            return key

        for g in m.get("groups", []):
            g["rows"] = [fix(k) for k in g.get("rows", [])]
        m["dismissed"] = [fix(k) for k in m.get("dismissed", [])]
        state["aifp.matches"] = json.dumps(m, ensure_ascii=False)

    # ── write ──
    db.init()
    if db.DATABASE_URL.startswith("sqlite:///"):
        db_path = Path(db.DATABASE_URL.removeprefix("sqlite:///"))
        if db_path.is_file():
            bak = db_path.with_name(f"{db_path.stem}.pre-migrate-{datetime.now():%Y%m%d-%H%M%S}{db_path.suffix}")
            shutil.copy2(db_path, bak)
            print(f"Backup: {bak.name}")

    with db.session() as s:
        u = db.user_by_email(s, email)
        if u is None:
            print(f"No account with the address {email!r}.")
            return 1

        existing = {x.statement_id: x for x in db.user_statements(s, u.id)}
        added = replaced = 0
        for p, sid, payload in statements:
            digest = hashlib.sha256(p.read_bytes()).hexdigest()
            row = existing.get(sid)
            if row is None:
                s.add(db.Statement(user_id=u.id, statement_id=sid, bank=payload.get("bank", ""),
                                   period_end=payload.get("periodEnd", ""), sha256=digest,
                                   payload=payload))
                added += 1
            else:
                row.payload, row.sha256 = payload, digest
                row.bank, row.period_end = payload.get("bank", ""), payload.get("periodEnd", "")
                replaced += 1

        merged_profile = dict(u.profile or {})
        merged_profile.update(profile)
        u.profile = merged_profile

        # Keep what only the server knows (the tour flag), overlay every
        # decision from the file.
        merged_state = db.get_state(s, u.id)
        merged_state.update(state)
        db.put_state(s, u.id, merged_state)
        s.commit()

    print(f"{email}: {added} statement(s) added, {replaced} replaced, "
          f"{len(statements)} on the account now.")
    print(f"Profile keys: {', '.join(sorted(merged_profile)) or 'none'}")
    print(f"State keys: {len(merged_state)} · match row keys rewritten: {rewritten}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
