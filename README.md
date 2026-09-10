# AIFinance Pro

A personal finance dashboard that parses Turkish bank statement PDFs (Ziraat,
Garanti BBVA, Kuveyt Türk) into structured transactions and turns them into a
real budget: category spend, recurring subscriptions, instalment forecasts,
transfer matching between accounts, and an AI coach that answers questions
about your own numbers.

Built with [Claude Code](https://claude.com/claude-code) — every parser,
verification rule, and UI feature in this repo was built through an iterative
process with Claude, including a self-check step where each parser confirms
its own output against the totals printed on the statement before anything is
trusted.

**Stack:** vanilla JS single-page dashboard, Python (pdfplumber) parsers,
FastAPI + SQLAlchemy backend for the hosted multi-user version, Google OAuth.

No bank data lives in this repo — statements, parsed JSON, and credentials are
all gitignored.
