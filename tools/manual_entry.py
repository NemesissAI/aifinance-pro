"""
Hand-entered statement -> dashboard JSON, for the one case the parsers cannot
help with: a statement that arrives as a scan with no text layer.

Everything else in this project is parsed, because a regex parser that is wrong
is still checkable against the statement's own arithmetic. A transcription gets
exactly the same treatment: the printed Borç/Alacak totals are declared below
and checked before anything is written. If a digit was read wrong, the check
fails and nothing lands in data/.

Add a statement by appending a block to STATEMENTS. Run:

    .venv\\Scripts\\python.exe tools\\manual_entry.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from parse_statement_local import apply_rules  # noqa: E402
from statement_model import (Statement, Transaction, bank_slug,  # noqa: E402
                             to_dashboard_json, write_index)

# Ziraat Bankası vadesiz hesap, 17.07.2026 – 17.08.2026.
# Source: statements/Hesap_Hareketleri_17082026__1_.pdf — a 1024x734 scan, no
# text layer, so pdfplumber sees zero words. Transcribed by reading the page.
#
# Columns: date, description, amount (negative = Borç), counterparty key.
# The counterparty key only matters where a transfer is between the holder's
# own accounts: "self" is what makes the dashboard pair it with the other leg.
ZIRAAT_VADESIZ = {
    "bank": "Ziraat Bankası (vadesiz hesap)",
    "source_pdf": "Hesap_Hareketleri_17082026__1_.pdf",
    "period_start": "2026-07-17",
    "period_end": "2026-08-17",
    # What the statement prints about itself.
    "stated_debit": 18441.89,
    "stated_credit": 19641.89,
    "rows": [
        ("2026-08-12", "Gönd: AYDIN KAYA 0046-Akbank T.A.Ş. FAST işlemi", 1200.00, "aydinkaya", "Aydın Kaya"),
        ("2026-08-10", "KK TAHSİLAT KART NO: 6587 **** **** 7959", -2621.89, "", ""),
        ("2026-08-10", "Gönd: TALHA AÇIK 0205-Kuveyt Türk Katılım Bankası A.Ş. FAST işlemi", 2621.89, "self", "Own account"),
        ("2026-08-10", "KK TAHSİLAT KART NO: 6587 **** **** 7959", -4000.00, "", ""),
        ("2026-08-10", "KREDİ YURTLAR KURUMU ÖĞRENİM BURSU 20264368333 AĞUSTOS 2026", 4000.00, "", ""),
        ("2026-08-04", "KK TAHSİLAT KART NO: 6587 **** **** 7959", -2000.00, "", ""),
        ("2026-08-04", "eğitim yardımı TAHSİN CERAN Ziraat Mobil Havale", 2000.00, "tahsinceran", "Tahsin Ceran"),
        ("2026-08-03", "KK TAHSİLAT KART NO: 6587 **** **** 7959", -3320.00, "", ""),
        ("2026-08-03", "Gönd: FATMA AKBAYRAK Opel motorin 0205-Kuveyt Türk Katılım Bankası A.Ş. FAST işlemi", 3320.00, "fatmaakbayrak", "Fatma Akbayrak"),
        ("2026-07-17", "KK TAHSİLAT KART NO: 6587 **** **** 7959", -1200.00, "", ""),
        ("2026-07-17", "Gönd: AYDIN KAYA 0046-Akbank T.A.Ş. FAST işlemi", 1200.00, "aydinkaya", "Aydın Kaya"),
        ("2026-07-17", "KK TAHSİLAT KART NO: 6587 **** **** 7959", -5300.00, "", ""),
        ("2026-07-17", "Gönd: AYDIN KAYA 0046-Akbank T.A.Ş. FAST işlemi", 5300.00, "aydinkaya", "Aydın Kaya"),
    ],
    # The printed Bakiye after each row, in the same order. Every transfer in
    # is spent on the card the same day, so this account sits at zero most of
    # the month — which is exactly why a missed row would be easy to overlook.
    "balances": [1200.00, 0.00, 2621.89, 0.00, 4000.00, 0.00, 2000.00,
                 0.00, 3320.00, 0.00, 1200.00, 0.00, 5300.00],
}


# Ziraat Bankası vadesiz hesap, 16.06.2026 – 16.07.2026 — the month before.
# Source: statements/Hesap_Hareketleri_16072026.pdf, a scan like the other one.
# This account is where both cards get paid from, so leaving July out left the
# counter-leg of a ₺10.000 and a ₺7.541,47 card payment missing, and the KYK
# bursu and the Tahsin Ceran transfer with it.
ZIRAAT_VADESIZ_TEMMUZ = {
    "bank": "Ziraat Bankası (vadesiz hesap)",
    "source_pdf": "Hesap_Hareketleri_16072026.pdf",
    "period_start": "2026-06-16",
    "period_end": "2026-07-16",
    "stated_debit": 17541.47,
    "stated_credit": 17441.36,
    "rows": [
        ("2026-07-13", "KK TAHSİLAT KART NO: 6587 **** **** 7959 FİŞ NO:0005116", -7541.47, "", ""),
        ("2026-07-13", "Gönd: TALHA AÇIK 0205-Kuveyt Türk Katılım Bankası A.Ş. FAST işlemi", 1441.36, "self", "Own account"),
        ("2026-07-10", "KREDİ YURTLAR KURUMU ÖĞRENİM BURSU 20264368333 TEMMUZ 2026", 4000.00, "", ""),
        ("2026-07-06", "eğitim yardımı TAHSİN CERAN Ziraat Mobil Havale", 2000.00, "tahsinceran", "Tahsin Ceran"),
        ("2026-07-01", "KK TAHSİLAT KART NO: 6587 **** **** 7959 FİŞ NO:0000596", -10000.00, "", ""),
        ("2026-07-01", "Gönd: TALHA AÇIK 0205-Kuveyt Türk Katılım Bankası A.Ş. FAST işlemi", 10000.00, "self", "Own account"),
    ],
    # The statement prints a running Bakiye, so the transcription can be checked
    # a second way: every row's balance must equal the one before it plus the
    # amount. A digit read wrong in one row breaks the chain from there down.
    "balances": [0.00, 7541.47, 6100.11, 2100.11, 100.11, 10100.11],
}

STATEMENTS = [ZIRAAT_VADESIZ_TEMMUZ, ZIRAAT_VADESIZ]


def build(spec: dict) -> Statement:
    transactions = [
        Transaction(
            date=date,
            merchant=desc[:80],
            amount=amount,
            type="income" if amount > 0 else "expense",
            category="Unknown",
            counterparty=key,
            counterparty_label=label,
        )
        for date, desc, amount, key, label in spec["rows"]
    ]

    debit = sum(-t.amount for t in transactions if t.amount < 0)
    credit = sum(t.amount for t in transactions if t.amount > 0)
    for name, got, expected in (("Borç", debit, spec["stated_debit"]),
                                ("Alacak", credit, spec["stated_credit"])):
        if abs(got - expected) > 0.01:
            sys.exit(f"{spec['bank']}: {name} transcribed {got:,.2f} but the "
                     f"statement says {expected:,.2f} — a figure was read wrong.")

    # Second, independent check where the statement prints a running Bakiye.
    # The totals above only prove the column adds up; the balance chain proves
    # each individual row, because one wrong digit breaks it at that row. Rows
    # stay in printed order here — newest first, so each balance is the next
    # one down plus this row's amount.
    balances = spec.get("balances")
    if balances:
        if len(balances) != len(spec["rows"]):
            sys.exit(f"{spec['bank']}: {len(balances)} balances for "
                     f"{len(spec['rows'])} rows.")
        for i in range(len(balances) - 1):
            date, _desc, amount, *_ = spec["rows"][i]
            want = balances[i + 1] + amount
            if abs(want - balances[i]) > 0.01:
                sys.exit(f"{spec['bank']}: the Bakiye chain breaks at {date} "
                         f"({amount:,.2f}) — {balances[i + 1]:,.2f} + "
                         f"{amount:,.2f} = {want:,.2f}, but the statement "
                         f"prints {balances[i]:,.2f}.")

    apply_rules(transactions)
    return Statement(
        bank=spec["bank"],
        account_mask="",
        period_start=spec["period_start"],
        period_end=spec["period_end"],
        # Both dates are read off the statement's own Dönem line.
        period_start_exact=True,
        # Printed newest-first, so the last balance is where the month opened
        # (before its own amount) and the first is where it closed.
        opening_balance=round(balances[-1] - spec["rows"][-1][2], 2) if balances else None,
        closing_balance=balances[0] if balances else None,
        transactions=sorted(transactions, key=lambda t: t.date, reverse=True),
    )


def main() -> None:
    data_dir = Path("data")
    data_dir.mkdir(exist_ok=True)

    for spec in STATEMENTS:
        stmt = build(spec)
        payload = to_dashboard_json(
            stmt, source=f"Hand-entered — {spec['source_pdf']} (scan, no text layer)")
        period = stmt.period_end[:7]
        out = data_dir / f"{period}__{bank_slug(stmt.bank)}.json"
        out.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

        s = payload["summary"]
        print(f"\n{stmt.bank} — {payload['statementMonth']}", file=sys.stderr)
        print(f"  {len(stmt.transactions)} transactions", file=sys.stderr)
        print(f"  Borç {s['totalExpense']:,.2f} / Alacak {s['totalIncome']:,.2f}", file=sys.stderr)
        print("  ✓ matches the totals printed on the statement", file=sys.stderr)
        print(f"  real income {s['realIncome']:,.2f} / real expense {s['realExpense']:,.2f}",
              file=sys.stderr)
        print(f"\nWrote {out}", file=sys.stderr)

    available = write_index(data_dir)
    print(f"  data/index.json now lists {len(available)} statement file(s)", file=sys.stderr)


if __name__ == "__main__":
    main()
