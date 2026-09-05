"""Parse a statement from bytes, for a named user, without touching the disk.

The CLI in parse_statement_local.py is built for one person at a keyboard: it
takes a Path and calls sys.exit() with a sentence for a human when anything is
wrong. A server can do neither — it has bytes in memory, it serves many people,
and an exit kills the worker instead of answering the request.

This module is the seam. It reuses the same templates, the same self-checks and
the same rules engine, and turns every exit into a typed exception the API can
map onto a status code.

Nothing here writes a PDF anywhere. Once the rows are extracted the file has
done its job, and keeping other people's bank statements on a disk is a
liability with no matching benefit.
"""

from __future__ import annotations

import io
import sys
from pathlib import Path

import pdfplumber
import pikepdf

sys.path.insert(0, str(Path(__file__).parent))
import parse_statement_local as P  # noqa: E402
import rules  # noqa: E402
from statement_model import Statement, to_dashboard_json  # noqa: E402


class ParseError(Exception):
    """Base: the statement could not be turned into rows."""
    code = "parse_failed"


class PasswordRequired(ParseError):
    code = "password_required"


class WrongPassword(ParseError):
    code = "wrong_password"


class ScannedPdf(ParseError):
    code = "scanned"


class NoTemplate(ParseError):
    code = "no_template"


def _decrypt(data: bytes, password: str | None) -> bytes:
    """Return readable bytes, or say precisely why we cannot."""
    try:
        with pikepdf.open(io.BytesIO(data)):
            return data
    except pikepdf.PasswordError:
        pass
    if not password:
        raise PasswordRequired(
            "This statement is password-protected. Turkish banks usually use "
            "your TCKN, your date of birth, or the last digits of the card."
        )
    try:
        with pikepdf.open(io.BytesIO(data), password=password) as pdf:
            out = io.BytesIO()
            pdf.save(out)
            return out.getvalue()
    except pikepdf.PasswordError:
        raise WrongPassword("That password did not open the statement.")


def parse_bytes(data: bytes, *, password: str | None = None,
                profile: dict | None = None, filename: str = "upload.pdf") -> dict:
    """bytes -> the same payload the dashboard already reads.

    `profile` is the owner's rules (see tools/rules.py). Passing None means a
    brand-new user with no rules yet: the statement still parses and still
    self-checks, everything simply lands in the Unknown queue for them to
    classify — which is the correct starting point, not a failure.
    """
    data = _decrypt(data, password)
    ruleset = rules.RuleSet(profile)

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        pages = P.unique_pages(pdf)
        if not pages:
            raise ParseError("The PDF has no readable pages.")
        first = pages[0].extract_text() or ""
        if not first.strip() and not pages[0].extract_words():
            raise ScannedPdf(
                "This PDF is a scan with no text layer, so there is nothing to "
                "read out of it. Ask your bank for a normal PDF."
            )
        for name, detects, parser in P.TEMPLATES:
            if detects(first):
                txns, meta = parser(pages)
                meta.setdefault("due_day", P.read_due_day(first))
                bank = name
                break
        else:
            raise NoTemplate(
                "No template matches this statement yet. Supported today: "
                + ", ".join(n for n, _, _ in P.TEMPLATES) + "."
            )

    if not txns:
        raise ParseError("The layout matched but no transactions were found.")

    # Order matters and is the same as the CLI's: verify() must measure the
    # untouched rows, so the rules run after it.
    warnings = P.verify(txns, meta)
    P.apply_rules(txns, ruleset)

    printed_start = meta.get("period_start")
    totals = meta.get("totals") or {}
    opening, closing = meta.get("opening_balance"), meta.get("closing_balance")
    if opening is None and "carried" in totals:
        opening, closing = -totals["carried"], -totals["due"]

    stmt = Statement(
        bank=bank,
        account_mask="",
        due_day=meta.get("due_day"),
        period_start=printed_start or min(t.date for t in txns),
        period_end=meta["statement_date"],
        period_start_exact=bool(printed_start),
        opening_balance=round(opening, 2) if opening is not None else None,
        closing_balance=round(closing, 2) if closing is not None else None,
        transactions=sorted(txns, key=lambda t: t.date, reverse=True),
    )
    payload = to_dashboard_json(stmt, source=f"Uploaded — {filename}")
    payload["warnings"] = warnings
    return payload
