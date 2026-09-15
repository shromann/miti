"""Gmail payload normalization with recursive MIME traversal."""

from __future__ import annotations

import base64
import re
from collections.abc import Callable
from email.utils import parseaddr
from typing import Any

from .models import Attachment, Message

_URL = re.compile(r"https?://[^\s<>'\"()]+", re.IGNORECASE)


def decode_base64url(value: str) -> bytes:
    """Decode Gmail's unpadded URL-safe base64 safely."""
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def normalize_message(
    raw: dict[str, Any],
    attachment_loader: Callable[[str, str], bytes] | None = None,
    include_attachment_data: bool = False,
) -> Message:
    payload = raw.get("payload", {})
    header_items = payload.get("headers", [])
    headers = {
        str(item.get("name", "")).lower(): str(item.get("value", ""))
        for item in header_items
        if item.get("name")
    }
    sender = parseaddr(headers.get("from", ""))[1].lower()
    domain = sender.rsplit("@", 1)[1] if "@" in sender else ""
    text_parts: list[str] = []
    html_parts: list[str] = []
    attachments: list[Attachment] = []

    def visit(part: dict[str, Any]) -> None:
        mime_type = str(part.get("mimeType", "")).lower()
        filename = str(part.get("filename", ""))
        body = part.get("body", {}) or {}
        encoded = body.get("data")
        attachment_id = body.get("attachmentId")
        if filename or attachment_id:
            data = None
            if include_attachment_data:
                if encoded:
                    data = decode_base64url(str(encoded))
                elif attachment_id and attachment_loader:
                    data = attachment_loader(str(raw.get("id", "")), str(attachment_id))
            attachments.append(Attachment(filename, mime_type, str(attachment_id) if attachment_id else None, data))
        elif encoded and mime_type in {"text/plain", "text/html"}:
            decoded = decode_base64url(str(encoded)).decode("utf-8", errors="replace")
            (text_parts if mime_type == "text/plain" else html_parts).append(decoded)
        for child in part.get("parts", []) or []:
            if isinstance(child, dict):
                visit(child)

    if not isinstance(payload, dict):
        raise ValueError("Gmail message payload must be an object")
    visit(payload)
    text = "\n".join(text_parts)
    html = "\n".join(html_parts)
    urls = tuple(dict.fromkeys(_URL.findall(text + "\n" + html)))
    message_id = str(raw.get("id", ""))
    if not message_id:
        raise ValueError("Gmail message has no id")
    return Message(
        message_id=message_id,
        thread_id=str(raw.get("threadId", message_id)),
        sender_email=sender,
        sender_domain=domain,
        subject=headers.get("subject", ""),
        text_body=text,
        html_body=html,
        attachments=tuple(attachments),
        urls=urls,
        headers=headers,
    )
