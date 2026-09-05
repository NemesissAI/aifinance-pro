# Start here

**Read `CLAUDE.md` first — it is the real handoff document** (~680 lines) and it
replaces the chat history. Everything below is just orientation.

This file exists because different tools look for different filenames. The
content is the same for all of them: Claude Code, Antigravity, Cursor, Copilot,
Codex, or a person.

## What this is

A personal finance dashboard. Turkish bank statement PDFs are parsed locally
into JSON; a single-file dashboard reads that JSON. Two shapes coexist:

| | entry point | state |
|---|---|---|
| **Local, single user** | `.claude/static-server.ps1` + `index.html` | working, in daily use |
| **Hosted, multi-user** | `server/app.py` (FastAPI) | accounts + upload + isolation done; onboarding UI, maintenance mode and Google sign-in not yet |

## Run it

```bash
# local single-user dashboard  -> http://localhost:4173
powershell -NoProfile -ExecutionPolicy Bypass -File .claude/static-server.ps1

# multi-user API               -> http://localhost:8000
.venv/Scripts/python.exe -m uvicorn server.app:app --reload --port 8000
```

## The one rule that matters

Every parser checks itself against the statement's own printed arithmetic, and
where a running balance exists it checks every row individually. **Output
without the `✓` line is not trustworthy.** That check is what caught a ₺10.000
transfer the parser had been silently dropping. See CLAUDE.md § "The rule that
matters most".

## What is deliberately not in this repo

`data/`, `statements/`, `profile.json`, `user-state.json`, `aifinance.db` are
gitignored. They are one person's real bank statements and the names of real
third parties. The code must work with none of them present — a new user starts
empty, and that is the supported path, not an edge case.

## Verifying a change

```bash
# re-parse every statement and compare against the previous output
.venv/Scripts/python.exe tools/parse_statement_local.py statements/<file>.pdf
.venv/Scripts/python.exe tools/manual_entry.py
```

Any change to the parser must leave existing output byte-identical unless the
change is *meant* to alter it — diff before and after, do not assume.
