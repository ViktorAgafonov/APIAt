"""Репозитории CRUD поверх SQLite."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ..models.base import TaskStatus
from .db import connect, init_db


class Storage:
    """Фасад доступа к данным: задачи, история, результаты, dedup, настройки."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        init_db(self._db_path)

    def _conn(self) -> sqlite3.Connection:
        return connect(self._db_path)

    # --- dedup входящей почты ---
    def is_mail_processed(self, message_id: str) -> bool:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT 1 FROM processed_mail WHERE message_id = ?",
                (message_id,),
            ).fetchone()
            return row is not None
        finally:
            conn.close()

    def mark_mail_processed(self, message_id: str, sender: str) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO processed_mail (message_id, sender) VALUES (?, ?)",
                (message_id, sender),
            )
            conn.commit()
        finally:
            conn.close()

    # --- задачи ---
    def save_task(self, task_id: str, task_type: str, status: str, payload: dict) -> None:
        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO tasks (task_id, type, status, payload)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    status = excluded.status,
                    payload = excluded.payload,
                    updated_at = datetime('now')
                """,
                (task_id, task_type, status, json.dumps(payload, default=str)),
            )
            conn.commit()
        finally:
            conn.close()

    def update_status(self, task_id: str, status: TaskStatus | str, note: str | None = None) -> None:
        status_str = status.value if isinstance(status, TaskStatus) else status
        conn = self._conn()
        try:
            conn.execute(
                "UPDATE tasks SET status = ?, updated_at = datetime('now') WHERE task_id = ?",
                (status_str, task_id),
            )
            conn.execute(
                "INSERT INTO history (task_id, status, note) VALUES (?, ?, ?)",
                (task_id, status_str, note),
            )
            conn.commit()
        finally:
            conn.close()

    def get_task(self, task_id: str) -> dict | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
            if row is None:
                return None
            data = dict(row)
            data["payload"] = json.loads(data["payload"])
            return data
        finally:
            conn.close()

    # --- результаты ---
    def save_result(
        self,
        task_id: str,
        summary: str,
        data: dict | None = None,
        metrics: dict | None = None,
    ) -> None:
        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO results (task_id, summary, data, metrics)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    summary = excluded.summary,
                    data = excluded.data,
                    metrics = excluded.metrics
                """,
                (
                    task_id,
                    summary,
                    json.dumps(data or {}, default=str),
                    json.dumps(metrics or {}, default=str),
                ),
            )
            conn.commit()
        finally:
            conn.close()

    # --- настройки (key/value) ---
    def set_setting(self, key: str, value: str) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO settings (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
            conn.commit()
        finally:
            conn.close()

    def get_setting(self, key: str) -> str | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
            return row["value"] if row else None
        finally:
            conn.close()
