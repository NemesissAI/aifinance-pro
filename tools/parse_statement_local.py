"""
Bank statement PDF -> dashboard JSON, fully offline. No API, no cost, no data
leaves the machine.

Currently carries one template: Ziraat Bankası "Bankkart" credit card
statements. See ziraat_bankkart() for the layout rules and add_template() for
how to teach it another bank.

Usage:
    .venv\\Scripts\\python.exe tools\\parse_statement_local.py statements\\ekstre.pdf
    .venv\\Scripts\\python.exe tools\\parse_statement_local.py statements\\ekstre.pdf --password 12345678901
    .venv\\Scripts\\python.exe tools\\parse_statement_local.py statements\\ekstre.pdf --show
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import unicodedata
from datetime import datetime
from pathlib import Path

import pdfplumber
import pikepdf

sys.path.insert(0, str(Path(__file__).parent))
import rules  # noqa: E402
from statement_model import (Instalment, Statement, Transaction,  # noqa: E402
                             bank_slug, to_dashboard_json, write_index)

# ── Formats ───────────────────────────────────────────────────────────────────

DATE_RE = re.compile(r"^(\d{2})/(\d{2})/(\d{4})$")
# tr-TR money: 1.234,56 — dot groups thousands, comma is the decimal point.
# A trailing '+' marks a credit (a payment onto the card).
AMOUNT_RE = re.compile(r"^-?\d{1,3}(?:\.\d{3})*,\d{2}\+?$")


def parse_amount(text: str) -> float:
    """'1.234,56' -> 1234.56 ; '1.234,56+' -> 1234.56 (sign handled by caller)."""
    return float(text.rstrip("+").replace(".", "").replace(",", "."))


def norm(text: str) -> str:
    """Fold Turkish text to plain ASCII lowercase for matching.

    Do not use str.casefold() directly here. Python folds 'İ' to 'i' + U+0307
    (a combining dot), which never compares equal to a plain 'i' — so
    'İŞLEM TARİHİ'.casefold() != 'işlem tarihi' and every match silently fails.
    Decomposing and dropping combining marks also folds ş/ğ/ç/ö/ü to s/g/c/o/u,
    which lets the rules below be written in plain ASCII and still match both
    'GIDA' and 'GİDA'.
    """
    text = text.replace("ı", "i").replace("I", "i").replace("İ", "i")
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).casefold()


# ── Merchant → category ───────────────────────────────────────────────────────
# Matched case-insensitively as substrings, first hit wins, so put the specific
# entries above the general ones. Everything unmatched becomes "Unknown", which
# the dashboard renders in amber — that is the signal to add a rule here.

CATEGORY_RULES: list[tuple[str, str]] = [
    # payments onto the card
    ("hesaptan odeme",  "Income"),

    # generic POS descriptors the statement never resolves to a real merchant
    ("takside pos",     "Unknown"),

    # eating out — before the grocery rules, so "TRENDYOL YEMEK" doesn't fall
    # through to the "trendyol" retail rule below
    ("trendyol yemek",  "Food & Dining"),
    ("doner",           "Food & Dining"),
    ("cigkoft",         "Food & Dining"),
    ("koftecis",        "Food & Dining"),
    ("kofte",           "Food & Dining"),
    ("kafe",            "Food & Dining"),
    ("bufe",            "Food & Dining"),
    ("hacioglu",        "Food & Dining"),
    ("matik otomat",    "Food & Dining"),

    # groceries / markets
    ("bim a.s",         "Food & Dining"),
    ("bim ",            "Food & Dining"),
    ("bima.s",          "Food & Dining"),
    ("betroburger",     "Food & Dining"),
    ("burger",          "Food & Dining"),
    ("yemeksepeti",     "Food & Dining"),
    ("a101",            "Food & Dining"),   # terminal digits often prefix this
    ("hakmar",          "Food & Dining"),
    ("migros",          "Food & Dining"),
    ("alkan market",    "Food & Dining"),
    ("gida",            "Food & Dining"),
    ("kuruyemis",       "Food & Dining"),
    ("tekel",           "Food & Dining"),

    # transport / fuel / municipal
    ("opet",            "Fixed Costs"),
    ("belbim",          "Fixed Costs"),   # İstanbulkart
    ("konbeltas",       "Fixed Costs"),
    ("ibb ",            "Fixed Costs"),
    ("ulasim",          "Fixed Costs"),

    # retail
    ("trendyol",        "Shopping"),
    ("media markt",     "Shopping"),
    ("vitamin",         "Shopping"),
    ("tobacco",         "Shopping"),
    ("ennova",          "Shopping"),

    # travel / leisure
]


# Ziraat writes a lot of terminal noise into the description: a city suffix, a
# POS/terminal code, and for instalments the original total inline
# ("25/06 S/TRENDYOL 02.Tak İSTANBUL 3.120,86 TL İşlemin 2/3 Taksidi").
# Left alone, that noise becomes the "merchant" and breaks both grouping and
# category matching.

CITIES = {
    "istanbul", "ankara", "izmir", "bursa", "antalya", "konya", "kocaeli",
    "adana", "gaziantep", "mersin", "kayseri", "eskisehir", "samsun", "denizli",
    "sakarya", "trabzon", "malatya", "erzurum", "van", "aydin", "balikesir",
    "tekirdag", "manisa", "hatay", "mugla", "ordu", "tokat", "sivas", "trtr",
}
INSTALMENT_RE = re.compile(r"\s*\d{1,3}(?:\.\d{3})*,\d{2}\s*TL\s*İşlemin.*$", re.IGNORECASE)
# Garanti Bonus writes the plan inline too: "473,00x7=3.311,00 1.Taksit"
INSTALMENT2_RE = re.compile(r"\s*[\d.]+,\d{2}\s*x\s*\d+\s*=.*$", re.IGNORECASE)
TAK_PREFIX_RE = re.compile(r"^\d{2}/\d{2}\s+")          # leading "25/06 "
TAK_MARKER_RE = re.compile(r"\s*\d{1,2}\.Tak\b", re.IGNORECASE)
TERMINAL_RE   = re.compile(r"\s*/\s*[A-Z]?\d{2,}\s*/?\s*[A-Z]{0,4}\b")   # "/ O178/ GOK"
LEADING_CODE_RE = re.compile(r"^(?:\d{4}[-\s]){1,3}[A-Z]?\d*\s*")        # "9935-8847-A101 "


def clean_merchant(raw: str) -> str:
    """Reduce a statement description to something recognisable as a merchant."""
    s = INSTALMENT_RE.sub("", raw)      # drop "3.120,86 TL İşlemin 2/3 Taksidi"
    s = INSTALMENT2_RE.sub("", s)       # drop "473,00x7=3.311,00 1.Taksit"
    s = TAK_PREFIX_RE.sub("", s)        # drop the leading "25/06 "
    s = TAK_MARKER_RE.sub("", s)        # drop "02.Tak"
    s = TERMINAL_RE.sub("", s)          # drop "/ O178/ GOK"
    s = LEADING_CODE_RE.sub("", s)      # drop "9935-8847-A101 "
    s = re.sub(r"\s{2,}", " ", s).strip(" -/")

    # Drop a trailing city token (possibly repeated: "KOCAELİ KOCAELİ")
    parts = s.split()
    while len(parts) > 1 and norm(parts[-1]) in CITIES:
        parts.pop()
    s = " ".join(parts).strip()
    return s or raw.strip()


def categorize(merchant: str) -> str:
    folded = norm(merchant)
    for needle, category in CATEGORY_RULES:
        if needle in folded:
            return category
    return "Unknown"



def split_glued(text: str) -> str:
    """'MoneyTransfer,Sender:TALHAAÇIK' -> 'Money Transfer, Sender: TALHA AÇIK'."""
    s = re.sub(r"([a-zçğıöşü])([A-ZÇĞİÖŞÜ])", r"\1 \2", text)
    s = re.sub(r"([,:])(?=\S)", r"\1 ", s)
    return re.sub(r"\s{2,}", " ", s).strip()


# ── Instalments and counterparties ───────────────────────────────────────────

# Ziraat:        "... 3.120,86 TL İşlemin 2/3 Taksidi 1.040,28"
# Garanti Bonus: "... 473,00x7=3.311,00 1.Taksit ..."
ZIRAAT_INST_RE  = re.compile(r"([\d.]+,\d{2})\s*TL\s*İşlemin\s*(\d+)\s*/\s*(\d+)\s*Taksidi", re.IGNORECASE)
GARANTI_INST_RE = re.compile(r"([\d.]+,\d{2})\s*x\s*(\d+)\s*=\s*([\d.]+,\d{2})\s*(\d+)\.\s*Taksit", re.IGNORECASE)


def read_instalment(raw: str) -> Instalment | None:
    m = ZIRAAT_INST_RE.search(raw)
    if m:
        return Instalment(number=int(m.group(2)), total=int(m.group(3)),
                          plan_total=parse_amount(m.group(1)))
    m = GARANTI_INST_RE.search(raw)
    if m:
        return Instalment(number=int(m.group(4)), total=int(m.group(2)),
                          plan_total=parse_amount(m.group(3)))
    return None


# "Gönderen:MUSTAFAAÇIK,Alıcı:talhaaçık" / "Sender:TALHAAÇIK,Recipient:SELMANAYDIN"
SENDER_RE    = re.compile(r"(?:Gönderen|Sender)\s*:\s*([^,]+)", re.IGNORECASE)
RECIPIENT_RE = re.compile(r"(?:Alıcı|Recipient)\s*:\s*([^,]+)", re.IGNORECASE)

# The statement writes the account holder's own name in several spellings
# (TALHAAÇIK / TalhaAçık / TALHAACIK / talhaaçık), so compare on a key with the
# spaces removed and the Turkish letters folded.
def person_key(name: str) -> str:
    return re.sub(r"[^a-z0-9]", "", norm(name))


# ACCOUNT_HOLDER_KEYS now comes from the profile (see below).


def read_counterparty(raw: str) -> tuple[str, str]:
    """Return (key, label) for the other party on a transfer."""
    sender = (SENDER_RE.search(raw) or [None, ""])[1].strip() if SENDER_RE.search(raw) else ""
    recipient = (RECIPIENT_RE.search(raw) or [None, ""])[1].strip() if RECIPIENT_RE.search(raw) else ""
    for candidate in (sender, recipient):
        if not candidate:
            continue
        key = person_key(candidate)
        if key and key not in ACCOUNT_HOLDER_KEYS:
            return key, split_glued(candidate).strip()
    # A transfer between the holder's own accounts
    if sender or recipient:
        return "self", "Own account"
    return "", ""


# ── PDF loading ───────────────────────────────────────────────────────────────

def load_pdf(pdf_path: Path, password: str | None) -> bytes:
    try:
        with pikepdf.open(pdf_path):
            return pdf_path.read_bytes()
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
            with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
                tmp_path = Path(tmp.name)
            pdf.save(tmp_path)
        try:
            return tmp_path.read_bytes()
        finally:
            tmp_path.unlink(missing_ok=True)
    except pikepdf.PasswordError:
        sys.exit("Wrong password for that PDF.")


def unique_pages(pdf: pdfplumber.PDF) -> list:
    """Ziraat ships the same page twice (customer copy). Without this every
    transaction would be counted once per duplicate."""
    seen: set[str] = set()
    out = []
    for page in pdf.pages:
        digest = hashlib.sha256((page.extract_text() or "").encode()).hexdigest()
        if digest not in seen:
            seen.add(digest)
            out.append(page)
    return out


# ── Ziraat Bankkart template ──────────────────────────────────────────────────
#
# Layout facts this relies on, all verified against a real statement:
#
#   * Column right-edges: TL Tutar x1≈460, USD Tutar x1≈517, Bankkart Lira
#     x1≈577. Amounts are matched by x-position, NOT by order on the line —
#     "MEDİA MARKT  1.234,56  7,89" has its second figure in the Bankkart Lira
#     (loyalty points) column, not USD, and must not be read as money.
#   * Installment rows carry two TL figures: the original total inside the
#     description (x1≈295) and the amount charged this period in the TL column
#     (x1≈460). Only the latter is a real charge.
#   * A trailing '+' marks a credit (a payment onto the card).
#   * The transaction block starts after the "İşlem Tarihi" header row and ends
#     at "Faiz ve Ücretler:".
#   * "ÖNCEKİ AYDAN DEVİR" (previous balance) and "KART NO :" are structure,
#     not transactions.

TL_COLUMN = (430.0, 475.0)      # x1 range that counts as the TL Tutar column
# Markers are written in the ASCII form norm() produces, not as they appear.
FOOTER_MARKER = "faiz ve ucretler"
HEADER_MARKER = "islem tarihi"
SKIP_LINES = ("onceki aydan devir", "kart no :")


def group_rows(page) -> list[list[dict]]:
    """Bucket words into visual rows by their vertical position."""
    rows: dict[int, list[dict]] = {}
    for w in page.extract_words():
        rows.setdefault(round(w["top"]), []).append(w)
    return [sorted(rows[k], key=lambda w: w["x0"]) for k in sorted(rows)]


def ziraat_bankkart(pages: list) -> tuple[list[Transaction], dict]:
    transactions: list[Transaction] = []
    totals: dict = {}
    statement_date: str | None = None

    for page in pages:
        text = page.extract_text() or ""

        # "Hesap Kesim Tarihi : 03/08/2026" — the statement's closing date
        if statement_date is None:
            m = re.search(r"Hesap Kesim Tarihi\s*:\s*(\d{2})/(\d{2})/(\d{4})", text)
            if m:
                d, mo, y = m.groups()
                statement_date = f"{y}-{mo}-{d}"

        # Footer summary, used to check the parse:
        # Devreden Bakiye + Harcamalarınız + Faiz/Kesintiler - Ödemeleriniz = Dönem Borcu
        summary = re.search(
            r"([\d.]+,\d{2})\s*TL\s*\+\s*([\d.]+,\d{2})\s*TL\s*\+\s*([\d.]+,\d{2})\s*TL"
            r"\s*-\s*([\d.]+,\d{2})\s*TL\s*=\s*([\d.]+,\d{2})\s*TL", text)
        if summary and not totals:
            carried, spend, fees, paid, due = (parse_amount(g) for g in summary.groups())
            totals = {"carried": carried, "spend": spend, "fees": fees,
                      "paid": paid, "due": due}

        in_block = False
        for row in group_rows(page):
            joined = " ".join(w["text"] for w in row)
            low = norm(joined)

            if HEADER_MARKER in low:
                in_block = True
                continue
            if FOOTER_MARKER in low:
                in_block = False
                continue
            if not in_block or any(s in low for s in SKIP_LINES):
                continue

            date_m = DATE_RE.match(row[0]["text"])
            if not date_m:
                continue
            d, mo, y = date_m.groups()

            # The charge is the amount sitting in the TL Tutar column.
            tl = [w for w in row
                  if AMOUNT_RE.match(w["text"]) and TL_COLUMN[0] <= w["x1"] <= TL_COLUMN[1]]
            if not tl:
                continue
            amount_word = tl[-1]["text"]

            # Description = everything between the date and the TL column.
            desc_words = [w["text"] for w in row[1:] if w["x1"] < TL_COLUMN[0]]
            raw_desc = " ".join(desc_words).strip()
            if not raw_desc:
                continue
            # Categorise on the raw text (it still holds useful keywords) but
            # display the cleaned name.
            merchant = clean_merchant(raw_desc)
            category = categorize(raw_desc)

            is_credit = amount_word.endswith("+")
            value = parse_amount(amount_word)
            transactions.append(Transaction(
                date=f"{y}-{mo}-{d}",
                merchant=merchant,
                amount=value if is_credit else -value,
                type="income" if is_credit else "expense",
                category=category,
                instalment=read_instalment(raw_desc),
            ))

    if statement_date is None:
        sys.exit("Could not find 'Hesap Kesim Tarihi' — is this a Ziraat Bankkart statement?")

    return transactions, {"totals": totals, "statement_date": statement_date}


# ── Garanti BBVA — account activity (Hesap Hareketleri) ──────────────────────
#
# The only one of these layouts with ruled table lines, so pdfplumber's own
# table extraction handles it. Columns: Tarih | Açıklama | Etiket | Tutar | Bakiye.
# Amounts carry an explicit sign prefix ("-1.234,56 TL" / "+1.234,56 TL"), and
# the Etiket column already names the kind of movement, which makes a better
# category signal than the free-text description.

GARANTI_LABEL_CATEGORY = {
    "para cekme":      "Unknown",        # ATM cash — the statement can't say what it bought
    "kart odemesi":    "Fixed Costs",    # paying off a credit card
    "para transferi":  None,             # decided by sign below
    "havale":          None,
    "eft":             None,
    "otomatik odeme":  "Fixed Costs",
    "fatura":          "Fixed Costs",
    "komisyon":        "Fixed Costs",
    "masraf":          "Fixed Costs",
}


def garanti_hesap(pages) -> tuple[list[Transaction], dict]:
    transactions: list[Transaction] = []
    period_end = None
    period_start = None
    stated_count = None
    chain: list[tuple[float | None, float]] = []   # (printed Bakiye, its amount)

    for page in pages:
        text = page.extract_text() or ""

        m = re.search(r"(\d{2})/(\d{2})/(\d{4})\s*-\s*(\d{2})/(\d{2})/(\d{4})", text)
        if m and period_end is None:
            period_end = f"{m.group(6)}-{m.group(5)}-{m.group(4)}"
            period_start = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"

        m = re.search(r"(\d+)\s+kayıt bulunmuştur", text)
        if m and stated_count is None:
            stated_count = int(m.group(1))

        for table in page.extract_tables():
            for row in table:
                cells = [(c or "").replace("\n", " ").strip() for c in row]
                if len(cells) < 5:
                    continue
                date_m = re.match(r"^(\d{2})\.(\d{2})\.(\d{4})$", cells[0])
                if not date_m:
                    continue           # header row or a spanned line
                d, mo, y = date_m.groups()

                amount_m = re.match(r"^([-+])\s*([\d.]+,\d{2})\s*TL$", cells[3])
                if not amount_m:
                    continue
                sign, raw = amount_m.groups()
                value = parse_amount(raw)
                is_credit = sign == "+"

                # The Bakiye column gives this template the same row-by-row
                # proof the Kuveyt one has: the row count only says how many
                # rows there are, not that any of their amounts were read right.
                balance_m = re.match(r"^([-+]?)\s*([\d.]+,\d{2})\s*TL$", cells[4])
                chain.append((
                    (-1 if balance_m.group(1) == "-" else 1) * parse_amount(balance_m.group(2))
                    if balance_m else None,
                    value if is_credit else -value,
                ))

                desc = cells[1]
                label = norm(cells[2])
                category = None
                for key, cat in GARANTI_LABEL_CATEGORY.items():
                    if key in label:
                        category = cat
                        break
                if category is None:
                    category = "Income" if is_credit else categorize(desc)

                key, label = read_counterparty(desc)
                if not key:
                    # "TALHA AÇIK-FAST-1234567" — the name sits before the tag
                    m2 = re.match(r"^([^\-]+?)-(?:FAST|EFT|HAVALE)", desc, re.IGNORECASE)
                    if m2 and person_key(m2.group(1)) not in ACCOUNT_HOLDER_KEYS:
                        key, label = person_key(m2.group(1)), m2.group(1).strip()
                    elif m2:
                        key, label = "self", "Own account"

                transactions.append(Transaction(
                    date=f"{y}-{mo}-{d}",
                    merchant=(label if key and key != "self" else clean_merchant(desc)),
                    amount=value if is_credit else -value,
                    type="income" if is_credit else "expense",
                    category=category,
                    counterparty=key,
                    counterparty_label=label,
                ))

    if period_end is None:
        period_end = max((t.date for t in transactions), default=None)
    if period_end is None:
        sys.exit("Could not determine the statement period.")

    # Printed newest-first, so reverse it into the oldest-first order verify()
    # walks, and prepend the balance the month opened at.
    chain.reverse()
    opening = (chain[0][0] - chain[0][1]) if chain and chain[0][0] is not None else None
    if opening is not None:
        chain.insert(0, (opening, None))

    return transactions, {"totals": {}, "statement_date": period_end,
                          "period_start": period_start,
                          "stated_count": stated_count,
                          "chain": chain,
                          "opening_balance": opening,
                          "closing_balance": chain[-1][0] if chain else None}


# ── Garanti BBVA — Bonus credit card statement ───────────────────────────────
#
# Text-positioned like the Ziraat card, and it carries the same trap: a Bonus
# (loyalty points) column sits left of the real Tutar column. Reading amounts by
# order instead of by x-position would book loyalty points as money.
#   Bonus (TL) right edge ≈ 445,  Tutar (TL) right edge ≈ 553.
# Dates are Turkish month names ("17 Temmuz 2026"), not dd/mm/yyyy.

TR_MONTHS = {
    "ocak": 1, "şubat": 2, "subat": 2, "mart": 3, "nisan": 4, "mayıs": 5, "mayis": 5,
    "haziran": 6, "temmuz": 7, "ağustos": 8, "agustos": 8, "eylül": 9, "eylul": 9,
    "ekim": 10, "kasım": 11, "kasim": 11, "aralık": 12, "aralik": 12,
}
TR_DATE_RE = re.compile(r"^(\d{1,2})\s+([A-Za-zÇĞİÖŞÜçğıöşü]+)\s+(\d{4})$")

BONUS_TUTAR_COLUMN = (520.0, 575.0)
BONUS_SKIP = ("onceki donemden devir", "bonus program ortaklari", "toplam",
              "ekstre ozeti", "bosluk")


def _tr_date(day: str, month_word: str, year: str) -> str | None:
    mo = TR_MONTHS.get(norm(month_word))
    return f"{year}-{mo:02d}-{int(day):02d}" if mo else None


def garanti_bonus(pages) -> tuple[list[Transaction], dict]:
    transactions: list[Transaction] = []
    totals: dict = {}
    statement_date = None

    for page in pages:
        text = page.extract_text() or ""

        if statement_date is None:
            m = re.search(r"Hesap Kesim Tarihi\s+(\d{1,2})\s+(\S+)\s+(\d{4})", text)
            if m:
                statement_date = _tr_date(*m.groups())

        # Önceki Bakiye + Dönem Harcamaları + Faiz ve Ücretler - Ödemeleriniz = Dönem Borcunuz
        m = re.search(
            r"([\d.]+,\d{2})\s*TL\s*\+\s*([\d.]+,\d{2})\s*TL\s*\+\s*([\d.]+,\d{2})\s*TL"
            r"\s*-\s*([\d.]+,\d{2})\s*TL\s*=\s*([\d.]+,\d{2})\s*TL", text)
        if m and not totals:
            carried, spend, fees, paid, due = (parse_amount(g) for g in m.groups())
            totals = {"carried": carried, "spend": spend, "fees": fees,
                      "paid": paid, "due": due}

        for row in group_rows(page):
            joined = " ".join(w["text"] for w in row)
            low = norm(joined)
            if any(s in low for s in BONUS_SKIP):
                continue

            # "17 Temmuz 2026 ..." — the date is the first three tokens
            if len(row) < 4:
                continue
            iso = None
            m = TR_DATE_RE.match(" ".join(w["text"] for w in row[:3]))
            if m:
                iso = _tr_date(*m.groups())
            if not iso:
                continue

            tutar = [w for w in row
                     if AMOUNT_RE.match(w["text"])
                     and BONUS_TUTAR_COLUMN[0] <= w["x1"] <= BONUS_TUTAR_COLUMN[1]]
            if not tutar:
                continue
            token = tutar[-1]["text"]

            desc_words = [w["text"] for w in row[3:] if w["x1"] < BONUS_TUTAR_COLUMN[0] - 80]
            raw_desc = " ".join(desc_words).strip()
            if not raw_desc:
                continue

            is_credit = token.endswith("+")
            value = parse_amount(token)
            transactions.append(Transaction(
                date=iso,
                merchant=clean_merchant(raw_desc),
                amount=value if is_credit else -value,
                type="income" if is_credit else "expense",
                category="Income" if is_credit else categorize(raw_desc),
                instalment=read_instalment(raw_desc),
            ))

    if statement_date is None:
        sys.exit("Could not find 'Hesap Kesim Tarihi' — is this a Garanti Bonus statement?")
    return transactions, {"totals": totals, "statement_date": statement_date}


# ── Kuveyt Türk — account statement ──────────────────────────────────────────
#
# English column headers, and the PDF carries no space characters at all —
# "MoneyTransfer,Sender:TALHAAÇIK,Recipient:..." is one token. Descriptions are
# therefore glued; split_glued() re-inserts spaces at case boundaries so the
# merchant is at least readable. Rows also wrap across several lines, with the
# date only on the first.
#   Amount right edge ≈ 550,  Balance right edge ≈ 590.

KT_AMOUNT_COLUMN = (525.0, 558.0)
KT_DATE_RE = re.compile(r"^(\d{2})\.(\d{2})\.(\d{4})$")
# A wrapped line sits ~6pt under the line it belongs to; the next transaction
# starts ~25pt down. Anything past this gap starts a new row.
KT_WRAP_GAP = 12.0


def _kt_amount_tokens(page) -> list[dict]:
    """Every amount-column token on the page, sign folded back on.

    The Tutar cell is narrow and right-aligned, so a wide amount wraps *inside
    the cell*: the minus goes on one line and the digits on the next, both
    flush to the same right edge, and neither one sits on the dated line. A
    ₺10.000 transfer went missing out of the July statement exactly this way —
    the dated row carried no amount at all, so it was dropped without a word.
    Amounts are therefore collected per page and claimed by proximity, the
    same rule KT_WRAP_GAP already applies to descriptions.
    """
    words = [w for w in page.extract_words()
             if KT_AMOUNT_COLUMN[0] <= w["x1"] <= KT_AMOUNT_COLUMN[1]]
    signs = [w for w in words if w["text"] == "-"]
    out = []
    for w in words:
        if not AMOUNT_RE.match(w["text"].lstrip("-")):
            continue
        text = w["text"]
        if not text.startswith("-"):
            for s in signs:
                # same right edge, printed on the line just above the digits
                if abs(s["x1"] - w["x1"]) <= 1.5 and 0 < w["top"] - s["top"] <= KT_WRAP_GAP:
                    text = "-" + text
                    break
        out.append({"top": w["top"], "x0": w["x0"], "text": text})
    return sorted(out, key=lambda a: (a["top"], a["x0"]))


KT_BALANCE_COLUMN = (560.0, 600.0)


def _kt_balance_chain(page, amt_tokens: list[dict]) -> list[tuple[float, float | None]]:
    """Pair each printed Bakiye with the amount on its line.

    The totals footer only proves the *columns* add up — a row read wrong and a
    second row read wrong the other way still sum correctly, and a row dropped
    from the middle is invisible if it never reached either total. The running
    balance proves every row individually: each one must be the balance above it
    plus this row's amount, so a single wrong digit breaks the chain right there.
    """
    out = []
    balances = [w for w in page.extract_words()
                if KT_BALANCE_COLUMN[0] <= w["x1"] <= KT_BALANCE_COLUMN[1]
                and AMOUNT_RE.match(w["text"].lstrip("-"))]
    for b in sorted(balances, key=lambda w: w["top"]):
        near = sorted((a for a in amt_tokens if abs(a["top"] - b["top"]) <= KT_WRAP_GAP),
                      key=lambda a: abs(a["top"] - b["top"]))
        out.append((_signed(b["text"]),
                    _signed(near[0]["text"]) if near else None))
    return out


def _signed(token: str) -> float:
    value = parse_amount(token.lstrip("-"))
    return -value if token.startswith("-") else value


def kuveyt_turk(pages) -> tuple[list[Transaction], dict]:
    transactions: list[Transaction] = []
    totals: dict = {}
    period_end = None
    period_start = None
    chain: list[tuple[float, float | None]] = []   # (printed balance, its amount)

    for page in pages:
        text = page.extract_text() or ""

        if period_end is None:
            m = re.search(r"Period:\s*(\d{2})\.(\d{2})\.(\d{4})\s*[–\-]\s*(\d{2})\.(\d{2})\.(\d{4})", text)
            if m:
                period_end = f"{m.group(6)}-{m.group(5)}-{m.group(4)}"
                # The statement says where it begins. Deriving that from the
                # earliest row instead makes a quiet month look like a gap in
                # coverage, which is the one thing the coverage report exists
                # to detect.
                period_start = f"{m.group(3)}-{m.group(2)}-{m.group(1)}"

        if not totals:
            w = re.search(r"TotalWithdrawals:?\s*([\d.]+,\d{2})", text)
            d = re.search(r"TotalDeposits:?\s*([\d.]+,\d{2})", text)
            if w and d:
                totals = {"spend": parse_amount(w.group(1)),
                          "paid": parse_amount(d.group(1))}

        pending = None     # dated row still collecting its trailing lines
        pending_top = 0.0  # bottom-most line already attached to it
        orphans: list[tuple[float, str]] = []   # (top, text) seen before a dated row
        amt_tokens = _kt_amount_tokens(page)
        claimed: set[int] = set()
        chain.extend(_kt_balance_chain(page, amt_tokens))

        for row in group_rows(page):
            first = row[0]["text"]
            date_m = KT_DATE_RE.match(first)
            top = min(w["top"] for w in row)

            row_tops = {w["top"] for w in row}
            amounts = [i for i, a in enumerate(amt_tokens)
                       if i not in claimed and a["top"] in row_tops]

            if not date_m:
                text = " ".join(w["text"] for w in row if w["x1"] < KT_AMOUNT_COLUMN[0]).strip()
                if not text:
                    continue
                # A wrapped line either continues the row above or introduces
                # the next one. Vertical distance is what tells them apart: a
                # continuation hugs the line it belongs to, while a description
                # printed *above* its own dated line — how the Midas transfer is
                # laid out — sits a full row away from the row above it.
                if pending is not None and not orphans and top - pending_top <= KT_WRAP_GAP:
                    pending["desc"] += " " + text
                    pending_top = top
                else:
                    orphans.append((top, text))
                continue

            # A dated line closes whatever came before it.
            if pending is not None:
                _emit_kt(transactions, pending)
                pending = None

            if not amounts:
                # The amount wrapped out of the dated line — claim the closest
                # unclaimed one within the same gap that binds a description.
                near = sorted(
                    (i for i, a in enumerate(amt_tokens)
                     if i not in claimed and abs(a["top"] - top) <= KT_WRAP_GAP),
                    key=lambda i: abs(amt_tokens[i]["top"] - top))
                if not near:
                    orphans.clear()
                    continue
                amounts = near[:1]

            d_, mo_, y_ = date_m.groups()
            claimed.update(amounts)
            token = amt_tokens[amounts[-1]]["text"]
            own = " ".join(w["text"] for w in row[2:] if w["x1"] < KT_AMOUNT_COLUMN[0])
            # Only the orphans immediately above belong to this row; the page
            # header sits a whole block higher and is not a description.
            above, reach = [], top
            for o_top, o_text in reversed(orphans):
                if reach - o_top > KT_WRAP_GAP:
                    break
                above.append(o_text)
                reach = o_top
            above.reverse()
            desc = " ".join([*above, own]).strip()
            orphans.clear()
            pending = {"date": f"{y_}-{mo_}-{d_}", "desc": desc, "token": token}
            pending_top = top

        if pending is not None:
            _emit_kt(transactions, pending)

    if period_end is None:
        period_end = max((t.date for t in transactions), default=None)
    if period_end is None:
        sys.exit("Could not read the statement Period.")
    # The last balance is printed again in a closing-balance box that carries no
    # amount of its own; keep it as the closing figure but drop it from the
    # chain, so every template hands verify() the same shape:
    # [opening (no amount), row, row, ...].
    closing = chain[-1][0] if chain else None
    if len(chain) >= 2 and chain[-1][1] is None:
        chain.pop()

    return transactions, {
        "totals": totals,
        "statement_date": period_end,
        "period_start": period_start,
        "chain": chain,
        "opening_balance": chain[0][0] if chain else None,
        "closing_balance": closing,
    }


# Kuveyt Türk never writes a plain merchant name. A card purchase is a
# comma-separated technical record whose *last* field is the shop; a POS
# transfer hides the shop behind "FirmaAdı:"; an ATM behind "ATMName:". The
# city is glued onto the end of the name because the PDF has no spaces.
KT_FIRM_RE = re.compile(r"Firma\s*Adı\s*:\s*([^,]+)", re.IGNORECASE)
KT_ATM_RE  = re.compile(r"ATM\s*Name\s*:\s*([^,]+)", re.IGNORECASE)


def strip_glued_city(name: str) -> str:
    """'BETROBURGERISTANBUL' -> 'BETROBURGER'."""
    folded = norm(name)
    for city in sorted(CITIES, key=len, reverse=True):
        if folded.endswith(city) and len(folded) > len(city) + 2:
            return name[: len(name) - len(city)].rstrip(" .,-/")
    return name


def kuveyt_merchant(raw: str) -> str:
    m = KT_FIRM_RE.search(raw)
    if m:
        return strip_glued_city(m.group(1).strip())
    m = KT_ATM_RE.search(raw)
    if m:
        return "ATM " + strip_glued_city(m.group(1).strip())
    if "*" in raw and re.search(r"\d{6}\*+\d{4}", raw):
        # masked card record: the shop is the last comma-separated field
        return strip_glued_city(raw.rsplit(",", 1)[-1].strip())
    return clean_merchant(split_glued(raw))


def _emit_kt(out: list, row: dict) -> None:
    token = row["token"]
    negative = token.startswith("-")
    value = parse_amount(token.lstrip("-"))
    raw = row["desc"]
    desc = split_glued(raw)
    key, label = read_counterparty(raw)

    # For a person-to-person transfer the counterparty *is* the merchant — the
    # rest of the line is boilerplate ("FAST Para Transferi, Ödeme Türü: ...").
    merchant = label if key and key != "self" else kuveyt_merchant(raw)[:80]

    out.append(Transaction(
        date=row["date"],
        merchant=merchant or "Transfer",
        amount=-value if negative else value,
        type="expense" if negative else "income",
        category=categorize(desc) if negative else "Income",
        counterparty=key,
        counterparty_label=label,
    ))


TEMPLATES = [
    # (name, detector, parser) — first match wins, so put the specific
    # detectors above the general ones.
    ("Ziraat Bankası (Bankkart)",
     lambda t: "Bankkart" in t and "Hesap Kesim Tarihi" in t,
     ziraat_bankkart),

    ("Garanti BBVA (Bonus kredi kartı)",
     lambda t: "bonus" in norm(t) and "hesap kesim tarihi" in norm(t) and "garanti" in norm(t),
     garanti_bonus),

    ("Garanti BBVA (hesap hareketleri)",
     lambda t: "garanti" in norm(t) and "hesap hareketleri" in norm(t),
     garanti_hesap),

    ("Kuveyt Türk (hesap ekstresi)",
     lambda t: "kuveytturk" in norm(t).replace(" ", "") or "ACCOUNT STATEMENT" in t.upper(),
     kuveyt_turk),
]


def detect_and_parse(pages: list) -> tuple[str, list[Transaction], dict]:
    first = pages[0].extract_text() or ""

    if not first.strip() and not pages[0].extract_words():
        sys.exit(
            "This PDF has no text layer — it is a scan or a photo, so there is "
            "nothing to read out of it. Re-download the statement from your "
            "bank as a normal PDF, or run it through OCR first."
        )

    for name, detects, parser in TEMPLATES:
        if detects(first):
            txns, meta = parser(pages)
            meta.setdefault("due_day", read_due_day(first))
            return name, txns, meta
    sys.exit(
        "No template matches this statement. Run tools/inspect_pdf.py on it and "
        "send the output to Claude to have a template added."
    )


# ── Verification ──────────────────────────────────────────────────────────────

# ── Economic rules ────────────────────────────────────────────────────────────
# What a bank prints is not what the month earned or cost. A credit-card
# payment, a transfer between the holder's own accounts and a currency bridge
# all show up as income or spending, and counting them makes both totals wrong.
# These rules were dictated by the account holder; they are written in the
# plain-ASCII form norm() produces.

# Money that really does arrive from outside. Anything else that comes in is
# left uncategorised on purpose, so the dashboard asks instead of assuming.
# Every rule that used to sit here as a literal now lives in tools/rules.py,
# split into generic DEFAULTS and a per-user profile. The lists were true for
# exactly one person ("MUSTAFAAÇIK is my father"), which is fine for a tool on
# one machine and wrong the moment a second person uploads a statement.
# PROFILE_PATH can name any profile; the default is this repo's own owner.
PROFILE_PATH = os.environ.get("AIFP_PROFILE", str(Path(__file__).parent.parent / "profile.json"))
RULES = rules.load(PROFILE_PATH)
ACCOUNT_HOLDER_KEYS = RULES.account_holder_keys


def _hit(haystack: str, rules: list[tuple[str, str]]) -> str:
    for needle, note in rules:
        if needle in haystack:
            return note
    return ""


def apply_rules(transactions: list[Transaction], ruleset=None) -> None:
    """Label each row with what it means economically, in place.

    Runs *after* verify(): the self-check measures the parse against the
    statement's own arithmetic, so it must see the untouched rows.
    """
    # One process serves many people, so the rules cannot be a module global:
    # each call carries the profile of whoever owns this statement.
    rs = ruleset or RULES
    holder_keys = rs.account_holder_keys

    for tx in transactions:
        hay = norm(f"{tx.merchant} {tx.counterparty} {tx.counterparty_label}")

        note = _hit(hay, rs.investment)
        if note:
            tx.flow, tx.flow_note = "investment", note
            tx.category = "Investment"
            continue

        note = _hit(hay, rs.trust)
        if note:
            tx.flow, tx.flow_note = "trust", note
            continue

        note = _hit(hay, rs.passthrough)
        if note:
            tx.flow, tx.flow_note = "passthrough", note
            continue

        note = _hit(hay, rs.internal)
        if note:
            tx.flow, tx.flow_note = "internal", note
            continue

        if tx.counterparty == "self":
            tx.flow = "internal"
            tx.flow_note = "Between my own accounts"
            tx.merchant = "Own account (FAST)"
            continue

        if tx.type == "income":
            note = _hit(hay, rs.regular_income)
            if note:
                tx.category, tx.flow_note = "Income", note
            else:
                # Most inflows are friends paying back a bill I fronted; calling
                # them income inflates the month. Ask instead of guessing.
                tx.category = "Unknown"


# A credit card prints the day its balance must be paid. It is the anchor for
# the whole month — the instalment table shows it, and it is what makes a
# statement cycle line up with real life.
DUE_MONTH_NAMES = ("ocak", "subat", "mart", "nisan", "mayis", "haziran",
                   "temmuz", "agustos", "eylul", "ekim", "kasim", "aralik")
DUE_NUMERIC_RE = re.compile(r"Son\s*Ödeme\s*Tarihi\s*:?\s*(\d{2})/(\d{2})/(\d{4})", re.IGNORECASE)
DUE_WORDS_RE = re.compile(r"Son\s*Ödeme\s*Tarihi\s*:?\s*(\d{1,2})\s+([A-Za-zÇĞİÖŞÜçğıöşü]+)\s+(\d{4})")


def read_due_day(text: str) -> int | None:
    """The day of the month the card has to be paid, from either date format."""
    m = DUE_NUMERIC_RE.search(text)
    if m:
        return int(m.group(1))
    m = DUE_WORDS_RE.search(text)
    if m and norm(m.group(2)) in DUE_MONTH_NAMES:
        return int(m.group(1))
    return None


def verify(transactions: list[Transaction], meta: dict) -> list[str]:
    """A regex parser can go wrong silently, so check the parse against whatever
    the statement asserts about itself: a totals equation where one is printed,
    otherwise the row count."""
    totals = meta.get("totals") or {}
    warnings: list[str] = []

    stated = meta.get("stated_count")
    if stated is not None and stated != len(transactions):
        warnings.append(
            f"Row count: parsed {len(transactions)} but the statement says {stated}"
        )

    # Where the statement prints a running balance, check it row by row. This
    # is strictly stronger than the totals: two compensating misreads still add
    # up, and a row dropped out of the middle never reaches either total at all.
    # The first entry is the opening balance and carries no amount; the last is
    # the closing-balance summary line and carries none either.
    chain = meta.get("chain") or []
    breaks = 0
    for i in range(1, len(chain)):
        previous = chain[i - 1][0]
        balance, amount = chain[i]
        if amount is None or abs(previous + amount - balance) > 0.011:
            breaks += 1
            if breaks <= 3:
                warnings.append(
                    f"Bakiye chain breaks at the row printing {balance:,.2f}: "
                    + (f"{previous:,.2f} + {amount:,.2f} = {previous + amount:,.2f}"
                       if amount is not None else "no amount could be read for it")
                )
    if breaks > 3:
        warnings.append(f"...and {breaks - 3} more balance rows that do not follow on.")

    if not totals:
        if not chain and stated is None:
            warnings.append("Could not read the statement's own summary totals "
                            "— parse unverified.")
        return warnings

    spend = sum(-t.amount for t in transactions if t.type == "expense")
    paid = sum(t.amount for t in transactions if t.type == "income")

    for label, got, expected in (("Harcamalarınız", spend, totals["spend"]),
                                 ("Ödemeleriniz", paid, totals["paid"])):
        if abs(got - expected) > 0.01:
            warnings.append(
                f"{label}: parsed {got:,.2f} but the statement says "
                f"{expected:,.2f} (off by {got - expected:+,.2f})"
            )
    return warnings


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    ap = argparse.ArgumentParser(description="Parse a statement PDF offline into dashboard JSON.")
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--password", help="PDF password (falls back to EKSTRE_PDF_PASSWORD)")
    ap.add_argument("--out", type=Path, help="output path (default: data/<YYYY-MM>.json)")
    ap.add_argument("--show", action="store_true", help="print every parsed transaction")
    args = ap.parse_args()

    if not args.pdf.is_file():
        sys.exit(f"No such file: {args.pdf}")

    data = load_pdf(args.pdf, args.password or os.environ.get("EKSTRE_PDF_PASSWORD"))

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        pages = unique_pages(pdf)
        total_pages = len(pdf.pages)
        bank, transactions, meta = detect_and_parse(pages)

    if total_pages != len(pages):
        print(f"note: {total_pages} pages, {len(pages)} unique "
              f"({total_pages - len(pages)} duplicate skipped)", file=sys.stderr)

    if not transactions:
        sys.exit("Template matched but no transactions were found — the layout may have changed.")

    warnings = verify(transactions, meta)
    apply_rules(transactions)

    # A card statement never prints where its period began, so the earliest row
    # has to stand in for it. An account statement does print it — and using the
    # earliest row there instead makes a quiet fortnight look like missing
    # coverage, so the two cases are marked apart rather than guessed at.
    printed_start = meta.get("period_start")

    # A card has no running balance, but its totals equation carries the same
    # information: Devreden Bakiye is what was owed when the period opened and
    # Dönem Borç what was owed when it closed. Carried as a negative balance, so
    # one continuity check covers cards and accounts alike — this statement must
    # open where the previous one closed, or a statement is missing between them.
    totals = meta.get("totals") or {}
    opening = meta.get("opening_balance")
    closing = meta.get("closing_balance")
    if opening is None and "carried" in totals:
        opening, closing = -totals["carried"], -totals["due"]

    stmt = Statement(
        bank=bank,
        account_mask="",
        due_day=meta.get("due_day"),
        period_start=printed_start or min(t.date for t in transactions),
        period_end=meta["statement_date"],
        period_start_exact=bool(printed_start),
        opening_balance=round(opening, 2) if opening is not None else None,
        closing_balance=round(closing, 2) if closing is not None else None,
        transactions=sorted(transactions, key=lambda t: t.date, reverse=True),
    )
    payload = to_dashboard_json(stmt, source=f"Offline parse — {args.pdf.name}")

    if args.show:
        for t in stmt.transactions:
            print(f"  {t.date}  {t.merchant[:44]:<44} {t.amount:>12,.2f}  {t.category}")

    period = f"{datetime.fromisoformat(stmt.period_end):%Y-%m}"
    out = args.out or Path("data") / f"{period}__{bank_slug(bank)}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    # One file per period+bank, so re-importing the same statement updates it
    # instead of duplicating it. But two *different* statements from one bank
    # can end in the same month — download 1–30 June and then 10–25 June and the
    # second silently replaces the first. Say so rather than losing it quietly.
    replaced = None
    if out.exists():
        try:
            prev = json.loads(out.read_text(encoding="utf-8"))
            replaced = {
                "count": len(prev.get("transactions", [])),
                "start": prev.get("periodStart", "?"),
                "end": prev.get("periodEnd", "?"),
            }
        except Exception:
            replaced = {"count": "?", "start": "?", "end": "?"}

    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    available = write_index(out.parent)

    s = payload["summary"]
    unknown = sum(1 for t in stmt.transactions if t.category == "Unknown")
    print(f"\n{bank} — {payload['statementMonth']}", file=sys.stderr)
    print(f"  {len(stmt.transactions)} transactions "
          f"({unknown} uncategorised)", file=sys.stderr)
    print(f"  spend {s['totalExpense']:,.2f} / payments {s['totalIncome']:,.2f}", file=sys.stderr)

    if replaced:
        same = (replaced["start"] == stmt.period_start
                and replaced["end"] == stmt.period_end)
        note = "note:" if same else "!"
        extra = "" if same else (
            "  <-- A DIFFERENT PERIOD. If both statements matter, import one "
            "with --out under another name.")
        print(f"\n  {note} replaced the existing {period} file "
              f"({replaced['count']} rows, {replaced['start']} -> {replaced['end']})"
              + extra, file=sys.stderr)

    if warnings:
        print("\n  WARNINGS:", file=sys.stderr)
        for w in warnings:
            print(f"    ! {w}", file=sys.stderr)
    else:
        print("  ✓ totals match the statement's own summary", file=sys.stderr)

    print(f"\nWrote {out}", file=sys.stderr)
    print(f"  data/index.json now lists {len(available)} statement file(s)", file=sys.stderr)


if __name__ == "__main__":
    main()
