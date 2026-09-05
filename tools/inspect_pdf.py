"""
Show the *structure* of a bank statement PDF, without exposing its contents.

Writing a deterministic parser means knowing how the bank lays out its rows:
which columns exist, in what order, how dates and amounts are formatted, where
the transaction block starts and ends. None of that requires seeing your actual
transactions.

So by default every digit is masked to '#'. "15.08.2026  MIGROS  -412,35"
becomes "##.##.####  MIGROS  -###,##" — the shape is intact, the data is not.
Merchant names stay visible because the parser needs to recognise header and
footer lines; pass --mask-letters if you'd rather hide those too.

Usage:
    .venv\\Scripts\\python.exe tools\\inspect_pdf.py statements\\ekstre.pdf
    .venv\\Scripts\\python.exe tools\\inspect_pdf.py statements\\ekstre.pdf --password 12345678901
    .venv\\Scripts\\python.exe tools\\inspect_pdf.py statements\\ekstre.pdf --raw   # no masking

Send the output to Claude to have the parser template written from it.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import sys
import tempfile
from pathlib import Path

import pdfplumber
import pikepdf

DIGITS = re.compile(r"\d")
LETTERS = re.compile(r"[^\W\d_]", re.UNICODE)


def mask(text: str, *, raw: bool, mask_letters: bool) -> str:
    if raw:
        return text
    out = DIGITS.sub("#", text)
    if mask_letters:
        out = LETTERS.sub("x", out)
    return out


def load(pdf_path: Path, password: str | None) -> bytes:
    """Return PDF bytes, decrypting first if needed."""
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


def main() -> None:
    ap = argparse.ArgumentParser(description="Dump a statement PDF's structure, digits masked.")
    ap.add_argument("pdf", type=Path)
    ap.add_argument("--password", help="PDF password (falls back to EKSTRE_PDF_PASSWORD)")
    ap.add_argument("--raw", action="store_true", help="do not mask digits (shows real data)")
    ap.add_argument("--mask-letters", action="store_true", help="also mask letters")
    ap.add_argument("--pages", type=int, default=2, help="how many pages to dump (default 2)")
    args = ap.parse_args()

    if not args.pdf.is_file():
        sys.exit(f"No such file: {args.pdf}")

    data = load(args.pdf, args.password or os.environ.get("EKSTRE_PDF_PASSWORD"))
    m = lambda s: mask(s, raw=args.raw, mask_letters=args.mask_letters)

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        print(f"=== {args.pdf.name} — {len(pdf.pages)} page(s) ===")
        if not args.raw:
            print("(digits masked as '#'; run with --raw to see real values)")

        for idx, page in enumerate(pdf.pages[: args.pages], start=1):
            print(f"\n{'=' * 70}\nPAGE {idx}  ({page.width:.0f} x {page.height:.0f} pt)\n{'=' * 70}")

            # 1. Tables, if the PDF has ruled lines pdfplumber can follow.
            tables = page.extract_tables()
            if tables:
                for t_i, table in enumerate(tables, start=1):
                    print(f"\n-- TABLE {t_i}: {len(table)} rows x "
                          f"{max((len(r) for r in table), default=0)} cols --")
                    for row in table[:25]:
                        cells = [m(c.replace("\n", " ")) if c else "" for c in row]
                        print("  | " + " | ".join(cells))
                    if len(table) > 25:
                        print(f"  ... {len(table) - 25} more rows")
            else:
                print("\n-- no ruled tables detected (text-positioned layout) --")

            # 2. Raw text lines — this is what a regex parser would work on.
            print("\n-- TEXT LINES --")
            text = page.extract_text(layout=True) or ""
            lines = [ln for ln in text.splitlines() if ln.strip()]
            for ln in lines[:60]:
                print("  " + m(ln.rstrip()))
            if len(lines) > 60:
                print(f"  ... {len(lines) - 60} more lines")

    print(f"\n{'=' * 70}")
    print("Send this output to Claude to have the parser template written from it.")


if __name__ == "__main__":
    main()
