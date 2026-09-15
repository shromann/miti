# miti repository instructions

## Commands

- Run the CLI during development: `uv run miti`
- Build the package: `uv build`
- Run the test suite: `uv run pytest`
- Run one test: `uv run pytest tests/test_normalize_rules.py::test_classifier_precedence_and_conservative_invoice`

## Architecture and current state

- This is a Python 3.12+ package using the `src/` layout.
- The installed `miti` command is declared in `pyproject.toml` as
  `miti.cli:main`; keep that callable entry point and its script declaration
  synchronized.
- `miti.cli:app` is the Typer command root. `miti.cli:main` is the installed
  entry point. The commands are `auth`, `invoices`, `reports shift-reports`,
  `reports box-office`, and `advertisements`.
- Keep Gmail-specific I/O behind the `GmailClient` protocol in `miti.gmail`;
  normalizing, classification, planning, and ledger tests must work without
  network access or Google dependencies being imported.
- `normalize.py` makes provider payloads typed `Message` values, `rules.py`
  makes deterministic explainable classifications, and `processor.py` performs
  preview/apply planning. `ledger.py` is the SQLite audit/idempotency boundary.
- `README.md` is the functional specification for the future Gmail automation:
  invoice handling, shift reports, box-office reports, and advertisements.
  Treat it as the source of truth when adding the corresponding CLI commands
  and processing logic.

## Email-processing rules

- A Shift Report is identified by a subject beginning with `Shift Report`;
  mark it read, apply `Reports GAC Daily Reports`, and remove it from the inbox.
- A Box Office report is identified by the exact subject
  `BOR - Golden Age Cinema- Nightly`; mark it read and apply
  `Box Office Report`, then remove it from the inbox.
- Only invoice messages already containing a PDF are forwarded to Xero Bills.
  Invoice emails that only link to a PDF are left untouched.
- After invoice forwarding succeeds, mark the original message read, apply
  `Accounts Payable`, and remove it from the inbox.
- Advertisements are labeled `Advertisements`, marked read, and removed from
  the inbox.

## Safety invariants

- Use exactly `gmail.modify` and `gmail.send` scopes. Every scan is limited to
  `is:unread in:inbox`; report scans may additionally filter by their deterministic subject,
  and advertisement scans use Gmail's Promotions category or `unsubscribe` term.
- `invoices`, `reports shift-reports`, `reports box-office`, and
  `advertisements` are non-mutating previews unless `--apply` is explicitly
  passed.
- Never trust a sender display name: rules use parsed email addresses/domains.
  Keep category precedence Shift, Box Office, Invoice, Advertisement,
  Unclassified.
- Forward invoices only after obtaining a validated PDF. Send first; only after
  send success mark the original read, label it, and remove `INBOX`. Preserve
  unrelated labels. Record actions in the ledger and never resend a successful
  invoice forward.
- Avoid logging or displaying OAuth token contents.
