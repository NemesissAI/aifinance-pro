# Which model should do this?

Pick by **how a mistake would show up**, not by which model is strongest.
Some errors announce themselves the moment you look at the screen. Others sit
quietly in a total for two months. Those two deserve different care.

## The test

Ask one question about the task: **if this were done wrong, when would I find
out — and how?**

| If it goes wrong… | Tier | Use |
|---|---|---|
| You see it immediately on screen | **1** | a fast, cheap model |
| A test or a run fails | **2** | a mid model |
| A number is quietly wrong, or a door is quietly open | **3** | the strongest model available, and the verification below is not optional |

## Tier 1 — visible instantly

CSS, layout, spacing, colour. Copy and translations. Static markup for a form
or screen. Searching the codebase. Mechanical renames with a clear spec.

## Tier 2 — a run or a test catches it

New API endpoints with a clear contract. Maintenance mode, version banner,
onboarding screens' behaviour, the in-app tour. Google OAuth wiring. Writing
tests. Refactors that must preserve behaviour and have tests to prove it.

## Tier 3 — silent when wrong

- **Parser templates and the self-checks** (`tools/parse_statement_local.py`).
  A parse whose columns add up can still be missing a row: ₺10.000 went missing
  and only the running-balance chain caught it.
- **Financial meaning** — `flow` classification, matching, cycle arithmetic,
  `categoryTotals()`. Wrong here looks exactly like right.
- **Security** — authentication, sessions, secrets, anything putting request
  data into a command. The password field once injected a parser argument and
  wrote a file outside the project; it took acting like an attacker to find.
- **Data migration** — changing the stored payload shape. Users' PDFs are
  deliberately not kept, so a bad migration cannot be re-derived.
- **Architecture** — anything that decides how the next ten changes are made.

## The verification is the real safeguard

Tier 3 is less about the model than the process. **A cheap model that follows
these steps beats an expensive one that skips them.** Any change touching a
parser or money:

```bash
powershell -NoProfile -ExecutionPolicy Bypass -File snapshot.ps1   # before
.venv/Scripts/python.exe tools/parse_statement_local.py statements/<file>.pdf
.venv/Scripts/python.exe tools/manual_entry.py
```

Then diff the new `data/*.json` against the snapshot. **Output identical unless
the change was meant to alter it**, and every statement printing its `✓` line.
No `✓`, no trust.

## If you are the model reading this

Work out the tier before you start. If the task is Tier 3 and you were picked
for speed, say so and ask to be swapped rather than proceeding — being wrong
here is expensive and quiet. State which tier you judged it, and why, before
you change anything.
