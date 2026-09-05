# Statement parser

Turns a bank statement PDF into the JSON the dashboard reads.

```
statements/ekstre.pdf  ──►  <parser>  ──►  data/2026-08.json  ──►  index.html
```

There are two parsers, producing identical output:

| Script | Engine | Cost | Notes |
|---|---|---|---|
| `parse_statement_local.py` | pdfplumber + regex | free | **Primary.** Fully offline — nothing leaves the machine. Needs a template per bank layout. |
| `parse_statement.py` | Claude API | ~2–3 kuruş per statement | Handles any layout without a template. Needs API credit on the account (an `ant auth login` profile alone is not enough — the balance is separate from a Claude subscription). |

`inspect_pdf.py` is the tool used to *write* a local template: it dumps a
statement's structure with every digit masked, so the layout can be worked out
without exposing the transactions.

## Importing from the dashboard (easiest)

Start the server, open <http://localhost:4173>, and drop PDFs on the **Import a
statement** panel. You can drop several at once.

A browser cannot read a bank PDF on its own, so the file is POSTed to the local
server, which saves it in `statements\` and runs `parse_statement_local.py` —
the same command you would type by hand. One of four things comes back:

| Result | Meaning |
|---|---|
| **Imported** (green) | Parsed, and the totals match the statement's own summary. |
| **Imported** (amber) | Parsed, but the totals *did not* match — check before trusting. |
| **Password needed** | Enter the PDF password in the card and press Unlock. |
| **Needs a layout template** | Unknown bank. The PDF is saved and the masked layout is shown — copy it and send it to Claude to have a template added. |

The API lives in `.claude/static-server.ps1`:

| Endpoint | Purpose |
|---|---|
| `POST /api/upload` | `{filename, data (base64), password?}` → saves and parses |
| `GET /api/statements` | lists what has been imported |
| `POST /api/statements/delete` | `{period}` → removes that month's parsed data |

## Importing from the command line

```powershell
cd "C:\Users\Talha-PC\Desktop\aylık finansal analiz"
.\.venv\Scripts\python.exe tools\parse_statement_local.py statements\ekstre.pdf
```

Add `--show` to print every parsed transaction, `--password ...` for an
encrypted PDF.

### The self-check

Ziraat statements print their own totals in the footer
(`Devreden Bakiye + Harcamalarınız + Kesintiler - Ödemeleriniz = Dönem Borcu`).
The parser sums what it extracted and compares:

```
  ✓ totals match the statement's own summary
```

If that line turns into a warning, the parse is wrong — the layout probably
changed. Don't trust the JSON until it says ✓. This matters more for a regex
parser than an LLM one, because a regex parser fails silently.

### Supported banks

| Template | Layout | Self-check |
|---|---|---|
| Ziraat Bankası (Bankkart) | text-positioned; TL / USD / Bankkart-Lira columns; duplicate page | totals equation |
| Garanti BBVA (Bonus kredi kartı) | text-positioned; Bonus (points) column sits left of Tutar; Turkish month names | totals equation |
| Garanti BBVA (hesap hareketleri) | ruled table; Etiket column gives the category | printed row count |
| Kuveyt Türk (hesap ekstresi) | English headers; **no space characters in the PDF**; rows wrap across lines | TotalWithdrawals / TotalDeposits |

Two of these carry the same trap: a loyalty-points column sits next to the money
column, so amounts are matched by x-position rather than by order on the line.
Reading them positionally is what makes the self-check pass.

### Adding a bank

`TEMPLATES` in `parse_statement_local.py` is a list of `(name, detector, parser)`,
first match wins. Drop the new bank's PDF on the dashboard: it is saved and its
masked layout shown. Send that to Claude and a new entry gets added — existing
templates are untouched.

Detectors run against page 1 text. Write the needle in the form `norm()`
produces (plain ASCII lowercase) — `"Garanti" in norm(t)` can never match,
because `norm()` lowercases.

### Scanned statements

A PDF with no text layer (a scan or a phone photo) cannot be parsed at all —
there are no characters to read. The parser detects this and says so rather than
reporting "no template matches", which would send you looking for the wrong fix.
Re-download the statement as a normal PDF from your bank, or OCR it first.

### One month, several banks

Statement files are named `data/<YYYY-MM>__<bank-slug>.json`. Keying on the
month alone meant a second bank's August statement overwrote the first one's,
silently. `data/index.json` lists the filenames; Settings → Imported statements
lists them with a per-row eye (isolate that one statement) and trash.

### Categories

`CATEGORY_RULES` maps merchant substrings to categories, first match wins, so
specific rules go above general ones (`trendyol yemek` before `trendyol`).
Anything unmatched becomes `Unknown` and renders amber in the dashboard — click
that badge to set the category by hand; the choice is remembered per merchant.

Rules are written in **plain ASCII lowercase**. `norm()` folds Turkish text to
that form before matching, so `gida` matches `GIDA`, `GİDA` and `Gıda` alike.
Do not use `str.casefold()` here: Python folds `İ` to `i` + a combining dot,
which never compares equal to a plain `i`, and every Turkish rule silently
stops matching.

Merchant names are cleaned by `clean_merchant()` before display — Ziraat writes
instalment text, terminal codes and a city suffix into the description
(`25/06 S/TRENDYOL 02.Tak İSTANBUL 3.120,86 TL İşlemin 2/3 Taksidi`), none of
which is a merchant.

## How the dashboard consumes it

Each run writes `data/<YYYY-MM>.json` **and** refreshes `data/index.json`, which
lists every month present. The browser cannot list a directory, so that manifest
is how the page finds multiple statements.

The page loads them automatically on open — no button press — and caches them in
`localStorage`, so a refresh keeps the data. Three keys are used:

| Key | Holds |
|---|---|
| `aifp.months` | every imported statement, keyed by period |
| `aifp.catOverrides` | category corrections made by clicking a badge |
| `aifp.theme` | light / dark choice |

To start clean, run `localStorage.clear()` in the browser console and reload.

**Settings → Imported statements** lists what has been parsed, with a per-row
eye (show only that period) and trash (delete that period's data; the PDF stays
in `statements\`).

## Running the server

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .claude\static-server.ps1
```

`index.html` must be served over `http://`, not opened as a `file://` path:
`fetch()` cannot read local files from a `file://` origin, and the upload API
does not exist there.

> **PowerShell trap, already fixed here:** the server must not call Python with
> `& $exe … 2>&1`. Windows PowerShell wraps every stderr line in an ErrorRecord,
> which under `$ErrorActionPreference='Stop'` becomes a terminating error even
> when the process exits 0 — the parser's harmless `note:` line was turning a
> successful parse into an HTTP 500. `Invoke-Python` uses `Start-Process` with
> file redirection instead.

## Cost and model choice

`MODEL` at the top of `parse_statement.py` is `claude-haiku-4-5` — the cheap
option, roughly a fifth the cost of Opus, and generally enough for reading a
table out of a PDF. Verify the first statement by hand before trusting it.

If a layout defeats it — merged columns, a scanned/photographed statement,
amounts read wrong — switch `MODEL` to `claude-opus-5`. One line, reversible.

Two things are tied to that constant, so change only the constant:

- `output_config={"effort": ...}` **errors** on Haiku 4.5. Only add it after
  switching to Opus.
- The PDF page cap is 100 on Haiku 4.5 vs 600 on Opus; `MAX_PDF_PAGES` follows
  `MODEL` automatically and the script refuses oversized files rather than
  sending a request that would fail.

## Not built yet

Automatic fetching from Gmail. The plan is a `fetcher.py` using the Gmail API
with OAuth (`gmail.readonly`), filtering by sender and subject, saving
attachments into `statements/`, then invoking this parser. That needs a Google
Cloud project and an OAuth client, which you have to create yourself.

## Importing from Gmail

```powershell
.\.venv\Scripts\python.exe tools\fetch_mail.py --check   # what would come in
.\.venv\Scripts\python.exe tools\fetch_mail.py           # import it
```

Or press **Fetch from Gmail** on the dashboard's import panel, which runs the
same command through `POST /api/fetch-mail`.

Connecting Gmail to the Claude chat does not cover this. That connector returns
an attachment's *name* and never its bytes, and the button has to work when
Claude is not in the conversation, so the credential lives on this machine:

1. console.cloud.google.com → create a project
2. APIs & Services → Library → enable **Gmail API**
3. OAuth consent screen → External → add your own address as a test user
4. Credentials → Create credentials → OAuth client ID → **Desktop app**
5. Save the downloaded JSON as `.gmail\credentials.json`
6. Run `fetch_mail.py --check` once and approve in the browser

The scope is `gmail.readonly` — it can read mail and nothing else.

### Never twice

Three guards, because each catches what the others miss: the Gmail message id,
the SHA-256 of the PDF, and the `statementId` the parser derives
(`ziraat-bankasi-bankkart-2026-08`). `mail-log.json` records every one.
