"""Deterministic, deliberately conservative mail classification rules."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .config import Settings
from .models import Category, Classification, Message

_INVOICE = re.compile(r"\b(invoice|tax invoice|amount due|bill(?:ing)?)\b", re.IGNORECASE)
_MARKETING = re.compile(r"\b(sale|offer|discount|unsubscribe|promotion|newsletter|deal)\b", re.IGNORECASE)
_AUTOMATED_TEXT = re.compile(r"\b(do not reply|please do not reply|automated (?:email|message))\b", re.IGNORECASE)
_CONVERSATIONAL_TEXT = re.compile(r"\b(hi|hello|thanks|thank you|regards|cheers)\b", re.IGNORECASE)


@dataclass(frozen=True)
class RuleSet:
    trusted_senders: frozenset[str]
    trusted_domains: frozenset[str]
    known_vendors: frozenset[str]
    known_contacts: frozenset[str]

    @classmethod
    def from_settings(cls, settings: Settings) -> "RuleSet":
        return cls(
            settings.trusted_senders,
            settings.trusted_domains,
            settings.known_vendors,
            settings.known_contacts,
        )


def classify(message: Message, rules: RuleSet) -> Classification:
    subject = message.subject.strip()
    content = f"{subject}\n{message.text_body}\n{message.html_body}"
    automation_confidence, automation_reasons = _automation_evidence(message, rules, content)
    if subject.lower().startswith("shift report"):
        return Classification(
            Category.SHIFT_REPORT, 1.0, ("subject starts with 'Shift Report'",),
            automation_confidence, automation_reasons,
        )
    if subject == "BOR - Golden Age Cinema- Nightly":
        return Classification(
            Category.BOX_OFFICE_REPORT, 1.0, ("subject exactly matches Box Office rule",),
            automation_confidence, automation_reasons,
        )

    evidence = bool(_INVOICE.search(content))
    trusted = message.sender_email in rules.trusted_senders or message.sender_domain in rules.trusted_domains
    vendor = message.sender_email in rules.known_vendors or message.sender_domain in rules.known_vendors
    pdf_attachment = any(item.is_pdf for item in message.attachments)
    safe_pdf_link = any(url.lower().startswith("https://") for url in message.urls)
    if evidence and (pdf_attachment or safe_pdf_link):
        reasons = ["invoice wording found"]
        reasons.append("PDF attachment found" if pdf_attachment else "HTTPS PDF link found")
        if trusted or vendor:
            reasons.append("sender matches configured trusted vendor/sender")
        return Classification(
            Category.INVOICE, 0.98 if trusted or vendor else 0.90, tuple(reasons),
            automation_confidence, automation_reasons,
        )

    marketing = bool(_MARKETING.search(content))
    human = "known_contact" in automation_reasons or "reply_or_forward" in automation_reasons
    if automation_confidence >= 0.9 and marketing and not human:
        return Classification(
            Category.ADVERTISEMENT,
            min(0.99, 0.80 + automation_confidence * 0.2),
            ("high automation confidence", "marketing wording found"),
            automation_confidence,
            automation_reasons,
        )
    reasons = ["no high-precedence deterministic rule matched"]
    if evidence:
        reasons.append("invoice wording lacks a PDF attachment or safe HTTPS PDF link")
    return Classification(Category.UNCLASSIFIED, 0.0, tuple(reasons), automation_confidence, automation_reasons)


def _automation_evidence(message: Message, rules: RuleSet, content: str) -> tuple[float, tuple[str, ...]]:
    """Return independently explainable automation evidence with a human bias."""
    score = 0.0
    reasons: list[str] = []

    def add(condition: bool, weight: float, reason: str) -> None:
        nonlocal score
        if condition:
            score += weight
            reasons.append(reason)

    headers = message.headers
    add(bool(headers.get("auto-submitted")) and headers["auto-submitted"].lower() != "no", 0.45, "auto_submitted")
    add(headers.get("precedence", "").lower() in {"bulk", "list", "junk"}, 0.35, "precedence_bulk")
    add(any(name.startswith("list-") for name in headers), 0.45, "list_header")
    add(bool(headers.get("list-unsubscribe")), 0.45, "list_unsubscribe")
    add(any(name in headers for name in ("x-campaign-id", "x-mailing-list", "x-bulkmail")), 0.25, "bulk_mail_header")
    local_part = message.sender_email.partition("@")[0]
    add(local_part in {"noreply", "no-reply", "donotreply", "do-not-reply"}, 0.20, "no_reply_sender")
    add(message.sender_email in rules.known_vendors or message.sender_domain in rules.known_vendors, 0.15, "known_vendor")
    add(bool(_AUTOMATED_TEXT.search(content)), 0.15, "automated_template")

    human_signals = 0.0
    if message.sender_email in rules.known_contacts:
        human_signals += 0.55
        reasons.append("known_contact")
    if subject_is_reply_or_forward(message.subject):
        human_signals += 0.25
        reasons.append("reply_or_forward")
    if _CONVERSATIONAL_TEXT.search(content) and not any(name.startswith("list-") for name in headers):
        human_signals += 0.15
        reasons.append("conversational_language")
    return max(0.0, min(1.0, score - human_signals)), tuple(reasons)


def subject_is_reply_or_forward(subject: str) -> bool:
    return bool(re.match(r"^\s*(?:re|fw|fwd)\s*:", subject, re.IGNORECASE))
