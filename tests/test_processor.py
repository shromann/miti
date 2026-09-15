from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from miti.ledger import Ledger
from miti.models import Attachment, Category, Classification, Message
from miti.processor import apply_plan, plan


class FakeGmail:
    def __init__(self) -> None:
        self.sent: list[bytes] = []
        self.modified: list[tuple[str, list[str], list[str]]] = []
        self.deleted: list[str] = []

    def send_raw(self, raw_message: bytes, thread_id: str | None = None) -> None:
        self.sent.append(raw_message)

    def ensure_label(self, name: str) -> str:
        return f"label:{name}"

    def modify_message(self, message_id: str, add_label_ids: list[str], remove_label_ids: list[str]) -> None:
        self.modified.append((message_id, add_label_ids, remove_label_ids))

    def delete_message(self, message_id: str) -> None:
        self.deleted.append(message_id)


def invoice_message() -> Message:
    return Message(
        "message-1", "thread-1", "billing@example.test", "example.test", "Invoice 1",
        "Invoice is attached.", "", (Attachment("invoice.pdf", "application/pdf", data=b"%PDF-1.4 test"),),
    )


@pytest.fixture
def workspace() -> Path:
    path = Path(".test-artifacts")
    shutil.rmtree(path, ignore_errors=True)
    path.mkdir()
    yield path
    shutil.rmtree(path)


def test_invoice_forward_is_idempotent_and_mutates_only_after_send(workspace: Path) -> None:
    message = invoice_message()
    item = plan(message, Classification(Category.INVOICE, 0.9, ("test",)), False)
    assert item is not None
    client = FakeGmail()
    ledger = Ledger(workspace / "audit.sqlite3")
    try:
        assert apply_plan(item, message, client, ledger, "bills@example.test", False) == "applied"
        assert len(client.sent) == 1
        assert client.modified == [("message-1", ["label:Accounts Payable"], ["UNREAD", "INBOX"])]
        assert apply_plan(item, message, client, ledger, "bills@example.test", False).startswith("resumed")
        assert len(client.sent) == 1
    finally:
        ledger.close()


def test_advertisement_needs_explicit_delete_consent(workspace: Path) -> None:
    message = invoice_message()
    item = plan(message, Classification(Category.ADVERTISEMENT, 0.95, ("test",)), False)
    assert item is not None
    client = FakeGmail()
    ledger = Ledger(workspace / "audit.sqlite3")
    try:
        assert "not applied" in apply_plan(item, message, client, ledger, None, False)
        assert not client.deleted
    finally:
        ledger.close()
