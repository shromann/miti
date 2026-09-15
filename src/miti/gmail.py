"""Small injectable Gmail boundary and lazily imported OAuth implementation."""

from __future__ import annotations

import base64
import time
from pathlib import Path
from typing import Any, Protocol

from loguru import logger

SCOPES = ("https://www.googleapis.com/auth/gmail.modify", "https://www.googleapis.com/auth/gmail.send")
RATE_LIMIT_RETRIES = 6
MESSAGE_GET_BATCH_SIZE = 100
UNREAD_INBOX_QUERY = "is:unread in:inbox"
# Includes headers and three levels of MIME parts, which covers ordinary Gmail
# multipart messages without requesting labels, history, or other message data.
MESSAGE_FIELDS = (
    "id,threadId,payload(mimeType,filename,headers(name,value),body(data,attachmentId),"
    "parts(mimeType,filename,body(data,attachmentId),"
    "parts(mimeType,filename,body(data,attachmentId),"
    "parts(mimeType,filename,body(data,attachmentId)))))"
)


class GmailAuthorizationError(RuntimeError):
    """Raised when the stored OAuth token lacks the scopes required by miti."""


class GmailClient(Protocol):
    def list_unread_inbox(self, query: str = UNREAD_INBOX_QUERY) -> list[dict[str, Any]]: ...
    def get_message(self, message_id: str) -> dict[str, Any]: ...
    def get_messages(self, message_ids: list[str]) -> list[dict[str, Any]]: ...
    def get_attachment(self, message_id: str, attachment_id: str) -> bytes: ...
    def ensure_label(self, name: str) -> str: ...
    def modify_message(self, message_id: str, add_label_ids: list[str], remove_label_ids: list[str]) -> None: ...
    def send_raw(self, raw_message: bytes, thread_id: str | None = None) -> None: ...


class GoogleGmailClient:
    """Gmail API adapter. Imports Google packages only when a live client is needed."""

    def __init__(self, service: Any):
        self.service = service

    @classmethod
    def from_credentials(cls, credentials_path: Path, client_secret_path: Path) -> "GoogleGmailClient":
        try:
            from google.auth.transport.requests import Request
            from google.oauth2.credentials import Credentials
            from google_auth_oauthlib.flow import InstalledAppFlow
            from googleapiclient.discovery import build
        except ImportError as error:
            raise RuntimeError(
                "Google Gmail dependencies are missing. Install miti with its production dependencies."
            ) from error
        credentials = None
        if credentials_path.exists():
            credentials = Credentials.from_authorized_user_file(str(credentials_path), SCOPES)
        if credentials and credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
        if not credentials or not credentials.valid:
            if not client_secret_path.exists():
                raise RuntimeError(
                    f"No valid Gmail credentials. Run `miti auth {client_secret_path}` first."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), SCOPES)
            credentials = flow.run_local_server(port=0)
        credentials_path.parent.mkdir(parents=True, exist_ok=True)
        credentials_path.write_text(credentials.to_json(), encoding="utf-8")
        return cls(build("gmail", "v1", credentials=credentials, cache_discovery=False))

    @classmethod
    def authorize(cls, client_secret_path: Path, credentials_path: Path) -> None:
        # Reopen consent so `miti auth` can repair a token created with older or incomplete scopes.
        credentials_path.unlink(missing_ok=True)
        cls.from_credentials(credentials_path, client_secret_path)

    @staticmethod
    def _execute(request: Any, *, retry_rate_limit: bool = False) -> Any:
        from googleapiclient.errors import HttpError

        for attempt in range(RATE_LIMIT_RETRIES + 1):
            try:
                return request.execute()
            except HttpError as error:
                if error.resp.status == 403 and b"insufficientPermissions" in error.content:
                    raise GmailAuthorizationError(
                        "Gmail access is missing required scopes. Run `miti auth` to authorize "
                        "`gmail.modify` and `gmail.send`, then retry."
                    ) from error
                if not retry_rate_limit or not _is_rate_limited(error):
                    raise
                if attempt == RATE_LIMIT_RETRIES:
                    raise RuntimeError(
                        "Gmail rate limit persisted after retries. Wait a minute, then run the command again."
                    ) from error
                delay = 2**attempt
                logger.warning(
                    "Gmail rate limit reached; retrying request in {delay} second(s) ({attempt}/{total})",
                    delay=delay,
                    attempt=attempt + 1,
                    total=RATE_LIMIT_RETRIES,
                )
                time.sleep(delay)

        raise AssertionError("unreachable")

    def list_unread_inbox(self, query: str = UNREAD_INBOX_QUERY) -> list[dict[str, Any]]:
        messages: list[dict[str, Any]] = []
        page_token: str | None = None

        while True:
            request_args: dict[str, Any] = {
                "userId": "me",
                "q": query,
                "maxResults": 500,
            }
            if page_token:
                request_args["pageToken"] = page_token
            response = self._execute(self.service.users().messages().list(**request_args), retry_rate_limit=True)
            messages.extend(response.get("messages", []))
            page_token = response.get("nextPageToken")
            if not page_token:
                break

        return messages

    def get_message(self, message_id: str) -> dict[str, Any]:
        return self._execute(
            self.service.users().messages().get(userId="me", id=message_id, format="full"),
            retry_rate_limit=True,
        )

    def get_messages(self, message_ids: list[str]) -> list[dict[str, Any]]:
        """Load messages with Gmail HTTP batches while preserving the supplied order."""
        messages: dict[str, dict[str, Any]] = {}
        for start in range(0, len(message_ids), MESSAGE_GET_BATCH_SIZE):
            batch_ids = message_ids[start:start + MESSAGE_GET_BATCH_SIZE]
            messages.update(self._get_message_batch(batch_ids))
        return [messages[message_id] for message_id in message_ids]

    def _get_message_batch(self, message_ids: list[str]) -> dict[str, dict[str, Any]]:
        from googleapiclient.errors import HttpError

        pending_ids = message_ids
        messages: dict[str, dict[str, Any]] = {}

        for attempt in range(RATE_LIMIT_RETRIES + 1):
            failures: dict[str, Exception] = {}

            def collect(
                request_id: str, response: dict[str, Any] | None, exception: Exception | None
            ) -> None:
                if exception:
                    failures[request_id] = exception
                elif response is not None:
                    messages[request_id] = response
                else:
                    raise RuntimeError(f"Gmail returned no response for message {request_id}")

            batch = self.service.new_batch_http_request(callback=collect)
            for message_id in pending_ids:
                batch.add(
                    self.service.users().messages().get(
                        userId="me",
                        id=message_id,
                        format="full",
                        fields=MESSAGE_FIELDS,
                    ),
                    request_id=message_id,
                )
            self._execute(batch, retry_rate_limit=True)

            rate_limited_ids: list[str] = []
            for message_id in pending_ids:
                error = failures.get(message_id)
                if error is None:
                    continue
                if isinstance(error, HttpError) and _is_rate_limited(error):
                    rate_limited_ids.append(message_id)
                else:
                    raise error
            if not rate_limited_ids:
                return messages
            if attempt == RATE_LIMIT_RETRIES:
                raise RuntimeError(
                    "Gmail rate limit persisted after retries. Wait a minute, then run the command again."
                )
            delay = 2**attempt
            logger.warning(
                "Gmail rate limit reached; retrying {message_count} batched read(s) in {delay} second(s) "
                "({attempt}/{total})",
                message_count=len(rate_limited_ids),
                delay=delay,
                attempt=attempt + 1,
                total=RATE_LIMIT_RETRIES,
            )
            time.sleep(delay)
            pending_ids = rate_limited_ids

        raise AssertionError("unreachable")

    def get_attachment(self, message_id: str, attachment_id: str) -> bytes:
        payload = self._execute(
            self.service.users().messages().attachments().get(
                userId="me", messageId=message_id, id=attachment_id
            ),
            retry_rate_limit=True,
        )["data"]
        return base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))

    def ensure_label(self, name: str) -> str:
        labels = self._execute(self.service.users().labels().list(userId="me"), retry_rate_limit=True).get("labels", [])
        for label in labels:
            if label["name"] == name:
                return label["id"]
        return self._execute(self.service.users().labels().create(userId="me", body={"name": name}))["id"]

    def modify_message(self, message_id: str, add_label_ids: list[str], remove_label_ids: list[str]) -> None:
        self._execute(self.service.users().messages().modify(
            userId="me", id=message_id, body={"addLabelIds": add_label_ids, "removeLabelIds": remove_label_ids}
        ))

    def send_raw(self, raw_message: bytes, thread_id: str | None = None) -> None:
        body: dict[str, Any] = {"raw": base64.urlsafe_b64encode(raw_message).decode("ascii")}
        if thread_id:
            body["threadId"] = thread_id
        self._execute(self.service.users().messages().send(userId="me", body=body))


def _is_rate_limited(error: Any) -> bool:
    return error.resp.status == 429 or (
        error.resp.status == 403 and b"rateLimitExceeded" in error.content
    )
