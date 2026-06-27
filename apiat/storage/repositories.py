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
        self._purge_old_records()

    def _conn(self) -> sqlite3.Connection:
        return connect(self._db_path)

    def _purge_old_records(self) -> None:
        """Удаляет устаревшие записи чтобы БД не росла бесконечно."""
        conn = self._conn()
        try:
            conn.execute("DELETE FROM processed_mail WHERE processed_at < datetime('now', '-90 days')")
            conn.execute("DELETE FROM history        WHERE created_at  < datetime('now', '-45 days')")
            conn.execute("DELETE FROM results        WHERE created_at  < datetime('now', '-45 days')")
            conn.execute("DELETE FROM tasks          WHERE updated_at  < datetime('now', '-45 days')")
            conn.execute("DELETE FROM mail_threads   WHERE created_at  < datetime('now', '-45 days')")
            conn.execute("DELETE FROM pending_sends  WHERE created_at  < datetime('now', '-7 days')")
            conn.commit()
            conn.execute("VACUUM")
        finally:
            conn.close()

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

    def save_mail_thread(self, message_id: str, sender: str, subject: str,
                         body: str, refs: str, direction: str) -> None:
        """Сохраняет письмо в истории переписки (in/out)."""
        conn = self._conn()
        try:
            conn.execute(
                """
                INSERT INTO mail_threads (message_id, sender, subject, body, refs, direction)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(message_id) DO UPDATE SET
                    sender = excluded.sender,
                    subject = excluded.subject,
                    body = excluded.body,
                    refs = excluded.refs,
                    direction = excluded.direction
                """,
                (message_id, sender, subject, body, refs, direction),
            )
            conn.commit()
        finally:
            conn.close()

    def get_thread_history(self, refs: str, limit: int = 10) -> list[dict]:
        """Возвращает историю писем по заголовкам References/In-Reply-To."""
        if not refs:
            return []
        conn = self._conn()
        try:
            # Ищем все message_id в refs
            ids = [m.strip() for m in refs.replace(",", " ").split() if m.strip()]
            if not ids:
                return []
            placeholders = ",".join("?" for _ in ids)
            rows = conn.execute(
                f"""SELECT * FROM mail_threads
                    WHERE message_id IN ({placeholders})
                       OR ({" OR ".join("refs LIKE ?" for _ in ids)})
                    ORDER BY created_at DESC
                    LIMIT ?""",
                ids + [f"%{m}%" for m in ids] + [limit],
            ).fetchall()
            return [dict(r) for r in rows]
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

    # --- динамический whitelist (overrides поверх .env) ---
    def whitelist_get(self) -> list[str]:
        """Возвращает список адресов из динамического whitelist (БД)."""
        conn = self._conn()
        try:
            rows = conn.execute("SELECT key FROM settings WHERE key LIKE 'wl:%'").fetchall()
            return [r["key"][3:] for r in rows]
        finally:
            conn.close()

    def whitelist_add(self, email: str) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "INSERT OR IGNORE INTO settings (key, value) VALUES (?, '1')",
                (f"wl:{email.strip().lower()}",),
            )
            conn.commit()
        finally:
            conn.close()

    def whitelist_get_excluded(self) -> set[str]:
        """Возвращает адреса помеченные как удалённые (overrides .env whitelist)."""
        conn = self._conn()
        try:
            rows = conn.execute(
                "SELECT key FROM settings WHERE key LIKE 'wl_excluded:%'"
            ).fetchall()
            return {r["key"][len("wl_excluded:"):] for r in rows}
        finally:
            conn.close()

    def whitelist_remove(self, email: str) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "DELETE FROM settings WHERE key = ?",
                (f"wl:{email.strip().lower()}",),
            )
            conn.commit()
        finally:
            conn.close()

    # --- браузерные сессии ---
    def save_browser_session(self, domain: str, storage_state: dict) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO browser_sessions (domain, storage) VALUES (?, ?) "
                "ON CONFLICT(domain) DO UPDATE SET storage=excluded.storage, updated_at=datetime('now')",
                (domain, json.dumps(storage_state, default=str)),
            )
            conn.commit()
        finally:
            conn.close()

    def load_browser_session(self, domain: str) -> dict | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT storage FROM browser_sessions WHERE domain = ?", (domain,)
            ).fetchone()
            return json.loads(row["storage"]) if row else None
        finally:
            conn.close()

    def save_cookies(self, domain: str, cookies: list) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO cookies (domain, data) VALUES (?, ?) "
                "ON CONFLICT(domain) DO UPDATE SET data=excluded.data, updated_at=datetime('now')",
                (domain, json.dumps(cookies, default=str)),
            )
            conn.commit()
        finally:
            conn.close()

    def load_cookies(self, domain: str) -> list:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT data FROM cookies WHERE domain = ?", (domain,)
            ).fetchone()
            return json.loads(row["data"]) if row else []
        finally:
            conn.close()

    def save_token(self, name: str, value: str) -> None:
        conn = self._conn()
        try:
            conn.execute(
                "INSERT INTO tokens (name, value) VALUES (?, ?) "
                "ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated_at=datetime('now')",
                (name, value),
            )
            conn.commit()
        finally:
            conn.close()

    def load_token(self, name: str) -> str | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT value FROM tokens WHERE name = ?", (name,)
            ).fetchone()
            return row["value"] if row else None
        finally:
            conn.close()

    # --- отложенные отправки (pending_sends) ---
    def save_pending_send(self, token: str, sender: str, subject: str,
                          body: str, attachments: str, task_name: str,
                          status: str, elapsed: float, message_id: str) -> None:
        conn = self._conn()
        try:
            conn.execute(
                """INSERT INTO pending_sends (token, sender, subject, body, attachments,
                   task_name, status, elapsed, message_id)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(token) DO UPDATE SET
                     sender=excluded.sender, subject=excluded.subject,
                     body=excluded.body, attachments=excluded.attachments,
                     task_name=excluded.task_name, status=excluded.status,
                     elapsed=excluded.elapsed, message_id=excluded.message_id""",
                (token, sender, subject, body, attachments, task_name, status, elapsed, message_id),
            )
            conn.commit()
        finally:
            conn.close()

    def load_pending_send(self, token: str) -> dict | None:
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM pending_sends WHERE token = ?", (token,)
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def load_pending_send_by_sender(self, sender: str) -> dict | None:
        """Возвращает последний pending_send от отправителя."""
        conn = self._conn()
        try:
            row = conn.execute(
                "SELECT * FROM pending_sends WHERE sender = ? ORDER BY created_at DESC LIMIT 1",
                (sender,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def delete_pending_send(self, token: str) -> None:
        conn = self._conn()
        try:
            conn.execute("DELETE FROM pending_sends WHERE token = ?", (token,))
            conn.commit()
        finally:
            conn.close()
