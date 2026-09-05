# Two tools, one folder

This project is worked on from more than one place — Claude Code and
Antigravity both open the same directory. Neither locks files, so nothing stops
them overwriting each other. These are the rules that keep that safe.

## 0. Announce what you're touching, before you touch it

`AGENT-CHAT.md` is a live status board and a log. Claim your files there
*before* editing, drop the claim when you commit and stop. It's a convention,
not a lock — it only works if both sides actually check it first.

## 1. Git is the shared memory. Commit before switching.

Whoever is about to stop working commits first:

```bash
git add -A && git commit -m "what changed"
git status          # must print "nothing to commit" before you hand over
```

The other tool then starts from a known state. A dirty working tree handed to a
second tool is how edits get silently clobbered.

**Never, from either tool:** `git reset --hard`, `git checkout -- .`,
`git clean -fd`. They delete uncommitted work with no undo. If a change needs
abandoning, commit it first and revert the commit.

## 2. The irreplaceable files are not in git.

`data/`, `statements/`, `profile.json`, `user-state.json`, `mail-log.json`,
`aifinance.db` and `.gmail/` are gitignored on purpose: they are real bank
statements and real third-party names, and they must never reach a remote.

The consequence is the thing to remember: **git cannot restore them.** So:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File snapshot.ps1
```

Snapshots go to `../aifinance-snapshots/<timestamp>/`, outside the project
folder, and the last ten are kept. Run it before any large change. `-List`
shows what exists; each snapshot records the commit it belonged to, so data and
code can be matched back up.

**Neither tool should ever delete those paths.** If one proposes to, say no.

## 3. Run one server at a time.

Both stacks bind fixed ports: `4173` (local dashboard) and `8000` (multi-user
API). Two tools starting a server means the second silently fails to bind and
the first keeps serving — so you edit, reload, and see no change, which reads
like a code bug. Check before starting:

```bash
powershell -NoProfile -Command "Get-NetTCPConnection -LocalPort 4173,8000 -State Listen -ErrorAction SilentlyContinue"
```

## 4. Rules live in files, not in a chat.

Anything learned about this project belongs in `CLAUDE.md` (the long handoff
document) so the other tool inherits it. A decision explained only in a
conversation is lost the moment that conversation ends — which is exactly why
`CLAUDE.md` exists and why it is written the way it is.

`AGENTS.md` is the short orientation, and points at `CLAUDE.md`.

## 5. Before trusting any parser change

```bash
.venv/Scripts/python.exe tools/parse_statement_local.py statements/<file>.pdf
.venv/Scripts/python.exe tools/manual_entry.py
```

Output without the `✓` line is not trustworthy. A parser change must leave
existing output identical unless it is *meant* to change it — snapshot `data/`,
re-parse, diff.
