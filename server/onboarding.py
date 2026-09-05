"""Work out one user's statement cycle from their own statements.

"Month starts on day N" is the single setting everything else hangs off, and it
is the one thing a new user cannot possibly answer on day one. Asking them to
guess produces the calendar month, which is wrong for almost everybody: cards
close mid-month and salaries land on the 1st or the 15th, so a calendar
boundary saws linked events in half.

It is computable instead. Given a few months of statements we can count, for
every candidate day, how many *linked* events it would split:

  - a transfer's two legs (money out of one account, into another)
  - a card's charge -> payment arc (it closes on one day, is paid on another)

and how long after a cycle ends before every card statement covering it has
arrived. Fewest splits wins; ties go to the shorter wait.

This is the same measurement that picked day 22 for the first user, run for
each new one against their own banks.
"""

from __future__ import annotations

import calendar
import datetime as dt
from collections import defaultdict

LEG_DAYS = 5          # a transfer's two legs land within this many days
MIN_STATEMENTS = 2    # below this there is nothing to measure


def _cycle_of(iso: str, start: int) -> str:
    d = dt.date.fromisoformat(iso)
    if d.day < start:
        d = (d.replace(day=1) - dt.timedelta(days=1)).replace(day=1)
    return f"{d.year}-{d.month:02d}"


def _on(y: int, m: int, day: int) -> dt.date:
    return dt.date(y, m, min(day, calendar.monthrange(y, m)[1]))


def _add_month(y: int, m: int, k: int = 1) -> tuple[int, int]:
    n = m - 1 + k
    return y + n // 12, n % 12 + 1


def analyse(payloads: list[dict]) -> dict:
    """payloads: the parsed statements this user has uploaded."""
    rows = []
    cards: dict[str, dict] = {}
    for p in payloads:
        bank = p.get("bank", "")
        for t in p.get("transactions", []):
            rows.append({**t, "_bank": bank})
        # A card announces itself: it prints the day the balance must be paid.
        if p.get("dueDay"):
            end = p.get("periodEnd", "")
            if end:
                cards.setdefault(bank, {"cut": int(end[-2:]), "due": int(p["dueDay"])})

    if len(payloads) < MIN_STATEMENTS or not rows:
        return {"ready": False,
                "reason": "Upload a few months of statements and this can be measured.",
                "statements": len(payloads)}

    # The linked pairs actually present: an outflow and an inflow of the same
    # size, days apart, on two different accounts.
    outs = [r for r in rows if r.get("flow") in ("internal", "passthrough") and r["amount"] < 0]
    used, pairs = set(), []
    for t in rows:
        if t.get("flow") not in ("internal", "passthrough") or t["amount"] <= 0:
            continue
        for i, o in enumerate(outs):
            if i in used:
                continue
            gap = abs((dt.date.fromisoformat(o["date"]) - dt.date.fromisoformat(t["date"])).days)
            if abs(abs(o["amount"]) - t["amount"]) < 0.02 and gap <= LEG_DAYS:
                used.add(i)
                pairs.append((o["date"], t["date"]))
                break

    # A sample month to measure card arcs and arrival lag against.
    last = max(r["date"] for r in rows)
    y, m = int(last[:4]), int(last[5:7])

    scored = []
    for start in range(1, 29):
        split = sum(1 for a, b in pairs if _cycle_of(a, start) != _cycle_of(b, start))
        for spec in cards.values():
            cut = _on(y, m, spec["cut"])
            due = (_on(y, m, spec["due"]) if spec["due"] > spec["cut"]
                   else _on(*_add_month(y, m), spec["due"]))
            if _cycle_of(cut.isoformat(), start) != _cycle_of(due.isoformat(), start):
                split += 1

        # How long after the cycle ends before every card covering it has closed.
        end = _on(*_add_month(y, m), start) - dt.timedelta(days=1)
        wait = 0
        for spec in cards.values():
            cy, cm = end.year, end.month
            c = _on(cy, cm, spec["cut"])
            if c < end:
                cy, cm = _add_month(cy, cm)
                c = _on(cy, cm, spec["cut"])
            wait = max(wait, (c - end).days)
        scored.append({"day": start, "split": split, "wait": wait})

    scored.sort(key=lambda x: (x["split"], x["wait"]))
    best = scored[0]
    calendar_month = next(x for x in scored if x["day"] == 1)

    why = []
    if best["split"] == 0:
        why.append("it keeps every linked transfer and every card's "
                   "charge-to-payment arc inside one cycle")
    if calendar_month["split"] > best["split"]:
        why.append(f"a plain calendar month would split "
                   f"{calendar_month['split']} of them")
    if best["wait"]:
        why.append(f"a cycle is fully covered about {best['wait']} days after it ends, "
                   "once the card statements arrive")

    return {
        "ready": True,
        "recommended": best["day"],
        "splitEvents": best["split"],
        "waitDays": best["wait"],
        "calendarMonthSplits": calendar_month["split"],
        "linkedPairs": len(pairs),
        "cards": {b: v for b, v in cards.items()},
        "why": why,
        "alternatives": [x for x in scored[:5]],
    }
