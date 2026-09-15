from __future__ import annotations

from typing import Any

import pytest
from googleapiclient.errors import HttpError
from httplib2 import Response

from miti.gmail import MESSAGE_FIELDS, GoogleGmailClient


class FakeRequest:
    def __init__(self, response: dict[str, Any]) -> None:
        self.response = response

    def execute(self) -> dict[str, Any]:
        return self.response


class FakeMessages:
    def __init__(self, responses: list[dict[str, Any]], message_responses: dict[str, dict[str, Any]] | None = None) -> None:
        self.responses = iter(responses)
        self.message_responses = message_responses or {}
        self.requests: list[dict[str, Any]] = []
        self.get_requests: list[dict[str, Any]] = []

    def list(self, **kwargs: Any) -> FakeRequest:
        self.requests.append(kwargs)
        return FakeRequest(next(self.responses))

    def get(self, **kwargs: Any) -> FakeRequest:
        self.get_requests.append(kwargs)
        return FakeRequest(self.message_responses[kwargs["id"]])


class FakeBatch:
    def __init__(self, callback: Any) -> None:
        self.callback = callback
        self.requests: list[tuple[FakeRequest, str]] = []

    def add(self, request: FakeRequest, request_id: str) -> None:
        self.requests.append((request, request_id))

    def execute(self) -> None:
        for request, request_id in self.requests:
            self.callback(request_id, request.execute(), None)


class FakeService:
    def __init__(self, responses: list[dict[str, Any]], message_responses: dict[str, dict[str, Any]] | None = None) -> None:
        self.messages_api = FakeMessages(responses, message_responses)
        self.batches: list[FakeBatch] = []

    def users(self) -> "FakeService":
        return self

    def messages(self) -> FakeMessages:
        return self.messages_api

    def new_batch_http_request(self, callback: Any) -> FakeBatch:
        batch = FakeBatch(callback)
        self.batches.append(batch)
        return batch


def test_list_unread_inbox_paginates_until_all_messages_are_loaded() -> None:
    service = FakeService([
        {"messages": [{"id": "one"}, {"id": "two"}], "nextPageToken": "page-2"},
        {"messages": [{"id": "three"}]},
    ])

    messages = GoogleGmailClient(service).list_unread_inbox()

    assert messages == [{"id": "one"}, {"id": "two"}, {"id": "three"}]
    assert service.messages_api.requests == [
        {"userId": "me", "q": "is:unread in:inbox", "maxResults": 500},
        {"userId": "me", "q": "is:unread in:inbox", "maxResults": 500, "pageToken": "page-2"},
    ]


def test_list_unread_inbox_uses_a_server_side_filter() -> None:
    service = FakeService([{"messages": [{"id": "one"}]}])
    query = 'is:unread in:inbox subject:"BOR - Golden Age Cinema- Nightly"'

    messages = GoogleGmailClient(service).list_unread_inbox(query)

    assert messages == [{"id": "one"}]
    assert service.messages_api.requests == [{"userId": "me", "q": query, "maxResults": 500}]


def test_get_messages_uses_gmail_batches_and_preserves_message_order() -> None:
    message_ids = [f"message-{index}" for index in range(101)]
    service = FakeService(
        [],
        {message_id: {"id": message_id} for message_id in message_ids},
    )

    messages = GoogleGmailClient(service).get_messages(message_ids)

    assert messages == [{"id": message_id} for message_id in message_ids]
    assert [len(batch.requests) for batch in service.batches] == [100, 1]
    assert service.messages_api.get_requests == [
        {"userId": "me", "id": message_id, "format": "full", "fields": MESSAGE_FIELDS}
        for message_id in message_ids
    ]


def test_message_fields_selector_has_balanced_parentheses() -> None:
    assert MESSAGE_FIELDS.count("(") == MESSAGE_FIELDS.count(")")


def test_execute_retries_rate_limited_read_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    rate_limited = HttpError(
        Response({"status": "403"}),
        b'{"error":{"errors":[{"reason":"rateLimitExceeded"}]}}',
    )

    class RetryRequest:
        def __init__(self) -> None:
            self.attempts = 0

        def execute(self) -> dict[str, bool]:
            self.attempts += 1
            if self.attempts == 1:
                raise rate_limited
            return {"success": True}

    sleeps: list[int] = []
    monkeypatch.setattr("miti.gmail.time.sleep", sleeps.append)
    request = RetryRequest()

    assert GoogleGmailClient._execute(request, retry_rate_limit=True) == {"success": True}
    assert request.attempts == 2
    assert sleeps == [1]
