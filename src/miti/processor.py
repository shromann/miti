"""Preview-first processing, MIME forwarding, and constrained PDF retrieval."""

from __future__ import annotations

from email.message import EmailMessage
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlparse

import httpx

from .gmail import GmailClient
from .ledger import Ledger
from .models import Category, Classification, Message, PlannedAction

LABELS = {
    Category.INVOICE: "Accounts Payable",
    Category.SHIFT_REPORT: "Reports/GAC Daily Reports",
    Category.BOX_OFFICE_REPORT: "Box Office Report",
}
MAX_PDF_BYTES = 5 * 1024 * 1024


def download_pdf(url: str, max_bytes: int = MAX_PDF_BYTES) -> bytes:
    """Fetch only a direct or HTTPS-redirected PDF with explicit size limits."""
    if urlparse(url).scheme != "https":
        raise ValueError("invoice PDF URL must use HTTPS")
    current_url = url
    with httpx.Client(follow_redirects=False, timeout=httpx.Timeout(10.0)) as client:
        for _ in range(4):
            with client.stream("GET", current_url, headers={"Accept": "application/pdf"}) as response:
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("PDF redirect has no location")
                    current_url = urljoin(str(response.url), location)
                    if urlparse(current_url).scheme != "https":
                        raise ValueError("PDF redirect left HTTPS")
                    continue
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                if content_type != "application/pdf":
                    raise ValueError(f"PDF URL returned unsafe content type {content_type!r}")
                declared = response.headers.get("content-length")
                if declared and int(declared) > max_bytes:
                    raise ValueError("PDF exceeds maximum download size")
                content = b"".join(chunk for chunk in response.iter_bytes() if chunk)
            if len(content) > max_bytes:
                raise ValueError("PDF exceeds maximum download size")
            if not content.startswith(b"%PDF-"):
                raise ValueError("download does not have a PDF signature")
            return content
    raise ValueError("PDF exceeded maximum redirects")


def plan(message: Message, classification: Classification, allow_delete: bool) -> PlannedAction | None:
    if classification.category in LABELS:
        action = "forward_invoice" if classification.category is Category.INVOICE else "label_report"
        return PlannedAction(action, message.message_id, message.thread_id, classification.category, LABELS[classification.category])
    if classification.category is Category.ADVERTISEMENT:
        if classification.confidence < 0.9:
            return PlannedAction(
                "review_advertisement",
                message.message_id,
                message.thread_id,
                classification.category,
                "confidence below deletion threshold",
            )
        detail = "eligible only with --apply --allow-delete" if allow_delete else "review required; --allow-delete absent"
        return PlannedAction("delete_advertisement", message.message_id, message.thread_id, classification.category, detail)
    return None


def _invoice_pdf(message: Message) -> tuple[str, bytes]:
    for attachment in message.attachments:
        if attachment.is_pdf and attachment.data and attachment.data.startswith(b"%PDF-"):
            return attachment.filename or "invoice.pdf", attachment.data
    for url in message.urls:
        if url.lower().startswith("https://"):
            filename = PurePosixPath(urlparse(url).path).name or "invoice.pdf"
            if not filename.lower().endswith(".pdf"):
                filename = f"{filename}.pdf"
            return filename, download_pdf(url)
    raise ValueError("invoice had no validated PDF attachment or safe PDF link")


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
    allow_delete: bool,
) -> str:
    if item.action == "label_report":
        label_id = client.ensure_label(item.details)
        client.modify_message(message.message_id, [label_id], ["UNREAD"])
        ledger.record(message.message_id, message.thread_id, item.action, "success", item.details)
        return "applied"
    if item.action == "delete_advertisement":
        if not allow_delete:
            return "not applied: --allow-delete is required"
        client.delete_message(message.message_id)
        ledger.record(message.message_id, message.thread_id, item.action, "success", "deleted")
        return "applied"
    if item.action == "review_advertisement":
        return "not applied: advertisement confidence is below deletion threshold"
    if item.action == "forward_invoice":
        if ledger.was_successful(message.message_id, item.action):
            # A prior send may have succeeded just before a label mutation failed.
            # Reconcile state without ever transmitting a second invoice.
            label_id = client.ensure_label(item.details)
            client.modify_message(message.message_id, [label_id], ["UNREAD", "INBOX"])
            return "resumed: invoice was already forwarded"
        if not forwarding_address:
            raise ValueError("forwarding_address is required to forward invoices")
        filename, pdf = _invoice_pdf(message)
        client.send_raw(_forward_message(message, forwarding_address, filename, pdf), message.thread_id)
        # Record immediately after the irreversible operation. A restart during
        # following state changes will reconcile rather than resend.
        ledger.record(message.message_id, message.thread_id, item.action, "success", f"sent {filename}")
        label_id = client.ensure_label(item.details)
        client.modify_message(message.message_id, [label_id], ["UNREAD", "INBOX"])
        return "applied"
    raise ValueError(f"unknown planned action {item.action}")
