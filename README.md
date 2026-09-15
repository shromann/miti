# miti

## Installation and setup

Miti is a Python 3.12+ `src`-layout package. Install it (and its Gmail OAuth
client) with:

```bash
uv sync --group dev
# or: python -m pip install -e .
```

Create a Google OAuth **Desktop app** client, download its JSON, then authorize:

```bash
miti auth ./oauth-client.json
```

OAuth requests only `gmail.modify` and `gmail.send`. The resulting credential
file and audit ledger are stored in `~/.config/miti/`, and tokens are never printed. `miti auth` always opens a
fresh consent flow and replaces the stored credential, so run it again after
changing required scopes. Do not commit either OAuth JSON file.

## Commands

All Gmail reads are limited to unread inbox messages. The report commands additionally
filter their Gmail search by the relevant report subject before loading full messages.
Advertisement processing is limited to messages Gmail categorizes as Promotions or that
contain `unsubscribe`, before applying its local advertisement classification rules.
Processing commands handle all matching unread inbox messages.

`miti invoices` asks Gmail for unread inbox messages containing `invoice` with
an attached PDF before it fetches message details. `miti` retrieves only the
headers, MIME structure, and text/HTML content needed to classify each matching
message. It reads PDF bytes only for a classified invoice being forwarded with
`--apply`, because Gmail's API requires those bytes to create the forwarded
message with its attachment.

```bash
miti auth                         # authorize Gmail access
miti invoices                     # preview invoice forwarding actions
miti invoices --apply --forwarding-address your-xero-bills-address@example.com
miti reports shift-reports        # preview shift-report actions
miti reports shift-reports --apply
miti reports box-office           # preview box-office-report actions
miti reports box-office --apply
miti advertisements               # preview advertisement actions
miti advertisements --apply       # label advertisements and mark them read
```

For offline demonstrations and tests, pass a Gmail API message JSON object or
array with `--fixture` to any preview processing command:

```bash
miti invoices --fixture examples/message.json
```

## Safety and deterministic rules

Classification has fixed precedence: a subject starting `Shift Report`, then
the exact Box Office subject `BOR - Golden Age Cinema- Nightly`, then
conservative invoices, then advertisements. Invoice forwarding requires both
invoice evidence and an existing PDF attachment. Invoice emails that only link
to a PDF are left untouched.

Reports are marked read, receive `Reports/GAC Daily Reports` or `Box Office
Report`, and are removed from the inbox. A forwarded invoice is only marked read, labeled `Accounts Payable`,
and removed from the inbox **after sending succeeds**. Other labels remain.
The SQLite ledger records every applied action and prevents forwarding the same
invoice twice. Advertisements are labeled `Advertisements` and marked read
when `miti advertisements --apply` is used, then removed from the inbox.