"""SQLite action ledger providing auditability and send idempotency."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path


class Ledger:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.connection = sqlite3.connect(path)
        self.connection.execute(
            """CREATE TABLE IF NOT EXISTS actions (
                id INTEGER PRIMARY KEY,
                message_id TEXT NOT NULL,
                thread_id TEXT NOT NULL,
                action TEXT NOT NULL,
                status TEXT NOT NULL,
                details TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(message_id, action)
            )"""
        )
        self.connection.commit()

    def was_successful(self, message_id: str, action: str) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM actions WHERE message_id = ? AND action = ? AND status = 'success'",
            (message_id, action),
        ).fetchone()
        return row is not None

    def record(self, message_id: str, thread_id: str, action: str, status: str, details: str) -> None:
        self.connection.execute(
            """INSERT INTO actions (message_id, thread_id, action, status, details, created_at)
               VALUES (?, ?, ?, ?, ?, ?)
               ON CONFLICT(message_id, action) DO UPDATE SET
                 thread_id=excluded.thread_id, status=excluded.status, details=excluded.details,
                 created_at=excluded.created_at""",
            (message_id, thread_id, action, status, details, datetime.now(UTC).isoformat()),
        )
        self.connection.commit()

    def close(self) -> None:
        self.connection.close()
