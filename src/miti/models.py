"""Typed, provider-neutral representations used by rules and processing."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import StrEnum
from typing import Any


class Category(StrEnum):
    SHIFT_REPORT = "shift_report"
    BOX_OFFICE_REPORT = "box_office_report"
    INVOICE = "invoice"
    ADVERTISEMENT = "advertisement"
    UNCLASSIFIED = "unclassified"


@dataclass(frozen=True)
class Attachment:
    filename: str
    mime_type: str
    attachment_id: str | None = None
    data: bytes | None = None

    @property
    def is_pdf(self) -> bool:
        return self.mime_type.lower() == "application/pdf" or self.filename.lower().endswith(".pdf")


@dataclass(frozen=True)
class Message:
    message_id: str
    thread_id: str
    sender_email: str
    sender_domain: str
    subject: str
    text_body: str
    html_body: str
    attachments: tuple[Attachment, ...] = ()
    urls: tuple[str, ...] = ()
    headers: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        result = asdict(self)
        result["attachments"] = [
            {**asdict(item), "data": None if item.data is None else f"<{len(item.data)} bytes>"}
            for item in self.attachments
        ]
        return result


@dataclass(frozen=True)
class Classification:
    category: Category
    confidence: float
    reasons: tuple[str, ...]
    automation_confidence: float = 0.0
    automation_reasons: tuple[str, ...] = ()

    def as_dict(self) -> dict[str, Any]:
        return {
            "category": self.category.value,
            "confidence": self.confidence,
            "reasons": list(self.reasons),
            "automation_confidence": self.automation_confidence,
            "automation_reasons": list(self.automation_reasons),
        }


@dataclass(frozen=True)
class PlannedAction:
    action: str
    message_id: str
    thread_id: str
    category: Category
    details: str

    def as_dict(self) -> dict[str, str]:
        return {
            "action": self.action,
            "message_id": self.message_id,
            "thread_id": self.thread_id,
            "category": self.category.value,
            "details": self.details,
        }
