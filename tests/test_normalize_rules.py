from __future__ import annotations

import base64

from miti.models import Category
from miti.normalize import normalize_message
from miti.rules import RuleSet, classify


def encoded(value: str | bytes) -> str:
    source = value.encode() if isinstance(value, str) else value
    return base64.urlsafe_b64encode(source).decode().rstrip("=")


def raw_message(subject: str, parts: list[dict], headers: list[dict] | None = None) -> dict:
    return {
        "id": "m-1",
        "threadId": "t-1",
        "payload": {
            "headers": [{"name": "From", "value": "Accounts <billing@vendor.example>"}, {"name": "Subject", "value": subject}] + (headers or []),
            "parts": parts,
        },
    }


def test_normalizes_nested_bodies_urls_and_sender_address() -> None:
    raw = raw_message("Invoice 41", [
        {"mimeType": "multipart/alternative", "parts": [
            {"mimeType": "text/plain", "body": {"data": encoded("Pay https://vendor.example/invoice.pdf")}},
            {"mimeType": "text/html", "body": {"data": encoded("<a href='https://vendor.example/invoice.pdf'>PDF</a>")}},
        ]},
        {"mimeType": "application/pdf", "filename": "bill.pdf", "body": {"data": encoded(b"%PDF-1.4")}},
    ])
    message = normalize_message(raw, include_attachment_data=True)
    assert message.sender_email == "billing@vendor.example"
    assert message.sender_domain == "vendor.example"
    assert message.text_body == "Pay https://vendor.example/invoice.pdf"
    assert message.attachments[0].data == b"%PDF-1.4"
    assert message.urls == ("https://vendor.example/invoice.pdf",)


def test_classifier_precedence_and_conservative_invoice() -> None:
    rules = RuleSet()
    shift = normalize_message(raw_message("Shift Report and invoice", []))
    assert classify(shift, rules).category is Category.SHIFT_REPORT

    no_pdf = normalize_message(raw_message("Invoice 88", [{"mimeType": "text/plain", "body": {"data": encoded("Invoice due")}}]))
    assert classify(no_pdf, rules).category is Category.UNCLASSIFIED

    linked_pdf = normalize_message(raw_message("Invoice 88", [
        {"mimeType": "text/plain", "body": {"data": encoded("Invoice due: https://vendor.example/invoice.pdf")}},
    ]))
    linked_result = classify(linked_pdf, rules)
    assert linked_result.category is Category.UNCLASSIFIED
    assert "invoice wording lacks a PDF attachment" in linked_result.reasons

    invoice = normalize_message(raw_message("Invoice 88", [
        {"mimeType": "text/plain", "body": {"data": encoded("Invoice due")}},
        {"mimeType": "application/pdf", "filename": "invoice.pdf", "body": {"data": encoded(b"%PDF-1.4")}},
    ]))
    result = classify(invoice, rules)
    assert result.category is Category.INVOICE
    assert "PDF attachment found" in result.reasons


def test_advertisement_requires_multiple_automation_signals() -> None:
    rules = RuleSet()
    automated = normalize_message(raw_message("Special offer", [], [
        {"name": "List-ID", "value": "offers.example"},
        {"name": "List-Unsubscribe", "value": "<https://example.test/unsubscribe>"},
    ]))
    result = classify(automated, rules)
    assert result.category is Category.ADVERTISEMENT
    assert result.automation_confidence >= 0.9
    assert {"list_header", "list_unsubscribe"} <= set(result.automation_reasons)
