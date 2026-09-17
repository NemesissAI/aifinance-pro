# AIFinance Pro — project notes

A local, offline personal-finance dashboard. Bank statement PDFs are parsed on
this machine into JSON; a single-file dashboard reads that JSON. Nothing is
uploaded anywhere and there is no cloud account.

**Read this first in a new session — it replaces the chat history.**

## Layout

```
index.html                     the whole dashboard (~4000 lines, no build step)
data/<YYYY-MM>__<bank>.json    one parsed statement each
data/index.json                manifest — the browser cannot list a directory
statements/*.pdf               the original PDFs
user-state.json                every decision the user makes — see below
logos/<bank>.png               optional bank logos; a missing one falls back
tools/parse_statement_local.py offline parser (pdfplumber) — the primary one
tools/parse_statement.py       Claude API parser — needs API credit, unused
tools/inspect_pdf.py           dumps a PDF's layout with digits masked
tools/statement_model.py       shared schema + serializer for both parsers
tools/manual_entry.py          hand-entered statements (scans), same self-check
tools/fetch_mail.py            pulls statement PDFs out of Gmail (needs OAuth)
mail-log.json                  which mails have been imported — the dedup ledger
.gmail/                        Google OAuth client + token; never served over HTTP
.claude/static-server.ps1      static server + upload/delete/fetch-mail API
.venv/                         Python 3.12
```

Run it:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .claude\static-server.ps1
```

Then <http://localhost:4173>. It must be `http://` — `fetch()` cannot read files
from a `file://` origin, and the upload API does not exist there.

## Where the user's decisions live

**`user-state.json` in the project root, not localStorage.** The desktop app
does not carry localStorage across a restart: on the next open only the keys
the page rewrites during boot (`aifp.months`, `aifp.theme`) were still there,
so every category fix, custom category, budget, matched pair, cash count,
subscription and instalment note was gone. localStorage is now a cache; the
file is the home. `durable` in index.html reads it before anything renders and
writes it back (debounced, plus a `sendBeacon` flush on close) through
`GET/POST /api/state`. The server keeps one `.bak` generation and refuses a
body that is not valid JSON, so a bad write can never replace a good file.

Deliberately **not** in `data/`: `write_index()` globs `data/*.json` and would
list it as a statement for the dashboard to parse.

## The rule that matters most

**Every parser verifies itself against the statement's own printed totals.**
Ziraat and Garanti Bonus print a five-term equation
(`Devreden Bakiye + Harcamalar + Kesintiler − Ödemeler = Dönem Borcu`), Kuveyt
Türk prints `TotalWithdrawals`/`TotalDeposits`, Garanti's account statement
prints a row count. If the check fails, the parse is wrong — a regex parser
fails silently otherwise. Never trust output without the `✓` line.

**And where a running balance is printed, every row is checked on its own.**
The totals only prove the *columns* add up: two misreads in opposite directions
still sum correctly, and a row dropped out of the middle never reaches either
total, so neither is felt. Each printed `Bakiye` must equal the one above it
plus that row's amount, which pins every row individually — `_kt_balance_chain()`
for Kuveyt, the `Bakiye` cell for Garanti's account statement. This is what
caught the ₺10.000 transfer the Kuveyt template had been silently dropping.

**Consecutive statements for one account must join up.** This month's
`openingBalance` is last month's `closingBalance`, and a card's `Devreden
Bakiye` is the previous statement's `Dönem Borcu` (carried as a negative
balance, so one check covers cards and accounts alike). A missing statement, a
double-counted one and a misread row all show up here and nowhere else.
Settings says so in as many words, and says it in green when it holds — "no
warnings" and "checked, and it ties" look identical otherwise.

## What a row *means*, not what the bank printed

A statement calls a credit-card payment income, a transfer between the holder's
own accounts income *and* expense, and a Midas transfer an expense. Counting
those makes both totals wrong, so every row carries a `flow`:

| flow | what it is | counted as |
|---|---|---|
| `real` | genuine income or spending | income / expense |
| `internal` | own accounts, card payments (Rule D) | nothing |
| `passthrough` | UPT / Selman Aydın / Arif Kesik bridges (Rule C) | nothing |
| `investment` | money sent to Midas | its own card + chart |
| `trust` | İsrafil Enes Saatçi's money (Rule B) | nothing |
| `matched` | both legs of a confirmed pair | nothing — only the leftover counts |

`apply_rules()` in the parser sets it, **after** `verify()` — the self-check
must measure the untouched rows. Only whitelisted senders (Mustafa Açık, KYK,
Tahsin Ceran) become `Income`; **every other inflow is left `Unknown` on
purpose**, because most of them are friends paying back a bill.

`resolveInternal()` in the dashboard is the other half: an own-name inflow is
only internal if the matching outflow exists in some loaded statement (same
amount, ±5 days, different file). An unmatched one came from outside — it is
promoted to `real`/`Unknown` so it lands in the queue instead of being silently
dropped. This is what the salary transfer looks like.

## Supported banks

| Template | Layout | Self-check |
|---|---|---|
| Ziraat Bankası (Bankkart) | positional; TL / USD / Bankkart-Lira columns; page printed twice | totals equation + carried balance |
| Garanti BBVA (Bonus kredi kartı) | positional; Bonus points column left of Tutar; Turkish month names | totals equation + carried balance |
| Garanti BBVA (hesap hareketleri) | ruled table; Etiket column carries the category | row count + Bakiye chain |
| Kuveyt Türk (hesap ekstresi) | English headers; **no space characters in the PDF**; wrapped rows | withdrawals/deposits + Bakiye chain |

A card statement never prints where its period *began*, so `periodStart` is its
earliest row and `periodStartExact` is false. A quiet fortnight on a card then
looks exactly like missing coverage — which is why the gap warning skips an
inferred start, and skips it outright when the balances already prove the two
statements join.

**Ziraat vadesiz hesap is hand-entered — both months.** Every statement of this
account arrives as a scan with no text layer: zero words, nothing to parse. It
is the only source for the KYK bursu and the Tahsin Ceran transfer, and it holds
the counter-legs of every card payment, so leaving a month out puts a hole in
it. `tools/manual_entry.py` carries the transcriptions and checks each one two
ways before writing anything — against the Borç/Alacak the statement prints
(17.541,47 / 17.441,36 for July, 18.441,89 / 19.641,89 for August) *and* against
the running `Bakiye`, which pins each row on its own rather than just the column
total. A misread digit fails a check instead of landing in `data/`. If a text
PDF of this account ever arrives, write a real template and delete the blocks.

To add one: drop the PDF on the dashboard. Unknown banks are saved and their
masked layout shown; send that to Claude and a template gets added to
`TEMPLATES` in `parse_statement_local.py`.

## Traps that already cost time — do not re-learn these

- **`str.casefold()` is broken for Turkish.** Python folds `İ` to `i` + U+0307,
  which never equals a plain `i`. Use `norm()`. Every rule and detector is
  written in the plain-ASCII form `norm()` produces, so `"Garanti" in norm(t)`
  can never match — it must be `"garanti"`.
- **Two banks put a loyalty-points column next to the money column.** Amounts
  are matched by x-position, never by order on the line. Reading them
  positionally is what makes the self-check pass.
- **Ziraat prints the same page twice.** Pages are de-duplicated by text hash;
  without it every transaction is counted twice.
- **The PDF password must never touch the command line.** `Invoke-Parser` used
  to build `--password "$password"` and hand it to `Start-Process
  -ArgumentList`, which joins an array with spaces and quotes *nothing*. A
  password of `x" --out "C:\somewhere\evil.json` broke out of its own argument
  and made the parser write a file outside the project — tested against the
  running server, it really did land there. It goes through
  `$env:EKSTRE_PDF_PASSWORD` now (both tools already read it), which also keeps
  it out of the process list. **Anything else that ever reaches a Python
  argument from a request body needs the same treatment.**
- **The server serves the project root, and the secrets live in it.**
  `.gmail/credentials.json`, `.gmail/token.json` and `user-state.json` were all
  fetchable over HTTP, content and all. Dot-files, dot-directories, the state
  file and the mail ledger are refused now — checked twice, once on the
  requested path and once on the *resolved* path, because the first only works
  while `HttpListener` keeps normalising `..` for us and that is its behaviour,
  not a promise.
- **The chat box put what you typed into `innerHTML`.** `<img src=x onerror=…>`
  in the message field ran. It is escaped now, with its own `chatEsc` because
  `esc()` lives in a different `<script>` block. Merchant names in the tables
  were already escaped; check any *new* `innerHTML` path against statement text,
  which is the one place attacker-shaped strings can arrive from outside.
- **`& $exe … 2>&1` in PowerShell** wraps stderr lines in ErrorRecords, which
  under `$ErrorActionPreference='Stop'` turns a successful parse into an HTTP
  500. The server uses `Start-Process` with file redirection instead.
- **Statement files must be keyed by period *and* bank.** All four August 2026
  statements would otherwise overwrite each other silently.
- **Kuveyt also wraps the *amount* out of its dated line.** The Tutar cell is
  narrow and right-aligned, so a wide figure breaks inside the cell: the minus
  sign prints on one line and the digits on the next, both flush to the same
  right edge, and neither sits on the row's dated line. That row then carried no
  amount at all and was dropped without a word — ₺10.000 missing out of July,
  and the only reason anyone noticed is that the statement's own
  `TotalWithdrawals` disagreed by exactly that much. `_kt_amount_tokens()`
  collects the amounts per page and folds a stray `-` back onto the number below
  it; a dated row with nothing on its own line claims the closest unclaimed one
  within `KT_WRAP_GAP`. **Never assume the amount is on the dated line.**
- **A total that adds up is not a parse that is right.** This one balanced its
  columns and still lost a row. The running-balance chain is what proves each
  row, so prefer it wherever the bank prints one.
- **Kuveyt Türk wraps a description above *and* below its dated line.** The
  dated line itself is often blank in the Description column. Rows are grouped
  by exact `top`, so proximity is the only thing that says which printed line
  belongs to which transaction: a wrap sits ~6pt away, the next transaction
  ~25pt. `KT_WRAP_GAP` is that boundary. Without it the Midas transfers vanish
  and rows read "Ödeme ?".
- **The same rule keeps the page header out.** The header block is ~40pt above
  the first row, so it fails the proximity test; matching on content instead
  would break on the next statement layout.
- **Kuveyt never prints a plain merchant name.** A card purchase is a
  comma-separated technical record whose *last* field is the shop; POS rows
  hide it behind `FirmaAdı:`, ATMs behind `ATMName:`, and the city is glued
  onto the end because the PDF has no spaces. `kuveyt_merchant()` handles all
  four shapes.
- **`window.prompt()` does nothing in the desktop app's webview.** It throws
  "prompt() is not supported", so the button appeared dead. Ask for text with
  `askText()`, which uses the app's own modal.
- **"Own account (FAST)" is the same string on every transfer, and the bank
  never prints the other end.** Eleven identical rows in a list, and the one
  thing that would let you recognise a move — which account it went to — is
  exactly what is missing. `resolveInternal()` already knows it, because pairing
  the two legs is how it decided the row was internal, so it stamps `_pairBank`
  on both and `transferLine()` prints "from Kuveyt Türk into Garanti BBVA"
  under the merchant. A promoted row (no leg found) gets `_intoBank` and says
  which account it landed in instead, since the other end is genuinely unknown
  — that is why it is in the queue.
  **The merchant string was deliberately left alone.** It is the key an override
  is stored under, and the saved state already has
  `"FAST transfer in my own name · 05.08" → Income`; renaming it would orphan
  decisions already made. Display detail goes in its own field, never in the key.
- **A per-merchant override is as broad as the merchant name.** `apply_rules()`
  renames every own-name transfer to one string, so a single category click
  landed on fourteen rows. Rows the dashboard promotes out of `internal` get a
  merchant of their own for exactly this reason.
- **`overrides.apply()` has to run last.** With `resolveInternal()` on the
  outside, a row the user had categorised was pushed back to `Unknown` on the
  next render.
- **A card payment hides behind many spellings.** `şube-hesaptan ödeme`,
  `KK TAHSİLAT`, `ÖDEMENİZ İÇİN TEŞEKKÜR`, `K.Kartı Ödeme 5269 ****` — each is
  the same internal move. The last one was missing and ₺2.587 counted as
  spending. Add the spelling to `INTERNAL`, not a new rule elsewhere.
- **`norm()` keeps Turkish letters in the dashboard but strips them in the
  parser.** `index.html` has its own `norm` (locale lowercase, keeps `ı ç ğ ö ş
  ü`); the parser's folds them to ASCII. A needle written for one will not match
  in the other.
- **Every `<script>` block on the page is its own IIFE.** A `function` in one
  is invisible to the others — nothing here is global. `toast()` is defined in
  the last block and published as `window.__toast`; earlier blocks call it
  through a one-line shim. Five controls (currency switch, the Monthly/
  Quarterly/Yearly toggle, mark-notifications-read, sign out, delete request)
  threw `ReferenceError` for exactly this reason.
- **The desktop app swallows `contextmenu` too.** Right-clicking a row never
  reached the page, so "remember as recurring" looked dead. The row carries a
  repeat button; the right-click handler stays as a shortcut where the host
  does deliver it. Same class of problem as `window.prompt()`.
- **Chart.js cannot read a Tailwind class.** Every hue `STYLE` can hand out
  needs an RGB triple in `TW_RGB`, or a category invented at runtime falls back
  to grey. Colours are picked by `freeColor()` — first hue nobody is using —
  because index-modulo gave two categories the same wedge colour.
- **There is no Debt Trend chart.** The card is paid off in full every cycle,
  so a debt curve only ever drew the same shape. Removed, along with its canvas
  and every reference to `window.__chartLine`.
- **Bank chips are monograms, not logos.** A solid chip in the bank's own
  colour with a card glyph on the two credit-card accounts. The real logos are
  image files this machine does not have, and fetching them would break the
  promise that nothing here touches the network.
- **`refreshAll()` is the only repaint.** Every mutation — a category, a match,
  a budget, a cash count, a subscription, an instalment note, the cycle day —
  calls it, and it drives `renderRealMode()` which paints cards, both tables,
  charts, heatmap, instalments, subscriptions, matches, budgets and the coach
  together. Subscriptions used to repaint only their own table and the cycle
  handler ran its own partial sequence.
- **A filter and a bulk edit must not look alike.** The bulk bar is now an
  action bar that appears *only* while rows are ticked, and the filter row
  carries a chip strip naming every active filter with a one-click clear —
  "54 of 133 shown · Food & Dining ×". Two permanent dropdowns side by side is
  what made a filter look like it did nothing.
- **`flex-shrink-0` on the filter row made it overflow its card**, so the last
  controls were unreachable. It wraps now.
- **"Largest expense" and "top merchant" are different questions.** ₺4.000 on
  one Media Markt purchase against ₺4.560 across eleven BIM visits read as a
  contradiction; the card names itself "Biggest single charge" and prints the
  top merchant underneath.
- **One bank renderer.** Settings used to draw its own coloured square from a
  second bank table that knew nothing about `logos/`, so an account looked
  different there than in a transaction row. Every logo is boxed to a fixed
  size — the sheet's tiles run from 193x28 to 193x87, so scaling by height
  alone made some marks three times the width of others.
- **The Transactions view has exactly one filter owner: `visibleTxns()`.**
  There used to be a second, `makeTable`, left from the seeded-demo era: it
  filtered by reading the *rendered* cells (`td:nth-child(3)` for the category)
  and paged by setting `display:none`. Two things followed. Adding a checkbox
  column shifted that cell to the merchant, so the category filter compared
  against the wrong text and returned rows tagged something else entirely. And
  it fought `applyTxnView()`, which rebuilds the same tbody from data — whoever
  ran last won, which is why filtering only appeared to work after clicking
  something else. **Never filter by reading the DOM.** Search, category, range,
  person, unknown-only, hide-settled, sort and paging all read the transaction
  objects now.
- **A bulk edit must never look like a filter.** The category dropdown in the
  bulk bar used to apply on `change` — the same gesture a filter uses. Combined
  with a "Select all" that ticked every row, one click rewrote 74 merchants'
  categories in a single action and wiped a session of manual work. Picking a
  category now only arms an *Apply* button that names the count, and anything
  over three merchants asks first. "Select all" is "Select page".
- **A match is a group of rows, not a pair.** One ₺5.300 transfer can cover two
  separate charges, and three friends can each send part of one bill; anything
  that only pairs two rows cannot express either. Every row in a group is
  flagged `matched` and stops counting — both legs, so both leave the queue and
  the totals. The leftover is not dropped: `applyMatches()` appends a synthetic
  adjustment row carrying the difference, as spending when more went out than
  came back and as income when more came back.
- **The Preferences switches were CSS theatre.** Three inline `onclick`
  handlers flipped a colour and changed nothing: auto-categorisation stayed on
  whatever the switch said, Dark Mode never touched the theme, and "Email
  Notifications" advertised something this app cannot do — no server, no
  account. They are real switches now (`aifp.prefs`, durable) gating
  `applyAuto()` and `findMatches()`, plus the actual theme. The email one is
  gone rather than faked.
- **Currency has to be re-applied after every render.** `applyCurrency()`
  rewrites money text nodes, and every repaint writes TRY again — so the choice
  silently reverted. `renderRealMode()` re-applies it at the end, and the pick
  is stored.
- **Budgets suggest, they do not invent.** The suggestion for a category is that
  category's own average across the imported cycles, rounded up to ₺50, with a
  basis line saying how many cycles it averaged. One cycle is stated as such
  rather than dressed up as an average.
- **Only two of the five accounts have a bank-imposed cut date.** Ziraat
  Bankkart closes on the 3rd (mail arrives the 4th–5th) and Garanti Bonus on the
  11th (mail the 16th–17th). Kuveyt Türk, Garanti hesap hareketleri and Ziraat
  vadesiz are **not cards**: the holder picks any date range and downloads it by
  hand, so they are never the thing a cycle waits for. Do not infer a cut from
  the `periodEnd` of whatever was downloaded — that is a choice, not a schedule,
  and reading it as a schedule produced a confidently wrong answer about when a
  month is ready.
  Two consequences. The free three should be downloaded on the *cycle*
  boundary, or each cycle needs two statements from each of them instead of
  one. And a cycle is complete only when both cards' straddling statements have
  landed: 26 days after the cycle ends at a day-22 boundary, 15 days at day 3.
  42% of real spending sits on those two cards, so a cycle read before they
  arrive understates spending by roughly that much.
- **The cycle day and the statements you download are unrelated.** `cycleStart`
  only groups rows for display; it never decides what to import. Download each
  account's complete consecutive statements and let the dashboard bucket them.
- **A period+bank filename can silently swallow a second statement.** Two
  different ranges from one bank can both end in the same month, and the second
  import overwrites the first. `main()` now reports what it replaced and shouts
  when the previous file covered a different period.
- **The oldest cycle is fed by only some of the accounts, so it is a fiction.**
  Statements start on different days: with these ten loaded, Ziraat Bankkart
  reaches back to 3 June while the Bonus card only covers from 22 June. The
  22 May – 21 June cycle therefore showed ₺27.798 of spending against ₺4.055 of
  income — a ₺23.743 "loss" that never happened, because the money that paid for
  it arrived in May in a statement nobody has. **Settings → Count from** is the
  answer: rows before that date are left out of every figure. It is set to
  **2026-06-22**, measured rather than guessed — it is both the first date all
  five accounts cover and a cycle boundary, so nothing is left half-covered.
  1 June and 16 June were both tried and both leave the same stub cycle behind.
  **The rows are filtered, never deleted.** A statement has to stay whole or its
  own totals equation and Bakiye chain prove nothing — and those are what caught
  the missing ₺10.000. `months.allTxns()` is the single choke point that applies
  it; the heatmap fades the days before it, because an empty cell otherwise
  reads as "spent nothing" rather than "no data".
- **Half a cycle on the same chart as a whole one reads as underspending.**
  Statements start and end on different days per account, so the earliest and
  latest cycles are only partly covered — with these ten statements loaded,
  only 22 June – 21 July is covered by all five accounts from end to end.
  Settings names each partial cycle and says how far the coverage actually runs
  ("covered only to Aug 3"), because the shortfall is otherwise indistinguishable
  from a cheap month. It names the accounts that fall short only when a couple
  of them do; when they all do, five account names bury the point.
- **Statement periods overlap, so rows must be de-duplicated.** A card
  statement runs 4 July → 3 August while the previous one ran 28 June →
  13 July: everything in between is printed on both. `dedupeStatements()` in
  `months.allTxns()` drops cross-file repeats keyed on bank+date+amount+
  merchant — two identical charges *inside one* statement are real and kept.
  Settings reports how many were folded, plus any gap between statements,
  because a missing month is silent otherwise.
- **`cycleLabel()` was a month out.** The key is the month a cycle *starts* in,
  so 2026-07 with a boundary of 22 spans 22 July – 21 August; the label said
  22 June – 21 July. Nobody could see it with one statement loaded. It prints
  the real span now.
- **An adjustment row must never be matchable — this bit twice.** The rule was
  already written for the repayment finder, and `findTrustInvestments()` was
  added without it: confirming ₺3.000 of İsrafil's against a ₺2.120 Midas
  transfer left ₺880 "held back", which was promptly offered as a fresh
  trust-into-Midas pair against the next Midas transfer. Any new suggestion
  source must filter `!t._adjustment` on both legs.
- **An adjustment row must never be matchable.** `applyMatches()` creates the
  leftover row, so offering it as a match candidate feeds the feature's output
  back into its input — two of them got matched to each other. `_adjustment`
  rows are excluded from suggestions, manual linking and the Unknown queue.
- **Settled matches stay on the card.** They used to be replaced by the open
  suggestions, so a match made by mistake was invisible and unfixable. The card
  shows "1 to confirm · 3 settled": suggestions first, then *Already matched
  this period* with the amounts, what was left over, and an Undo on each. Undo
  goes through `refreshAll()`, so the cards, both tables, every chart, the
  heatmap and the coach move together — verified by snapshotting nine surfaces
  either side of one click.
- **`Unknown` is not assignable.** It is where a row starts, not a choice; the
  per-row menu offers every category except it, because a stray click once
  wrote `MEDİA MARKT → Unknown` and pushed a categorised row back into the
  queue.
- **"Not a pair" is about the inflow, not the candidate.** Dismissals used to
  be stored per pair, so refusing one suggestion just let the same money come
  back matched against the next-closest charge — the user was asked about it
  again and again. A dismissal now silences the *inflow*; the card lists what
  was dismissed with a one-click restore, because otherwise a careless refusal
  loses a repayment for good.
- **The counter-leg of a repayment is usually already `internal`.** A friend's
  transfer funds the card payment it arrived for, so restricting the match
  search to `real` outflows hid exactly the cases the feature exists for. Only
  rows already in a group, and rows flagged `matched`, are off limits.
- **A tick must not live on the checkbox.** The table is rebuilt from data on
  every filter, sort and page change, so a tick stored in the DOM vanished when
  the user turned the page — which made it impossible to match a transfer on
  page 1 against a charge on page 3. Selection is a `Set` of row keys.
- **The instalment table shows which card is paying**, as the bank's logo next
  to the purchase — four plans across two cards is otherwise unreadable.
- **Bank logos are optional files in `logos/`.** `GET /api/logos` lists what is
  actually there and the dashboard asks once at boot; a bank without a file
  keeps its coloured monogram. Probing with an `<img>` per row instead put a
  404 in the console for every row of every bank without a logo.
- **A scanned PDF has no text layer.** The parser detects this and says so
  rather than reporting "no template matches".

## Dashboard behaviour

- Loads `data/` automatically on open; caches in `localStorage`.
- `aifp.months` (statements by filename), `aifp.catOverrides` (manual category
  fixes by merchant), `aifp.recurring` (recurring rules), `aifp.customCats`
  (categories the user invented), `aifp.cash` (wallet count + cash income per
  cycle), `aifp.instNotes` (a line of your own per instalment plan),
  `aifp.budgets` (per-category, empty until set), `aifp.matches`
  (confirmed/dismissed repayment pairs), `aifp.subs` (subscriptions),
  `aifp.theme`, `aifp.cycleStart`. `localStorage.clear()` resets everything.
- **Statement cycle**: Settings → *Month starts on day N*. Cards close on the
  4th and 13th, salary lands on the 1st, the family transfer on the 15th — a
  calendar month splits those apart. `cycleOf()` applies the boundary everywhere.
- **Unknown queue**: Transactions → *Unknown* button. Checkboxes + a category
  dropdown categorise in bulk; a choice applies to every row from that merchant.
- **Recurring rules**: right-click a transaction → name it. Matched on
  counterparty + amount (±15%) + day-of-month window, so later statements are
  labelled without asking.
- **Person filter**: transfers carry a normalised `counterparty` key, so
  `MUSTAFAAÇIK` and `Mustafa Açık` group together.
- **Instalments**: parsed from `İşlemin N/M Taksidi` (Ziraat) and
  `X,XXxN=TOTAL N.Taksit` (Garanti). The table carries a column per upcoming
  cycle with a month-total row, projected from the *statement* month
  (`_period`), not the purchase date — an August statement covers mid-July
  transactions.
- **Custom categories**: the Unknown queue and the per-row menu both end in
  "＋ New category…". A new one is folded into `STYLE`/`BADGE`/`CATEGORIES` at
  runtime and gets a colour from `CUSTOM_PALETTE`, so every renderer picks it
  up without knowing it is custom.
- **Analytics follows an account filter**: `analyticsBank` gates every figure
  on that view, so picking one bank gives that account's own avg daily spend,
  top category and largest expense. "Spend per account" above it is the
  cross-bank table; clicking a row selects that bank.
- **Budgets are empty until set.** `aifp.budgets` starts `{}` — a category with
  no budget shows what it cost and a "click to set one" line. The four seeded
  figures that used to be hard-coded were never the user's.
- **The heatmap is one calendar block per month**, Monday-first, with the
  month name, that month's total, and a blue rule down the left edge of the
  day the statement cycle starts. It was a single 44-cell strip: the month
  boundary was invisible and the only way to identify a cell was to count. The
  colour ramp is one hue in six steps — the old green→yellow→red scale read as
  a warning rather than a quantity.
- **Everything re-renders together.** `renderRealMode()` is the single entry
  point: tables, charts, heatmap, instalments, subscriptions, matches, budgets
  *and* the AI Coach cards. The coach was left out and quoted stale figures
  after every categorisation.
- **Charts follow the statement cycle, not the calendar.** `updateCharts()`
  buckets on `cycleOf()`; with the cycle starting on the 22nd, grouping on
  `date.slice(0,7)` split each cycle across two bars. Quarterly and Yearly roll
  up from those same buckets — they used to swap in a seeded demo series, so
  clicking either replaced the real chart with invented numbers.
- **Two layers of categorisation.** The parser emits only the seven categories
  the JSON schema allows. `AUTO_RULES` in the dashboard maps merchants onto the
  categories the account holder actually asked for — Transportation (flights,
  İstanbulkart, fuel), Sport (kickboxing, BOUN SPOR), Abonelik (Claude, kontör),
  Health. It runs *before* `overrides.apply()`, so a manual fix always wins.
  **ATM withdrawals are deliberately not in it**: the statement never says what
  the cash was for, so each one is asked about in the queue.
- **One counterparty, several spellings.** Kuveyt prints `MUSTAFANAMLI` with no
  spaces and, three rows later, `Mustafa Namlı`; Ziraat writes `KÜÇÜK ÇAMLICA`
  where Kuveyt wrote `KUCUKCAMLICA`. Overrides are keyed on the merchant string,
  so categorising one left the others sitting in the queue — which is why the
  saved state carried the same decision two and three times over. `overrides
  .apply()` now falls back to `mergeKey()`, the merchant folded to plain ASCII
  with everything but letters and digits stripped, so one click covers every
  spelling. The exact string still wins, and digits survive the fold, so
  `ATM Kodu:00924CRS140` and `...CRS121` stay apart.
- **`AUTO_RULES` match the merchant as printed, not `norm()`ed.** `konbeltas`
  never matched `KONBELTAŞ` and `ibb` never matched `İBB`. Spell the Turkish
  letters out: `konbelta[sş]`, `[iİ]bb`.
- **Statement cycle should start on day 22.** Measured, not guessed: for every
  candidate day, count how many linked events (a transfer's two legs, a bridge
  pair, a card's cut→due arc) fall in different cycles. Day 22 splits one, day 1
  splits two. Garanti Bonus is due on the 21st, so a cycle starting on the 22nd
  holds both cards' whole charge→payment arc.
- **A tagged inflow is a repayment, not income.** Giving a `+` row a spending
  category means "I fronted this and a share came back". `categoryTotals()` is
  the one place that arithmetic lives: charges minus repayments per category.
  Counting such a row as income inflates both sides — the meal keeps its full
  price *and* the repayment shows as earnings. `Income` means earned;
  `Unknown` means undecided and still counts as income until it is not.
  Every surface must go through `categoryTotals()` / `isRefund()`: the
  per-account table did not, and disagreed with the cash-flow card by exactly
  the repaid amount.
- **Subscriptions are hand-entered** (`aifp.subs`, under the instalment
  forecast). A statement only shows a subscription after it is charged, and
  only for a card that was imported — anything billed elsewhere is invisible
  and still costs money every month. Yearly plans are divided down so they can
  be compared with monthly ones.
- **`toISOString()` shifts the date west.** A subscription renewing on the 7th
  rendered as the 6th. Build the key from `getFullYear/getMonth/getDate`
  (`isoLocal`), never from the UTC string.
- **Possible matches** (bottom of Transactions): an unclassified inflow within
  **±5% and ±3 days** of a payment out is offered as a pair — a bill fronted on
  a card and the share that came back. A share is rarely exact to the kuruş,
  which is why it is a tolerance; the window stays narrow because a suggestion
  on every row is noise. Closest amount wins, so a ₺4.000 charge is not paired
  with ₺3.810 when ₺3.980 sits beside it. Inflows already recognised as regular
  income are never offered — KYK and the family transfers land and pay the card
  too, and pairing them would erase real income.
  **Confirming depends on the other leg.** Against a real charge the inflow
  takes that charge's category and becomes a repayment, so the category loses
  the repaid part and the remainder stays as spending — ₺4.000 repaid with
  ₺3.900 leaves ₺100 of real cost. Against a leg that is already settled (a
  card payment) there is no category to credit, so the inflow just stops
  counting. Pairs are stored in `aifp.matches` **by row key and applied by row
  key**, never re-discovered, which is what lets a hand-made pair be any two
  rows however far apart.
- **Money held for İsrafil that goes straight into Midas was invested for him.**
  He sends a round sum and a Midas transfer follows within a day or two — that
  transfer is what the money was for, and the account holder usually tops it up
  from his own pocket. Counting the whole transfer as the holder's own investing
  overstates it by İsrafil's share. `findTrustInvestments()` offers the pair on
  the same card as the repayment suggestions: a `trust` inflow and an
  `investment` outflow dated **on or after it, within `TRUST_INVEST_DAYS` (5)**.
  Amounts are deliberately *not* compared — requiring them to agree would hide
  every case where money was added, which is most of them. Confirming splits it:
  his share stops counting as the holder's investing, and the leftover is
  `investment` when the holder added money and `trust` when some of his stayed
  behind. **It must never become `real`** — the generic leftover rule would have
  called that ₺600 top-up *spending* and moved it into the expense total, which
  is the one place it cannot appear.
- **The period filter lives in the header, not on the dashboard.** It used to
  sit inside `#view-dashboard`, so Transactions, Analytics and Settings had no
  way to narrow anything. One control now governs every view.
- **A panel that answers a question the period filter cannot narrow must be fed
  `everyCycle`, not `txns`.** `renderRealMode()` builds both. Four panels were
  wrongly taking the filtered set: an instalment plan is a commitment spanning
  months, so picking one cycle changed how many were left and what they cost;
  "Already committed" is about the cycle *after* the last one; and both
  time-series charts drew a single lonely bar. The rule: if the answer is about
  *other* months, it cannot come from the filtered rows.
- **"Already committed" was labelling a statement month with a cycle span.**
  An instalment is keyed by the statement that charged it (`_period`); a cycle
  key is a window of days set by "Month starts on day". The card ran
  `cycleLabel()` over the statement key and printed "Sep 22 – Oct 21" above a
  figure that is really the September statement — a month adrift, and why the
  card read as nonsense. It says "Sep '26 statement" now, which is the same
  thing the instalment table's own Sep column says.
- **A budget is monthly, so it cannot be measured against "All periods".**
  Two cycles of spending against one month's allowance made every category look
  blown. The Budgets card carries its own month selector and always measures one
  cycle, through `categoryTotals()` like every other surface.
- **The green "Email Hook" banner was fiction too.** It announced that July's
  statement had been "automatically fetched via Email Hook and analyzed", with
  an *Automated* badge, and at boot it was rewritten to a row count. Nothing
  ever fetched anything. Removed — Gmail import is real now and reports itself
  in `#mail-result`. The header button's label was fixed in the same pass: the
  markup said "Analyze August Statement" and only became "Reload statements"
  after boot, so it was wrong on every load and wrong about the month besides.
- **The notification bell was three hard-coded fictions** — a statement "parsed
  via Email Hook", a budget that was never set, and a debt trend for a chart
  that no longer exists. Removed, like the fake Preferences switches before it.
- **Settings lists PDFs nothing was read from.** `/api/statements` returns
  `unread`: every file in `statements\` that no parsed JSON names in its
  `source`. A statement nobody could read otherwise looks exactly like a month
  never downloaded. `manual_entry.py` therefore writes the PDF's name into
  `source` too, so a hand-typed scan counts as read.
- **"What changed since last cycle" is the difference, not two numbers.** A
  total that fell ₺400 can hide ₺3.000 less on food and ₺2.600 more on travel.
  The card subtracts cycle from cycle per category and says which moved — and
  it names a cycle that is not fully covered yet, because a half-covered month
  turns into a headline "−50%" that is really missing data.
- **A live clock sits in the header**, and it is not decoration: the instalment
  countdowns are only trustworthy if the date they count from is on screen and
  visibly moving. It also re-renders on the stroke of midnight, so "in 8 days"
  becomes "in 7 days" without a reload.
- **Colour a figure by what it *is*, not by whether the news is good.** The
  "What changed" cards painted a falling Expense green, because spending less
  is good — and a green number under the word "Expense" reads as income at a
  glance. Money in is emerald, money out is red, Net follows its own sign, and
  direction is carried by an arrow and words ("less spent · 50%"), which also
  survives a colour-blind eye.
- **The instalment table leads with the next charge.** "21st of each month" does
  not answer "what is about to be taken"; the column is a date, a countdown in
  days, and the day-of-month underneath, ordered soonest-first and reddening
  inside a week. `nextCharge()` clamps to the length of the month, so a 31st due
  day is the 30th in June.
- **Manual pairing**: tick one inflow and one outflow anywhere in the
  Transactions list, press *Link a pair*. Checkboxes are always on there now,
  not only inside the Unknown queue.
- **Instalment notes** (`aifp.instNotes`, keyed on merchant|planTotal|total): a
  statement writes "S/TRENDYOL" and six months later that means nothing.
- **A settled row shows its flow, not a category.** Rendering a card payment as
  a green `Income` badge is the exact misreading the flow labels exist to
  prevent, so `renderTable` swaps in `FLOW_BADGE` and greys the amount.
- **Cash & investments card**: the wallet count and rare cash income are typed
  in per cycle (no statement can see them); the card compares this cycle's
  count with the previous one and charts invested-vs-cash by cycle.

## Fetching statements from Gmail

`tools/fetch_mail.py` + `POST /api/fetch-mail` + the **Fetch from Gmail** button
on the import panel. *Check mail* lists what would come in without downloading.

**Claude's Gmail connector cannot do this job.** It was tried: `get_message`
returns an attachment's `filename`, `id` and `mimeType` and there is no call
that returns the bytes. The button also has to work when nobody is talking to
Claude, so the credential has to live on this machine. That means a Google
Cloud OAuth client (Desktop app, scope `gmail.readonly`) with the JSON saved as
`.gmail/credentials.json` — a one-time manual step the user must do. Until then
every entry point reports "not configured" **with the steps in it**, rather
than failing quietly. `google-api-python-client` and `google-auth-oauthlib` are
already installed in the venv.

Ziraat's mail is `ziraat@ileti.ziraatbank.com.tr`, subject `<Ay> Ayı E-Ekstre
Servisi`, with the PDF attached under its own name (`Z260903CGNCKKTR480994.pdf`).
Senders are matched on the *domain* so a changed no-reply prefix does not
silently stop the import.

**The same statement must never land twice**, so there are three guards, each
catching what the others miss:

1. the Gmail message id — this mail has been processed
2. the SHA-256 of the PDF — the same file re-sent under a new id
3. `statementId` (`ziraat-bankasi-bankkart-2026-08`) — the same period and
   account, whatever the file was called

`statementId` is emitted by `to_dashboard_json()` for every statement, parsed or
hand-entered. `mail-log.json` at the project root is the ledger — outside
`data/` because `write_index()` globs `data/*.json` and would list it as a
statement.

**The project root is what the server serves, and secrets live there.**
`.gmail/credentials.json` and `.gmail/token.json` were fetchable at
`http://localhost:4173/.gmail/credentials.json`, content and all, and so was
`user-state.json`. Anything under a dot-directory or dot-file, plus the state
and mail ledger, is refused with a 403; the state file has its own API. Check
this again if the server ever grows another way to read a path.

## The creator dashboard (`/admin`)

Who has an account, how long they actually used it, how many statements they
uploaded and which features they touched — one row per person, for watching a
demo that friends are testing.

**`AIFP_ADMIN_EMAILS` is the only thing that grants it.** A comma-separated
list, read from the environment on every request. There *is* an `is_admin`
column and it is deliberately not consulted: three leftover test accounts were
found sitting at `is_admin = 1` from a session where the flag was flipped by
hand, and a column granted once stays granted — those accounts would have kept
the dashboard forever with nothing in the app ever saying so. A column also
disappears with the database on a fresh deploy, which 403s the owner out of
their own dashboard. The environment survives both.

```powershell
$env:AIFP_ADMIN_EMAILS = "you@example.com"
.venv\Scripts\python.exe -m uvicorn server.app:app --port 8000
```

**Time is active time, not tab-open time.** The clock only runs while the tab
is visible *and* something was clicked or typed in the last 60 seconds. The
first version measured wall-clock since page load, which turns a tab forgotten
overnight into "8h of usage" — and that number is the entire reason the page
exists. Batches go every 30s and on `pagehide`/`visibilitychange`, which fire
where `beforeunload` increasingly does not.

**The feature list is an allowlist, server-side** (`KNOWN_FEATURES`). It
arrives from the browser, so without one the column is whatever a client cares
to invent and a single crafted request fills the dashboard with strings
somebody else chose. `active_seconds` is capped at an hour for the same
reason — nothing else bounds a number the page supplies.

**Everything on that page is user-written text.** A display name comes from a
sign-up form; the first version interpolated it straight into `innerHTML`, so
whoever registered could run script in the one browser guaranteed to belong to
the owner. Escape every cell.

## A locked-out account, and why it happened

There is no "forgot password": the app sends no email, so it cannot prove who
is asking. The sign-up form used to ask for the password once, masked, with
no way to see what was typed — so one typo at registration locked the account
with no way back in from the browser. That is exactly what happened to the
owner's own account, and "my account is gone" was the report.

Three things now stand between a user and that:

- **Show/hide on every password field** (`data-toggle-password`), plus a hint
  on the sign-up tab saying there is no reset link.
- **Settings → Change password** for a signed-in user. It bumps
  `session_version`, so every *other* device is signed out, and writes the new
  version into the current session so the person changing it is not.
- **`server/set_password.py EMAIL`** — the recovery path. Run on the machine
  the server runs on; the password is typed at a hidden prompt, never on the
  command line. Same policy as sign-up, so a recovered account is not a weaker
  one. It also bumps `session_version`.

## One page, two servers — the parity rule

`index.html` is served by both `.claude/static-server.ps1` (single-user,
4173) and `server/app.py` (multi-user, 8000). **Every `fetch('api/…')` in
the page must be answered by both, in the same shape.** The audit that
proved this: enumerate the page's calls, enumerate each server's routes,
diff. Five gaps turned up at once, each reported by the user as a separate
"it worked before": `/api/logos` (monograms instead of logos),
`/api/statements/delete` (the Settings delete button 404'd),
`/api/statements` missing `file`/`period` (view and delete sent
"undefined"), `/api/upload` accepting only multipart (the import panel sends
JSON+base64, so every upload outside onboarding was 422), and
`/api/fetch-mail` (raw 404 in the mail note). Run that diff again whenever
either side gains a route.

Two shapes the page depends on and the key it uses for a statement:
locally the file name, hosted the `statementId`. `months.periods()` used
to split the key on `__` and reported ten periods for two. Derive from the
payload, never from the key.

## Moving the local build into a hosted account

`server/migrate_local.py EMAIL` copies `data/*.json`, `profile.json` and
`user-state.json` into one account, backing the database up first.
Re-uploading the PDFs is **not** equivalent, for two reasons found the hard
way: the two Ziraat vadesiz statements are hand-entered from scans with no
text layer, so no upload can ever read them; and every statement uploaded
before the profile carried the bridge/trust/regular-income rules was parsed
*without* them, so its `flow` labels were wrong — the local JSON already has
the right ones.

**Row keys change shape between builds.** Matches and dismissals are keyed
`<months key>#<transaction id>`; locally the months key is the file name,
hosted it is the `statementId`. Copied verbatim, every match points at a row
that does not exist and vanishes without a word — that was "all my matches
are gone". The script rewrites them. Anything new that stores a row key
needs the same treatment.

## Presenting the first-run experience

Showing "this is what a new user sees" needs an account that *is* new every
time, and the owner's own account is the one thing that must never be wiped
to get there. So there is a fixed demo login, `demo@aifinance.local`, whose
password is `AIFP_DEMO_PASSWORD` (environment, never source — a fixed one
would ship in a public repo) and whose state is reset by **Reset demo
account** in the sidebar's *Presenting* block, admin-only. Reset wipes name,
statements, decisions, telemetry and bumps `session_version`, so a browser
still signed in as the demo from the last talk is signed out too. Then sign
out, sign in as the demo, and the onboarding runs from step 1.

**Replay tour** re-opens the tour overlay on the current account and touches
nothing. Onboarding itself is deliberately *not* replayable there: its first
step re-saves the holder name and its last re-saves the cycle day, and a
presenter clicking through would overwrite real settings.

```powershell
$env:AIFP_ADMIN_EMAILS = "you@example.com"
$env:AIFP_DEMO_PASSWORD = "a-real-password-of-12-plus-chars"
.venv\Scripts\python.exe -m uvicorn server.app:app --port 8000
```

## Not built

- **Scheduled/automatic mail polling.** The button is manual on purpose. A
  timer would need the OAuth token to refresh unattended, which is fine, but
  nobody has asked for it yet.
- **The Claude API parser is unused.** `ant auth login` works, but the account
  has no API credit — that balance is separate from a Claude subscription.

## The UI is English

Every label, badge, flow note and toast the user reads is in English, including
the notes the parser writes into `flow_note`. The chat keyword lists still carry
Turkish words on purpose — they match what the user types, they are not shown.

## Editing this repo through a shell heredoc

Backslash escapes do not survive it. Writing a Python `'''...'''` block through
a bash heredoc collapses `\\b` to `\b`, which Python then turns into a real
backspace character in the output file. It has happened three times and cost
time every time:

| written | landed in the file | broke |
|---|---|---|
| `\\b` in an `AUTO_RULES` regex | U+0008 backspace | the H&M rule silently never matched |
| `\\f` in `tools\fetch_mail.py` | U+000C form feed | the server could not find the script |
| `\\u2713` in a test | a literal tick | two parsers "failed" that had passed |

Invisible in terminal output, so it looks like a logic bug. **Use the Write or
Edit tool for anything containing a backslash**, or build it with `chr(92)`.
A control-character scan is part of the system test for this reason.

## Working style the user expects

Verify claims by measuring, not asserting — run the parser, check the browser,
show the numbers. Say plainly when something cannot be done (scanned PDF, no
API credit) instead of working around it silently. The user writes in Turkish.
