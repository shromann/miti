"""Preview-first processing and MIME forwarding."""

from __future__ import annotations

from email.message import EmailMessage

from .gmail import GmailClient
from .ledger import Ledger
from .models import Category, Classification, Message, PlannedAction

LABELS = {
    Category.INVOICE: "Accounts Payable",
    Category.SHIFT_REPORT: "Reports/GAC Daily Reports",
    Category.BOX_OFFICE_REPORT: "Box Office Report",
    Category.ADVERTISEMENT: "Advertisements",
}


def plan(message: Message, classification: Classification) -> PlannedAction | None:
    if classification.category in LABELS:
        action = "forward_invoice" if classification.category is Category.INVOICE else "label_report"
        return PlannedAction(action, message.message_id, message.thread_id, classification.category, LABELS[classification.category])
    return None


def _invoice_pdf(message: Message, client: GmailClient) -> tuple[str, bytes]:
    for attachment in message.attachments:
        if not attachment.is_pdf:
            continue
        pdf = attachment.data
        if pdf is None and attachment.attachment_id:
            pdf = client.get_attachment(message.message_id, attachment.attachment_id)
        if pdf and pdf.startswith(b"%PDF-"):
            return attachment.filename or "invoice.pdf", pdf
    raise ValueError("invoice had no validated PDF attachment")


def _forward_message(message: Message, destination: str, filename: str, pdf: bytes) -> bytes:
    outgoing = EmailMessage()
    outgoing["To"] = destination
    outgoing["Subject"] = f"Fwd: {message.subject}"
    outgoing.set_content(
        f"Forwarded by miti from {message.sender_email}\n"
        f"Original subject: {message.subject}\n\n{message.text_body}"
    )
    outgoing.add_attachment(pdf, maintype="application", subtype="pdf", filename=filename)
    return outgoing.as_bytes()


def apply_plan(
    item: PlannedAction,
    message: Message,
    client: GmailClient,
    ledger: Ledger,
    forwarding_address: str | None,
) -> str:
    if item.action == "label_report":
        label_id = client.ensure_label(item.details)
        remove_label_ids = (
            ["UNREAD", "INBOX"]
            if item.category in {Category.SHIFT_REPORT, Category.BOX_OFFICE_REPORT, Category.ADVERTISEMENT}
            else ["UNREAD"]
        )
        client.modify_message(message.message_id, [label_id], remove_label_ids)
        ledger.record(message.message_id, message.thread_id, item.action, "success", item.details)
        return "applied"
    if item.action == "forward_invoice":
        if ledger.was_successful(message.message_id, item.action):
            # A prior send may have succeeded just before a label mutation failed.
            # Reconcile state without ever transmitting a second invoice.
            label_id = client.ensure_label(item.details)
            client.modify_message(message.message_id, [label_id], ["UNREAD", "INBOX"])
            return "resumed: invoice was already forwarded"
        if not forwarding_address:
            raise ValueError("forwarding_address is required to forward invoices")
        filename, pdf = _invoice_pdf(message, client)
        client.send_raw(_forward_message(message, forwarding_address, filename, pdf), message.thread_id)
        # Record immediately after the irreversible operation. A restart during
        # following state changes will reconcile rather than resend.
        ledger.record(message.message_id, message.thread_id, item.action, "success", f"sent {filename}")
        label_id = client.ensure_label(item.details)
        client.modify_message(message.message_id, [label_id], ["UNREAD", "INBOX"])
        return "applied"
    raise ValueError(f"unknown planned action {item.action}")
