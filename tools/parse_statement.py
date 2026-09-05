"""
Bank statement PDF -> dashboard JSON.

Reads a bank statement PDF (optionally password-protected, as Turkish banks
email them), extracts the transactions with Claude, and writes the JSON file
that index.html loads.

Usage:
    .venv\\Scripts\\python.exe tools\\parse_statement.py statements\\ekstre.pdf
    .venv\\Scripts\\python.exe tools\\parse_statement.py statements\\ekstre.pdf --password 12345678901
    .venv\\Scripts\\python.exe tools\\parse_statement.py statements\\ekstre.pdf --out data\\2026-08.json

Credentials come from the environment, never from this file:
    ANTHROPIC_API_KEY     - required
    EKSTRE_PDF_PASSWORD   - optional, used when --password is not given
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import pikepdf
import anthropic
from anthropic import Anthropic
from pydantic import BaseModel

# Extraction is a straightforward task, so this defaults to the cheap model.
# Switch to "claude-opus-5" if a statement layout defeats it — that model is
# roughly 5x the cost but the most reliable on messy or scanned PDFs.
MODEL = "claude-haiku-4-5"

# The Messages API caps a request at 32 MB; base64 inflates by 4/3.
MAX_PDF_BYTES = 20 * 1024 * 1024

# 600 pages normally, but 100 on 200K-context models such as Haiku 4.5.
MAX_PDF_PAGES = 100 if MODEL.startswith("claude-haiku") else 600

# Categories the dashboard already knows how to render. Constraining the model
# to this set means the style lookup below can never miss.
Category = Literal[
    "Shopping",
    "Food & Dining",
    "Entertainment",
    "Fixed Costs",
    "Investment",
    "Income",
    "Unknown",
]

# Presentation is decided here, not by the model: it maps a semantic category
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


# ── Schema the model must fill ────────────────────────────────────────────────
# messages.parse() validates the response against this, so parsed_output either
# matches the shape or raises — there is no "sometimes it returned bad JSON".

class Transaction(BaseModel):
    date: str          # ISO 8601, YYYY-MM-DD
    merchant: str
    amount: float      # negative for expenses, positive for income
    type: Literal["income", "expense"]
    category: Category


class Statement(BaseModel):
    bank: str
    account_mask: str  # e.g. "•••4821"; empty string if the PDF doesn't show one
    period_start: str  # ISO 8601
    period_end: str    # ISO 8601
    transactions: list[Transaction]


EXTRACTION_PROMPT = """\
Extract every transaction from this bank statement.

Formatting rules for the source document:
- Amounts are tr-TR formatted: "1.234,56" means one thousand two hundred
  thirty-four and 56/100. The dot is a thousands separator, the comma is the
  decimal separator. Parse accordingly — do not read "1.234,56" as 1.234.
- Dates are usually dd.MM.yyyy or dd/MM/yyyy. Return ISO 8601 (YYYY-MM-DD).
- Expenses (debits, harcama, çekilen) are negative. Income (credits, yatan,
  maaş, iade) is positive.

Assign each transaction the single best-fitting category. Use "Fixed Costs" for
rent, utilities, insurance, subscriptions and loan payments. Use "Unknown" only
when the merchant is genuinely unidentifiable (e.g. a bare POS terminal code).

Include every transaction line. Do not include running balances, interest
summaries, or subtotal rows as transactions.
"""


def decrypt_if_needed(pdf_path: Path, password: str | None) -> bytes:
    """Return the PDF bytes, decrypting first if the file is protected."""
    try:
        with pikepdf.open(pdf_path):
            return pdf_path.read_bytes()  # not encrypted
    except pikepdf.PasswordError:
        pass

    if not password:
        sys.exit(
            f"'{pdf_path.name}' is password-protected. Pass --password, or set "
            "EKSTRE_PDF_PASSWORD. Turkish banks typically use your TCKN, your "
            "date of birth, or the last digits of the card."
        )

    try:
        with pikepdf.open(pdf_path, password=password) as pdf:
            # Save the decrypted copy to a temp file, then read it back.
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            pdf.save(tmp_path)
        try:
            return tmp_path.read_bytes()
        finally:
            tmp_path.unlink(missing_ok=True)
    except pikepdf.PasswordError:
        sys.exit("Wrong password for that PDF.")


def count_pages(pdf_bytes: bytes) -> int:
    with pikepdf.open(io.BytesIO(pdf_bytes)) as pdf:
        return len(pdf.pages)


def extract(pdf_bytes: bytes) -> Statement:
    # Zero-arg constructor: resolves ANTHROPIC_API_KEY, else ANTHROPIC_AUTH_TOKEN,
    # else the OAuth profile written by `ant auth login`.
    client = Anthropic()

    try:
        response = _request(client, pdf_bytes)
    except anthropic.AuthenticationError:
        sys.exit(
            "Authentication failed. Either run `ant auth login`, or set "
            "ANTHROPIC_API_KEY. Check what is active with `ant auth status`."
        )
    except anthropic.RateLimitError:
        sys.exit("Rate limited. Wait a moment and run it again.")
    except anthropic.APIConnectionError:
        sys.exit("Could not reach the API — check your network connection.")

    return _validate(response)


def _request(client: Anthropic, pdf_bytes: bytes):
    return client.messages.parse(
        model=MODEL,
        max_tokens=16000,
        # Do NOT add output_config={"effort": ...} while MODEL is Haiku 4.5 —
        # the effort parameter errors on that model. It is available on
        # claude-opus-5 if you switch back.
        messages=[{
            "role": "user",
            "content": [
                # The document block goes before the text block.
                {
                    "type": "document",
                    "source": {
                        "type": "base64",
                        "media_type": "application/pdf",
                        "data": base64.standard_b64encode(pdf_bytes).decode(),
                    },
                },
                {"type": "text", "text": EXTRACTION_PROMPT},
            ],
        }],
        output_format=Statement,
    )

def _validate(response) -> Statement:
    if response.stop_reason == "refusal":
        detail = getattr(response.stop_details, "explanation", None) or "no explanation given"
        sys.exit(f"The model declined to process this document: {detail}")
    if response.stop_reason == "max_tokens":
        sys.exit(
            "Output hit the token cap — the statement is longer than max_tokens "
            "allows. Raise max_tokens, or split the PDF by page range."
        )
    if response.parsed_output is None:
        sys.exit("The model returned no parseable output.")

    usage = response.usage
    print(
        f"  tokens: {usage.input_tokens} in / {usage.output_tokens} out",
        file=sys.stderr,
    )
    return response.parsed_output


def to_dashboard_json(stmt: Statement, source: str) -> dict:
    """Shape the extraction into what index.html consumes."""
    months = ["January", "February", "March", "April", "May", "June",
              "July", "August", "September", "October", "November", "December"]
    end = datetime.fromisoformat(stmt.period_end)

    transactions = []
    for i, tx in enumerate(stmt.transactions, start=1):
        style = CATEGORY_STYLE[tx.category]
        transactions.append({
            "id": f"tx-{end:%Y%m}-{i:03d}",
            "date": tx.date,
            "merchant": tx.merchant,
            "category": tx.category,
            "amount": round(tx.amount, 2),
            "type": tx.type,
            **style,
        })

    income = sum(t["amount"] for t in transactions if t["type"] == "income")
    expense = sum(-t["amount"] for t in transactions if t["type"] == "expense")

    return {
        "statementMonth": f"{months[end.month - 1]} {end.year}",
        "bank": stmt.bank,
        "accountMask": stmt.account_mask,
        "periodStart": stmt.period_start,
        "periodEnd": stmt.period_end,
        "fetchedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "source": source,
        "summary": {
            "totalIncome": round(income, 2),
            "totalExpense": round(expense, 2),
            "netCashFlow": round(income - expense, 2),
        },
        "transactions": transactions,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="Parse a bank statement PDF into dashboard JSON.")
    ap.add_argument("pdf", type=Path, help="path to the statement PDF")
    ap.add_argument("--password", help="PDF password (falls back to EKSTRE_PDF_PASSWORD)")
    ap.add_argument("--out", type=Path, help="output path (default: data/<YYYY-MM>.json)")
    args = ap.parse_args()

    # No API-key check here on purpose. An unset ANTHROPIC_API_KEY does not mean
    # there are no credentials — the SDK also resolves ANTHROPIC_AUTH_TOKEN and
    # the OAuth profile left by `ant auth login`. Let the SDK decide, and turn
    # its auth failure into a readable message (see extract()).
    if not args.pdf.is_file():
        sys.exit(f"No such file: {args.pdf}")

    size = args.pdf.stat().st_size
    if size > MAX_PDF_BYTES:
        sys.exit(f"{args.pdf.name} is {size / 1e6:.1f} MB; the request cap is ~20 MB.")

    print(f"Reading {args.pdf.name} ...", file=sys.stderr)
    pdf_bytes = decrypt_if_needed(args.pdf, args.password or os.environ.get("EKSTRE_PDF_PASSWORD"))

    pages = count_pages(pdf_bytes)
    if pages > MAX_PDF_PAGES:
        sys.exit(
            f"{args.pdf.name} has {pages} pages; {MODEL} accepts {MAX_PDF_PAGES}. "
            "Split the PDF, or switch MODEL to claude-opus-5 (600-page limit)."
        )

    print(f"Extracting transactions from {pages} page(s) via {MODEL} ...", file=sys.stderr)
    stmt = extract(pdf_bytes)

    payload = to_dashboard_json(stmt, source=f"Manual import — {args.pdf.name}")

    out = args.out or Path("data") / f"{datetime.fromisoformat(stmt.period_end):%Y-%m}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    s = payload["summary"]
    print(
        f"\n{stmt.bank} {payload['statementMonth']}: "
        f"{len(payload['transactions'])} transactions, "
        f"income {s['totalIncome']:,.2f} / expense {s['totalExpense']:,.2f} "
        f"/ net {s['netCashFlow']:,.2f}\n"
        f"Wrote {out}",
        file=sys.stderr,
    )


if __name__ == "__main__":
    main()
