from __future__ import annotations

import base64
import json
import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from miti.cli import app


@pytest.fixture
def workspace() -> Path:
    path = Path(".test-artifacts")
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir()
    yield path
    shutil.rmtree(path)


def test_classify_fixture_without_gmail_credentials(workspace: Path) -> None:
    fixture = {
        "id": "one", "threadId": "one",
        "payload": {"headers": [
            {"name": "From", "value": "Vendor <billing@example.test>"},
            {"name": "Subject", "value": "Shift Report Tuesday"},
        ], "parts": [{"mimeType": "text/plain", "body": {"data": base64.urlsafe_b64encode(b"report").decode()}}]},
    }
    fixture_path = workspace / "message.json"
    config_path = workspace / "config.toml"
    fixture_path.write_text(json.dumps(fixture))
    config_path.write_text('forwarding_address = "bills@example.test"\n')
    result = CliRunner().invoke(app, ["--config", str(config_path), "classify", "--fixture", str(fixture_path)])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["classifications"][0]["category"] == "shift_report"
