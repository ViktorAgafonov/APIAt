"""Инициализация SQLite и схема хранилища."""

from __future__ import annotations

import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_mail (
    message_id TEXT PRIMARY KEY,
    sender     TEXT NOT NULL,
    processed_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tasks (
    task_id    TEXT PRIMARY KEY,
    type       TEXT NOT NULL,
    status     TEXT NOT NULL,
    payload    TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS history (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    TEXT NOT NULL,
    status     TEXT NOT NULL,
    note       TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS results (
    task_id    TEXT PRIMARY KEY,
    summary    TEXT,
    data       TEXT,
    metrics    TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS settings (
    key   TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS browser_sessions (
    domain     TEXT PRIMARY KEY,
    storage    TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS cookies (
    domain     TEXT PRIMARY KEY,
    data       TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS tokens (
    name       TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS mail_threads (
    message_id TEXT PRIMARY KEY,
    sender     TEXT NOT NULL,
    subject    TEXT,
    body       TEXT,
    refs       TEXT,
    direction  TEXT NOT NULL CHECK(direction IN ('in', 'out')),
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_mail_threads_refs ON mail_threads(refs);

CREATE TABLE IF NOT EXISTS pending_sends (
    token       TEXT PRIMARY KEY,
    sender      TEXT NOT NULL,
    subject     TEXT,
    body        TEXT,
    attachments TEXT,
    task_name   TEXT,
    status      TEXT,
    elapsed     REAL,
    message_id  TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_pending_sends_sender ON pending_sends(sender);
"""


def connect(db_path: str | Path) -> sqlite3.Connection:
    """Открывает соединение с включёнными внешними ключами и row_factory."""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(db_path: str | Path) -> None:
    """Создаёт таблицы, если их ещё нет."""
    conn = connect(db_path)
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        conn.close()
