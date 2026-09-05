"""
Shared shape for both parsers.

parse_statement.py (Claude API) and parse_statement_local.py (pdfplumber) must
emit byte-identical JSON, so the schema, the category→style map and the
serializer all live here rather than being duplicated in each.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel

# Categories the dashboard already knows how to render. Constraining to this set
# means the style lookup can never miss.
Category = Literal[
    "Shopping",
    "Food & Dining",
    "Entertainment",
    "Fixed Costs",
    "Investment",
    "Income",
    "Unknown",
]

# Presentation is decided here, not by the parser: it maps a semantic category
# to the icon and Tailwind classes index.html expects.
CATEGORY_STYLE: dict[str, dict[str, str]] = {
    "Shopping":      {"icon": "fa-bag-shopping",      "iconBg": "bg-violet-500/15",  "iconColor": "text-violet-400",  "catColor": "violet"},
    "Food & Dining": {"icon": "fa-utensils",          "iconBg": "bg-orange-500/15",  "iconColor": "text-orange-400",  "catColor": "orange"},
    "Entertainment": {"icon": "fa-film",              "iconBg": "bg-pink-500/15",    "iconColor": "text-pink-400",    "catColor": "pink"},
    "Fixed Costs":   {"icon": "fa-bolt",              "iconBg": "bg-blue-500/15",    "iconColor": "text-blue-400",    "catColor": "blue"},
    "Investment":    {"icon": "fa-chart-line",        "iconBg": "bg-cyan-500/15",    "iconColor": "text-cyan-400",    "catColor": "cyan"},
    "Income":        {"icon": "fa-building-columns",  "iconBg": "bg-emerald-500/15", "iconColor": "text-emerald-400", "catColor": "emerald"},
    "Unknown":       {"icon": "fa-question",          "iconBg": "bg-amber-500/15",   "iconColor": "text-amber-400",   "catColor": "amber"},
}

MONTHS_LONG = ["January", "February", "March", "April", "May", "June",
               "July", "August", "September", "October", "November", "December"]


class Instalment(BaseModel):
    """One payment of a plan: 'İşlemin 2/3 Taksidi' on a 3.120,86 TL purchase."""
    number: int        # which instalment this statement charges (1-based)
    total: int         # how many there are in all
    plan_total: float  # the original purchase amount, if the statement prints it


# What a row means for the month's real economics, as opposed to what the bank
# printed. Only "real" rows count as income or spending; the rest move money
# without earning or consuming it, and counting them double-counts the month.
Flow = Literal[
    "real",         # genuine income or spending
    "internal",     # between the account holder's own accounts (Rule D)
    "passthrough",  # a bridge transfer that cancels itself out (Rule C)
    "investment",   # money moved into the Midas brokerage account
    "trust",        # funds held for someone else, not income (Rule B)
]


class Transaction(BaseModel):
    date: str          # ISO 8601, YYYY-MM-DD
    merchant: str
    amount: float      # negative for expenses, positive for income
    type: Literal["income", "expense"]
    category: Category
    flow: Flow = "real"
    flow_note: str = ""               # why, in Turkish, for the dashboard
    # The other party on a transfer, normalised for grouping ("mustafaacik").
    counterparty: str = ""
    counterparty_label: str = ""      # as printed, for display
    instalment: Instalment | None = None


class Statement(BaseModel):
    bank: str
    account_mask: str  # e.g. "•••4821"; empty string if the PDF doesn't show one
    due_day: int | None = None   # day of month the card must be paid
    period_start: str  # ISO 8601
    period_end: str    # ISO 8601
    # A card statement never prints where its period starts, so period_start is
    # the earliest row and a quiet fortnight is indistinguishable from missing
    # coverage. False says "inferred" so the coverage report can hold its tongue.
    period_start_exact: bool = True
    # Where the statement prints a running balance. Consecutive statements for
    # one account must join: this month's opening is last month's closing, and
    # if they differ a statement is missing between them.
    opening_balance: float | None = None
    closing_balance: float | None = None
    transactions: list[Transaction]


def bank_slug(bank: str) -> str:
    """'Garanti BBVA (Bonus kredi kartı)' -> 'garanti-bbva-bonus-kredi-karti'."""
    folded = unicodedata.normalize("NFKD", bank.replace("ı", "i").replace("İ", "i"))
    ascii_only = "".join(c for c in folded if not unicodedata.combining(c)).lower()
    return re.sub(r"-{2,}", "-", re.sub(r"[^a-z0-9]+", "-", ascii_only)).strip("-") or "bank"


def write_index(data_dir) -> list[str]:
    """Refresh data/index.json — the dashboard reads it to know which statement
    files exist, since a browser cannot list a directory.

    Files are named <YYYY-MM>__<bank-slug>.json: several banks can cover the
    same month, and keying on the month alone made each import overwrite the
    last one.
    """
    import json
    from pathlib import Path

    data_dir = Path(data_dir)
    files = sorted(p.name for p in data_dir.glob("*.json") if p.stem != "index")
    months = sorted({f.split("__")[0] for f in files})
    (data_dir / "index.json").write_text(
        json.dumps({"files": files, "months": months,
                    "updated": datetime.now(timezone.utc).isoformat(timespec="seconds")},
                   indent=2),
        encoding="utf-8",
    )
    return files


def to_dashboard_json(stmt: Statement, source: str) -> dict:
    """Shape a Statement into what index.html consumes."""
    end = datetime.fromisoformat(stmt.period_end)

    transactions = []
    for i, tx in enumerate(stmt.transactions, start=1):
        style = CATEGORY_STYLE[tx.category]
        row = {
            "id": f"tx-{end:%Y%m}-{i:03d}",
            "date": tx.date,
            "merchant": tx.merchant,
            "category": tx.category,
            "amount": round(tx.amount, 2),
            "type": tx.type,
            "flow": tx.flow,
            **style,
        }
        if tx.flow_note:
            row["flowNote"] = tx.flow_note
        if tx.counterparty:
            row["counterparty"] = tx.counterparty
            row["counterpartyLabel"] = tx.counterparty_label or tx.counterparty
        if tx.instalment:
            row["instalment"] = {
                "number": tx.instalment.number,
                "total": tx.instalment.total,
                "planTotal": round(tx.instalment.plan_total, 2),
                "remaining": max(0, tx.instalment.total - tx.instalment.number),
            }
        transactions.append(row)

    # Gross is what the bank printed — the self-check is measured against it.
    # Net strips the rows that only move money around, which is what the
    # dashboard actually reports.
    income = sum(t["amount"] for t in transactions if t["type"] == "income")
    expense = sum(-t["amount"] for t in transactions if t["type"] == "expense")
    real = [t for t in transactions if t["flow"] == "real"]
    net_income = sum(t["amount"] for t in real if t["type"] == "income")
    net_expense = sum(-t["amount"] for t in real if t["type"] == "expense")
    invested = sum(-t["amount"] for t in transactions
                   if t["flow"] == "investment" and t["type"] == "expense")
    internal = sum(-t["amount"] for t in transactions
                   if t["flow"] == "internal" and t["type"] == "expense")

    # A stable name for this statement, independent of the file it arrived in:
    # "ziraat-bankasi-bankkart-2026-08". The same statement fetched twice — by
    # mail, by hand, under a different filename — lands on the same id, which is
    # what lets the fetcher skip one it has already imported. Keyed on the
    # period *end*, because that is the month the bank itself calls it.
    statement_id = f"{bank_slug(stmt.bank)}-{end:%Y-%m}"

    return {
        "statementId": statement_id,
        "statementMonth": f"{MONTHS_LONG[end.month - 1]} {end.year}",
        "bank": stmt.bank,
        "accountMask": stmt.account_mask,
        "dueDay": stmt.due_day,
        "periodStart": stmt.period_start,
        "periodEnd": stmt.period_end,
        "periodStartExact": stmt.period_start_exact,
        "openingBalance": stmt.opening_balance,
        "closingBalance": stmt.closing_balance,
        "fetchedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source,
        "summary": {
            "totalIncome": round(income, 2),
            "totalExpense": round(expense, 2),
            "netCashFlow": round(income - expense, 2),
            "realIncome": round(net_income, 2),
            "realExpense": round(net_expense, 2),
            "realNet": round(net_income - net_expense, 2),
            "invested": round(invested, 2),
            "internalOut": round(internal, 2),
        },
        "transactions": transactions,
    }
