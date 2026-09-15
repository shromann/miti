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
  entry point. The commands are `auth`, `scan`, `classify`, `process`, `rules
  validate`, `labels ensure`, and `review`.
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
  mark it read and apply `Reports GAC Daily Reports`.
- A Box Office report is identified by the exact subject
  `BOR - Golden Age Cinema- Nightly`; mark it read and apply
  `Box Office Report`.
- Invoice messages with a link must download the linked PDF and attach it to
  the email before forwarding to Xero Bills. Invoice messages already
  containing a PDF are forwarded to Xero Bills.
- After invoice forwarding succeeds, mark the original message read, apply
  `Accounts Payable`, and remove it from the inbox.
- Advertisements are deleted.

## Safety invariants

- Use exactly `gmail.modify` and `gmail.send` scopes and begin scans with only
  `is:unread in:inbox`.
- `process` is a non-mutating preview unless `--apply` is explicitly passed.
  Advertisement deletion additionally requires `--allow-delete`.
- Never trust a sender display name: rules use parsed email addresses/domains.
  Keep category precedence Shift, Box Office, Invoice, Advertisement,
  Unclassified.
- Forward invoices only after obtaining a validated PDF. Send first; only after
  send success mark the original read, label it, and remove `INBOX`. Preserve
  unrelated labels. Record actions in the ledger and never resend a successful
  invoice forward.
- Avoid logging or displaying OAuth token contents.
