"""Typer command line interface for preview-first Gmail automation."""

from __future__ import annotations

import json
import sys
from collections.abc import Iterable
from pathlib import Path
from typing import Annotated

import typer
from loguru import logger
from rich.console import Console
from rich.table import Table

from .config import Settings, load_settings
from .gmail import GmailClient, GoogleGmailClient, UNREAD_INBOX_QUERY
from .ledger import Ledger
from .models import Category, Classification, Message
from .normalize import normalize_message
from .processor import apply_plan, plan
from .rules import RuleSet, classify as classify_message

app = typer.Typer(help="Rule-first, auditable Gmail automation.")
reports_app = typer.Typer(help="Process report emails by report type.")
app.add_typer(reports_app, name="reports")
SHIFT_REPORT_QUERY = f'{UNREAD_INBOX_QUERY} subject:"Shift Report"'
BOX_OFFICE_REPORT_QUERY = f'{UNREAD_INBOX_QUERY} subject:"BOR - Golden Age Cinema- Nightly"'
INVOICE_QUERY = f"{UNREAD_INBOX_QUERY} invoice has:attachment filename:pdf"
ADVERTISEMENT_QUERY = f"{UNREAD_INBOX_QUERY} {{category:promotions unsubscribe}}"


def _table(title: str, columns: tuple[str, ...], rows: Iterable[tuple[str, ...]]) -> None:
    table = Table(title=title, expand=True)
    for column in columns:
        table.add_column(column)
    for row in rows:
        table.add_row(*row)
    Console().print(table)


def _configure_logging() -> None:
    logger.remove()
    logger.add(
        sys.stderr,
        level="INFO",
        colorize=sys.stderr.isatty(),
        format="<green>{time:HH:mm:ss}</green> | <level>{level}</level> | <level>{message}</level>",
    )


def _settings(ctx: typer.Context) -> Settings:
    settings = ctx.obj
    if not isinstance(settings, Settings):
        raise RuntimeError("settings were not initialized")
    return settings


@app.callback()
def root(
    ctx: typer.Context,
) -> None:
    """Use --apply to mutate Gmail; previews are the default."""
    _configure_logging()
    ctx.obj = load_settings()


def _load_fixture(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("messages", [data])
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise typer.BadParameter("fixture must be a Gmail message object or a JSON array of messages")
    return data


def _classifications(messages: Iterable[Message]) -> list[tuple[Message, Classification]]:
    rules = RuleSet()
    return [(message, classify_message(message, rules)) for message in messages]


@app.command()
def auth(
    ctx: typer.Context,
    client_secret: Annotated[Path | None, typer.Argument(help="OAuth desktop client JSON.")] = None,
) -> None:
    """Authorize Gmail with only gmail.modify and gmail.send scopes."""
    settings = _settings(ctx)
    secret = client_secret or settings.client_secret_path
    GoogleGmailClient.authorize(secret, settings.credentials_path)
    _table(
        "Authorization",
        ("Status", "Credentials Path"),
        (("authorized", str(settings.credentials_path)),),
    )


def _process(
    ctx: typer.Context,
    *,
    fixture: Path | None,
    apply: bool,
    forwarding_address: str | None,
    gmail_query: str | None,
    categories: frozenset[Category],
    title: str,
) -> None:
    if apply and fixture:
        raise typer.BadParameter("--apply cannot use a fixture because it has no Gmail API client")
    if apply and Category.INVOICE in categories and not forwarding_address:
        raise typer.BadParameter("--forwarding-address is required when applying invoice forwarding")
    settings = _settings(ctx)
    client: GmailClient | None = None
    if apply:
        logger.info("Starting {title} apply; loading unread inbox messages.", title=title.lower())
    if fixture:
        messages = [normalize_message(raw) for raw in _load_fixture(fixture)]
    else:
        client = GoogleGmailClient.from_credentials(settings.credentials_path, settings.client_secret_path)
        stubs = client.list_unread_inbox(gmail_query) if gmail_query else client.list_unread_inbox()
        raw_messages = client.get_messages([str(stub["id"]) for stub in stubs])
        messages = [
            normalize_message(
                raw_message,
                client.get_attachment,
                include_attachment_data=False,
            )
            for raw_message in raw_messages
        ]
        logger.info(
            "Loaded {message_count} unread inbox message(s); planning {title} apply.",
            message_count=len(messages),
            title=title.lower(),
        )
    rows: list[tuple[str, ...]] = []
    ledger = Ledger(settings.audit_db_path) if apply else None
    try:
        planned = [
            (message, result, candidate)
            for message, result in _classifications(messages)
            if result.category in categories and (candidate := plan(message, result)) is not None
        ]
        for message, result, candidate in planned:
            status = "preview"
            if apply:
                assert client is not None and ledger is not None
                status = apply_plan(candidate, message, client, ledger, forwarding_address)
                logger.info(
                    "Processed {category} message {message_id}: {action} ({status})",
                    category=candidate.category.value,
                    message_id=message.message_id,
                    action=candidate.action,
                    status=status,
                )
            rows.append(
                (
                    f"{candidate.message_id}\nThread: {candidate.thread_id}",
                    candidate.action,
                    candidate.category.value,
                    status,
                    f"{result.confidence:.0%}",
                    f"{candidate.details}\n" + "\n".join(result.reasons),
                )
            )
    finally:
        if ledger:
            ledger.close()
    _table(
        f"{title} ({'Apply' if apply else 'Preview'}) ({len(rows)})",
        ("Message", "Action", "Category", "Status", "Confidence", "Details"),
        rows,
    )


@app.command()
def invoices(
    ctx: typer.Context,
    fixture: Annotated[Path | None, typer.Option("--fixture", help="Local Gmail JSON fixture; preview only.")] = None,
    apply: Annotated[bool, typer.Option("--apply", help="Forward attached-PDF invoices.")] = False,
    forwarding_address: Annotated[
        str | None,
        typer.Option("--forwarding-address", help="Address to receive forwarded invoices."),
    ] = None,
) -> None:
    """Preview or forward invoice emails that already have a PDF attachment."""
    _process(
        ctx,
        fixture=fixture,
        apply=apply,
        forwarding_address=forwarding_address,
        gmail_query=INVOICE_QUERY,
        categories=frozenset({Category.INVOICE}),
        title="Invoices",
    )


@reports_app.command("shift-reports")
def shift_reports(
    ctx: typer.Context,
    fixture: Annotated[Path | None, typer.Option("--fixture", help="Local Gmail JSON fixture; preview only.")] = None,
    apply: Annotated[bool, typer.Option("--apply", help="Mark matching shift reports read and label them.")] = False,
) -> None:
    """Preview or process messages whose subject starts with Shift Report."""
    _process(
        ctx,
        fixture=fixture,
        apply=apply,
        forwarding_address=None,
        gmail_query=SHIFT_REPORT_QUERY,
        categories=frozenset({Category.SHIFT_REPORT}),
        title="Shift Reports",
    )


@reports_app.command("box-office")
def box_office_reports(
    ctx: typer.Context,
    fixture: Annotated[Path | None, typer.Option("--fixture", help="Local Gmail JSON fixture; preview only.")] = None,
    apply: Annotated[bool, typer.Option("--apply", help="Mark matching box-office reports read and label them.")] = False,
) -> None:
    """Preview or process messages with the exact Box Office subject."""
    _process(
        ctx,
        fixture=fixture,
        apply=apply,
        forwarding_address=None,
        gmail_query=BOX_OFFICE_REPORT_QUERY,
        categories=frozenset({Category.BOX_OFFICE_REPORT}),
        title="Box Office Reports",
    )


@app.command()
def advertisements(
    ctx: typer.Context,
    fixture: Annotated[Path | None, typer.Option("--fixture", help="Local Gmail JSON fixture; preview only.")] = None,
    apply: Annotated[bool, typer.Option("--apply", help="Mark matching advertisements read and label them.")] = False,
) -> None:
    """Preview or label matching advertisements and mark them read."""
    _process(
        ctx,
        fixture=fixture,
        apply=apply,
        forwarding_address=None,
        gmail_query=ADVERTISEMENT_QUERY,
        categories=frozenset({Category.ADVERTISEMENT}),
        title="Advertisements",
    )


def main() -> None:
    app()
