"""Small injectable Gmail boundary and lazily imported OAuth implementation."""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any, Protocol

SCOPES = ("https://www.googleapis.com/auth/gmail.modify", "https://www.googleapis.com/auth/gmail.send")


class GmailClient(Protocol):
    def list_unread_inbox(self, max_results: int) -> list[dict[str, Any]]: ...
    def get_message(self, message_id: str) -> dict[str, Any]: ...
    def get_attachment(self, message_id: str, attachment_id: str) -> bytes: ...
    def ensure_label(self, name: str) -> str: ...
    def modify_message(self, message_id: str, add_label_ids: list[str], remove_label_ids: list[str]) -> None: ...
    def send_raw(self, raw_message: bytes, thread_id: str | None = None) -> None: ...
    def delete_message(self, message_id: str) -> None: ...


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
                    f"No valid Gmail credentials. Run `miti auth --client-secret {client_secret_path}` first."
                )
            flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), SCOPES)
            credentials = flow.run_local_server(port=0)
        credentials_path.parent.mkdir(parents=True, exist_ok=True)
        credentials_path.write_text(credentials.to_json(), encoding="utf-8")
        return cls(build("gmail", "v1", credentials=credentials, cache_discovery=False))

    @classmethod
    def authorize(cls, client_secret_path: Path, credentials_path: Path) -> None:
        cls.from_credentials(credentials_path, client_secret_path)

    def list_unread_inbox(self, max_results: int) -> list[dict[str, Any]]:
        response = self.service.users().messages().list(
            userId="me", q="is:unread in:inbox", maxResults=max_results
        ).execute()
        return response.get("messages", [])

    def get_message(self, message_id: str) -> dict[str, Any]:
        return self.service.users().messages().get(userId="me", id=message_id, format="full").execute()

    def get_attachment(self, message_id: str, attachment_id: str) -> bytes:
        payload = self.service.users().messages().attachments().get(
            userId="me", messageId=message_id, id=attachment_id
        ).execute()["data"]
        return base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))

    def ensure_label(self, name: str) -> str:
        labels = self.service.users().labels().list(userId="me").execute().get("labels", [])
        for label in labels:
            if label["name"] == name:
                return label["id"]
        return self.service.users().labels().create(userId="me", body={"name": name}).execute()["id"]

    def modify_message(self, message_id: str, add_label_ids: list[str], remove_label_ids: list[str]) -> None:
        self.service.users().messages().modify(
            userId="me", id=message_id, body={"addLabelIds": add_label_ids, "removeLabelIds": remove_label_ids}
        ).execute()

    def send_raw(self, raw_message: bytes, thread_id: str | None = None) -> None:
        body: dict[str, Any] = {"raw": base64.urlsafe_b64encode(raw_message).decode("ascii")}
        if thread_id:
            body["threadId"] = thread_id
        self.service.users().messages().send(userId="me", body=body).execute()

    def delete_message(self, message_id: str) -> None:
        self.service.users().messages().delete(userId="me", id=message_id).execute()
