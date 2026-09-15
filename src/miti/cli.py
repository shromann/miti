"""Typer command line interface for preview-first Gmail automation."""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
from collections.abc import Iterable
from dataclasses import asdict
from pathlib import Path
from typing import Annotated, Any

import typer

from .config import Settings, load_settings
from .gmail import GmailClient, GoogleGmailClient
from .ledger import Ledger
from .models import Classification, Message
from .normalize import normalize_message
from .processor import LABELS, apply_plan, plan
from .rules import RuleSet, classify as classify_message

app = typer.Typer(help="Rule-first, auditable Gmail automation.")
rules_app = typer.Typer(help="Inspect and validate deterministic classification rules.")
labels_app = typer.Typer(help="Manage the labels used by miti.")
app.add_typer(rules_app, name="rules")
app.add_typer(labels_app, name="labels")


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {"level": record.levelname, "event": record.getMessage()}
        fields = getattr(record, "fields", None)
        if isinstance(fields, dict):
            payload.update(fields)
        return json.dumps(payload, ensure_ascii=False)


def _configure_logging() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(_JsonFormatter())
    logger = logging.getLogger("miti")
    if not logger.handlers:
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def _settings(ctx: typer.Context) -> Settings:
    settings = ctx.obj
    if not isinstance(settings, Settings):
        raise RuntimeError("settings were not initialized")
    return settings


@app.callback()
def root(
    ctx: typer.Context,
    config: Annotated[Path | None, typer.Option("--config", help="TOML configuration path.")] = None,
) -> None:
    """Use --apply to mutate Gmail; previews are the default."""
    _configure_logging()
    ctx.obj = load_settings(config)


def _load_fixture(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = data.get("messages", [data])
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise typer.BadParameter("fixture must be a Gmail message object or a JSON array of messages")
    return data


def _messages(
    settings: Settings, fixture: Path | None, max_results: int, include_attachment_data: bool = False
) -> tuple[list[Message], GmailClient | None]:
    if fixture:
        return [normalize_message(raw, include_attachment_data=include_attachment_data) for raw in _load_fixture(fixture)], None
    client = GoogleGmailClient.from_credentials(settings.credentials_path, settings.client_secret_path)
    stubs = client.list_unread_inbox(max_results)
    messages = [
        normalize_message(
            client.get_message(str(stub["id"])),
            client.get_attachment,
            include_attachment_data=include_attachment_data,
        )
        for stub in stubs
    ]
    return messages, client


def _classifications(messages: Iterable[Message], settings: Settings) -> list[tuple[Message, Classification]]:
    rules = RuleSet.from_settings(settings)
    return [(message, classify_message(message, rules)) for message in messages]


@app.command()
def auth(
    ctx: typer.Context,
    client_secret: Annotated[Path | None, typer.Option("--client-secret", help="OAuth desktop client JSON.")] = None,
) -> None:
    """Authorize Gmail with only gmail.modify and gmail.send scopes."""
    settings = _settings(ctx)
    secret = client_secret or settings.client_secret_path
    GoogleGmailClient.authorize(secret, settings.credentials_path)
    typer.echo(json.dumps({"status": "authorized", "credentials_path": str(settings.credentials_path)}))


@app.command()
def scan(
    ctx: typer.Context,
    fixture: Annotated[Path | None, typer.Option("--fixture", help="Local Gmail JSON fixture; no network.")] = None,
    max_results: Annotated[int, typer.Option(min=1, max=500)] = 50,
) -> None:
    """Read only the fixed Gmail query is:unread in:inbox and normalize it."""
    messages, _ = _messages(_settings(ctx), fixture, max_results)
    typer.echo(json.dumps({"query": "is:unread in:inbox", "count": len(messages), "messages": [m.as_dict() for m in messages]}, ensure_ascii=False))


@app.command()
def classify(
    ctx: typer.Context,
    fixture: Annotated[Path | None, typer.Option("--fixture", help="Local Gmail JSON fixture; no network.")] = None,
    max_results: Annotated[int, typer.Option(min=1, max=500)] = 50,
) -> None:
    """Classify normalized unread inbox messages with reasons and confidence."""
    settings = _settings(ctx)
    messages, _ = _messages(settings, fixture, max_results)
    records = [
        {"message_id": message.message_id, "subject": message.subject, "sender": message.sender_email, **result.as_dict()}
        for message, result in _classifications(messages, settings)
    ]
    typer.echo(json.dumps({"count": len(records), "classifications": records}, ensure_ascii=False))


@app.command()
def process(
    ctx: typer.Context,
    fixture: Annotated[Path | None, typer.Option("--fixture", help="Local Gmail JSON fixture; preview only.")] = None,
    apply: Annotated[bool, typer.Option("--apply", help="Perform allowed mutations.")] = False,
    allow_delete: Annotated[bool, typer.Option("--allow-delete", help="Allow high-confidence advertisement deletion.")] = False,
    max_results: Annotated[int, typer.Option(min=1, max=500)] = 50,
) -> None:
    """Plan actions by default; --apply is required for every mutation."""
    if apply and fixture:
        raise typer.BadParameter("--apply cannot use a fixture because it has no Gmail API client")
    settings = _settings(ctx)
    messages, client = _messages(settings, fixture, max_results, include_attachment_data=apply)
    planned = [
        (message, result, candidate)
        for message, result in _classifications(messages, settings)
        if (candidate := plan(message, result, allow_delete)) is not None
    ]
    output: list[dict[str, Any]] = []
    ledger = Ledger(settings.audit_db_path) if apply else None
    try:
        for message, result, candidate in planned:
            status = "preview"
            if apply:
                assert client is not None and ledger is not None
                status = apply_plan(candidate, message, client, ledger, settings.forwarding_address, allow_delete)
                logging.getLogger("miti").info(
                    "process_action",
                    extra={"fields": {"action": candidate.action, "message_id": message.message_id, "status": status}},
                )
            output.append({**candidate.as_dict(), "confidence": result.confidence, "reasons": list(result.reasons), "status": status})
    finally:
        if ledger:
            ledger.close()
    typer.echo(json.dumps({"mode": "apply" if apply else "preview", "actions": output}, ensure_ascii=False))


@rules_app.command("validate")
def validate_rules(ctx: typer.Context) -> None:
    """Validate configuration and expose the fixed rule precedence."""
    settings = _settings(ctx)
    warnings: list[str] = []
    if not settings.forwarding_address:
        warnings.append("forwarding_address is unset; invoice apply will be blocked")
    typer.echo(json.dumps({
        "valid": True,
        "warnings": warnings,
        "precedence": ["shift_report", "box_office_report", "invoice", "advertisement", "unclassified"],
        "trusted_senders": len(settings.trusted_senders),
        "trusted_domains": len(settings.trusted_domains),
    }))


@labels_app.command("ensure")
def ensure_labels(ctx: typer.Context) -> None:
    """Create missing required labels without changing message state."""
    settings = _settings(ctx)
    client = GoogleGmailClient.from_credentials(settings.credentials_path, settings.client_secret_path)
    result = {name: client.ensure_label(name) for name in LABELS.values()}
    typer.echo(json.dumps({"labels": result}, ensure_ascii=False))


@app.command()
def review(ctx: typer.Context, limit: Annotated[int, typer.Option(min=1, max=500)] = 50) -> None:
    """Show the most recent mutable actions from the SQLite audit ledger."""
    path = _settings(ctx).audit_db_path
    if not path.exists():
        typer.echo(json.dumps({"actions": []}))
        return
    with sqlite3.connect(path) as connection:
        rows = connection.execute(
            """SELECT message_id, thread_id, action, status, details, created_at
               FROM actions ORDER BY id DESC LIMIT ?""", (limit,)
        ).fetchall()
    keys = ("message_id", "thread_id", "action", "status", "details", "created_at")
    typer.echo(json.dumps({"actions": [dict(zip(keys, row, strict=True)) for row in rows]}, ensure_ascii=False))


def main() -> None:
    app()
