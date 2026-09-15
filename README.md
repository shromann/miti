# miti

## Installation and setup

Miti is a Python 3.12+ `src`-layout package. Install it (and its Gmail OAuth
client) with:

```bash
uv sync --group dev
# or: python -m pip install -e .
```

Create `~/.config/miti/config.toml` (or provide `--config PATH`) without
putting credentials in it:

```toml
forwarding_address = "your-xero-bills-address@example.com"
# Optional exact addresses/domains; display names are never trusted.
trusted_senders = ["billing@trusted-vendor.example"]
trusted_domains = ["trusted-vendor.example"]
known_vendors = ["invoices@vendor.example"]
known_contacts = ["person@example.com"]
```

Create a Google OAuth **Desktop app** client and save its downloaded JSON as
`~/.config/miti/client_secret.json`, then authorize:

```bash
miti auth
# or: miti --config ./miti.toml auth --client-secret ./oauth-client.json
```

OAuth requests only `gmail.modify` and `gmail.send`. The resulting credential
file is stored locally and tokens are never printed. Do not commit either OAuth
JSON file.

## Commands

All Gmail reads begin with the fixed query `is:unread in:inbox`.

```bash
miti scan                         # normalized unread inbox JSON
miti classify                     # categories, confidence, and rule reasons
miti process                      # preview only: no Gmail mutations
miti process --apply              # process reports and safe invoices
miti process --apply --allow-delete # additionally delete high-confidence ads
miti rules validate
miti labels ensure
miti review                       # recent SQLite audit actions
```

For offline demonstrations and tests, pass a Gmail API message JSON object or
array with `--fixture` to `scan`, `classify`, or preview `process`:

```bash
miti classify --fixture examples/message.json
```

## Safety and deterministic rules

Classification has fixed precedence: a subject starting `Shift Report`, then
the exact Box Office subject `BOR - Golden Age Cinema- Nightly`, then
conservative invoices, then advertisements. Invoice forwarding requires both
invoice evidence and either an existing PDF or an HTTPS link.
Downloaded links must remain HTTPS through redirects, return
`application/pdf`, fit the size limit, and start with `%PDF-`.

Reports are marked read and receive `Reports/GAC Daily Reports` or `Box Office
Report`. A forwarded invoice is only marked read, labeled `Accounts Payable`,
and removed from the inbox **after sending succeeds**. Other labels remain.
The SQLite ledger records every applied action and prevents forwarding the same
invoice twice. Advertisement deletion additionally requires `--apply` and
`--allow-delete`; all other advertisement results stay reviewable in preview.