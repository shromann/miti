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
        self.attachment_requests: list[tuple[str, str]] = []

    def send_raw(self, raw_message: bytes, thread_id: str | None = None) -> None:
        self.sent.append(raw_message)

    def ensure_label(self, name: str) -> str:
        return f"label:{name}"

    def modify_message(self, message_id: str, add_label_ids: list[str], remove_label_ids: list[str]) -> None:
        self.modified.append((message_id, add_label_ids, remove_label_ids))

    def get_attachment(self, message_id: str, attachment_id: str) -> bytes:
        self.attachment_requests.append((message_id, attachment_id))
        return b"%PDF-1.4 test"

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
    item = plan(message, Classification(Category.INVOICE, 0.9, ("test",)))
    assert item is not None
    client = FakeGmail()
    ledger = Ledger(workspace / "audit.sqlite3")
    try:
        assert apply_plan(item, message, client, ledger, "bills@example.test") == "applied"
        assert len(client.sent) == 1
        assert client.modified == [("message-1", ["label:Accounts Payable"], ["UNREAD", "INBOX"])]
        assert apply_plan(item, message, client, ledger, "bills@example.test").startswith("resumed")
        assert len(client.sent) == 1
    finally:
        ledger.close()


def test_advertisement_is_labeled_and_marked_read(workspace: Path) -> None:
    message = invoice_message()
    item = plan(message, Classification(Category.ADVERTISEMENT, 0.95, ("test",)))
    assert item is not None
    assert item.action == "label_report"
    assert item.details == "Advertisements"
    client = FakeGmail()
    ledger = Ledger(workspace / "audit.sqlite3")
    try:
        assert apply_plan(item, message, client, ledger, None) == "applied"
        assert client.modified == [("message-1", ["label:Advertisements"], ["UNREAD", "INBOX"])]
    finally:
        ledger.close()


@pytest.mark.parametrize(
    ("category", "label"),
    [
        (Category.SHIFT_REPORT, "Reports/GAC Daily Reports"),
        (Category.BOX_OFFICE_REPORT, "Box Office Report"),
    ],
)
def test_reports_are_labeled_marked_read_and_archived(
    workspace: Path, category: Category, label: str
) -> None:
    message = invoice_message()
    item = plan(message, Classification(category, 1.0, ("test",)))
    assert item is not None
    client = FakeGmail()
    ledger = Ledger(workspace / "audit.sqlite3")
    try:
        assert apply_plan(item, message, client, ledger, None) == "applied"
        assert client.modified == [("message-1", [f"label:{label}"], ["UNREAD", "INBOX"])]
    finally:
        ledger.close()


def test_invoice_attachment_is_loaded_only_when_forwarding(workspace: Path) -> None:
    message = Message(
        "message-1",
        "thread-1",
        "billing@example.test",
        "example.test",
        "Invoice 1",
        "Invoice is attached.",
        "",
        (Attachment("invoice.pdf", "application/pdf", attachment_id="attachment-1"),),
    )
    item = plan(message, Classification(Category.INVOICE, 0.9, ("test",)))
    assert item is not None
    client = FakeGmail()
    ledger = Ledger(workspace / "audit.sqlite3")
    try:
        assert apply_plan(item, message, client, ledger, "bills@example.test") == "applied"
        assert client.attachment_requests == [("message-1", "attachment-1")]
        assert len(client.sent) == 1
    finally:
        ledger.close()
