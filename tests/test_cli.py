from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from miti.cli import app
from miti.config import Settings
from miti.gmail import GoogleGmailClient
from miti.normalize import normalize_message


@pytest.fixture
def workspace() -> Path:
    path = Path(".test-artifacts")
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir()
    yield path
    shutil.rmtree(path)


def test_supported_commands_render_rich_tables(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    fixture = {
        "id": "one", "threadId": "one",
        "payload": {"headers": [
            {"name": "From", "value": "Vendor <billing@example.test>"},
            {"name": "Subject", "value": "Shift Report Tuesday"},
        ]},
    }
    fixture_path = workspace / "message.json"
    fixture_path.write_text(json.dumps(fixture))
    runner = CliRunner()

    for command, title in (
        (["reports", "shift-reports", "--fixture", str(fixture_path)], "Shift Reports (Preview) (1)"),
        (["invoices", "--fixture", str(fixture_path)], "Invoices (Preview) (0)"),
    ):
        result = runner.invoke(app, command)
        assert result.exit_code == 0, result.output
        assert title in result.output

    monkeypatch.setattr(GoogleGmailClient, "authorize", lambda *_: None)
    result = runner.invoke(app, ["auth", str(fixture_path)])
    assert result.exit_code == 0, result.output
    assert "Authorization" in result.output

    auth_help = runner.invoke(app, ["auth", "--help"])
    assert auth_help.exit_code == 0, auth_help.output
    assert "client_secret" in auth_help.output.lower()
    assert "--client-secret" not in auth_help.output

def test_scoped_processing_commands_only_plan_matching_messages(workspace: Path) -> None:
    fixture = {
        "id": "one", "threadId": "one",
        "payload": {"headers": [
            {"name": "From", "value": "Vendor <billing@example.test>"},
            {"name": "Subject", "value": "Shift Report Tuesday"},
        ]},
    }
    fixture_path = workspace / "message.json"
    fixture_path.write_text(json.dumps(fixture))
    runner = CliRunner()

    invoices = runner.invoke(app, ["invoices", "--fixture", str(fixture_path)])
    assert invoices.exit_code == 0, invoices.output
    assert "Invoices (Preview) (0)" in invoices.output

    box_office = runner.invoke(
        app, ["reports", "box-office", "--fixture", str(fixture_path)]
    )
    assert box_office.exit_code == 0, box_office.output
    assert "Box Office Reports (Preview) (0)" in box_office.output


def test_command_tree_only_exposes_auth_invoices_and_reports() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0, result.output
    assert "auth" in result.output
    assert "invoices" in result.output
    assert "reports" in result.output
    assert "advertisements" in result.output
    for removed_command in ("scan", "classify", "review", "rules", "labels"):
        assert not re.search(rf"^\s*│\s+{removed_command}\s", result.output, re.MULTILINE)


def test_advertisements_command_has_no_delete_option() -> None:
    result = CliRunner().invoke(app, ["advertisements", "--help"])

    assert result.exit_code == 0, result.output
    assert "--apply" in result.output
    assert "--batch-size" not in result.output
    assert "--allow-delete" not in result.output
    assert "--max-results" not in result.output


def test_invoices_require_a_forwarding_address_only_when_applying() -> None:
    runner = CliRunner()

    missing_address = runner.invoke(app, ["invoices", "--apply"])
    help_output = runner.invoke(app, ["invoices", "--help"])

    assert missing_address.exit_code != 0
    assert "--forwarding-address is required" in missing_address.output
    assert "invoice" in missing_address.output
    assert "--forwarding-address" in help_output.output


def test_apply_logs_start_and_loaded_message_count(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    raw_message = {
        "id": "one",
        "threadId": "one",
        "payload": {"headers": [
            {"name": "From", "value": "Newsletter <news@example.test>"},
            {"name": "Subject", "value": "Exclusive offer"},
            {"name": "List-Unsubscribe", "value": "<mailto:unsubscribe@example.test>"},
        ]},
    }
    logged: list[str] = []

    class FakeClient:
        def __init__(self) -> None:
            self.queries: list[str] = []

        def list_unread_inbox(self, query: str) -> list[dict[str, str]]:
            self.queries.append(query)
            return [{"id": "one"}]

        def get_messages(self, _message_ids: list[str]) -> list[dict[str, object]]:
            return [raw_message]

        def get_attachment(self, _message_id: str, _attachment_id: str) -> bytes:
            raise AssertionError("advertisements do not load attachments")

    client = FakeClient()
    monkeypatch.setattr(GoogleGmailClient, "from_credentials", lambda *_: client)
    monkeypatch.setattr(
        "miti.cli.load_settings",
        lambda: Settings(
            credentials_path=workspace / "credentials.json",
            client_secret_path=workspace / "client_secret.json",
            audit_db_path=workspace / "audit.sqlite3",
        ),
    )
    monkeypatch.setattr("miti.cli.apply_plan", lambda *_args: "applied")
    monkeypatch.setattr(
        "miti.cli.logger.info",
        lambda message, **values: logged.append(message.format(**values)),
    )

    result = CliRunner().invoke(app, ["advertisements", "--apply"])

    assert result.exit_code == 0, result.output
    assert logged[:2] == [
        "Starting advertisements apply; loading unread inbox messages.",
        "Loaded 1 unread inbox message(s); planning advertisements apply.",
    ]
    assert client.queries == ["is:unread in:inbox {category:promotions unsubscribe}"]


def test_invoices_use_a_gmail_side_invoice_and_pdf_filter(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.queries: list[str] = []

        def list_unread_inbox(self, query: str) -> list[dict[str, str]]:
            self.queries.append(query)
            return []

        def get_messages(self, _message_ids: list[str]) -> list[dict[str, object]]:
            return []

    client = FakeClient()
    monkeypatch.setattr(GoogleGmailClient, "from_credentials", lambda *_: client)
    monkeypatch.setattr(
        "miti.cli.load_settings",
        lambda: Settings(
            credentials_path=workspace / "credentials.json",
            client_secret_path=workspace / "client_secret.json",
            audit_db_path=workspace / "audit.sqlite3",
        ),
    )

    result = CliRunner().invoke(app, ["invoices"])

    assert result.exit_code == 0, result.output
    assert client.queries == ["is:unread in:inbox invoice has:attachment filename:pdf"]


def test_auth_replaces_existing_credentials(workspace: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    credentials_path = workspace / "credentials.json"
    credentials_path.write_text("old token")
    monkeypatch.setattr(GoogleGmailClient, "from_credentials", lambda *_: None)

    GoogleGmailClient.authorize(workspace / "client_secret.json", credentials_path)

    assert not credentials_path.exists()
