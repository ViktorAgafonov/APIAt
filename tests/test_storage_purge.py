"""Тесты автоочистки устаревших записей в Storage."""

import sqlite3
from apiat.storage.repositories import Storage
from apiat.storage.db import connect


def _force_old_timestamps(db_path, table: str, ts_col: str, age_days: int = 91) -> None:
    conn = connect(db_path)
    try:
        conn.execute(
            f"UPDATE {table} SET {ts_col} = datetime('now', '-{age_days} days')"
        )
        conn.commit()
    finally:
        conn.close()


def test_purge_old_processed_mail(tmp_path):
    db = tmp_path / "t.db"
    s = Storage(db)
    s.mark_mail_processed("old-msg", "a@b.com")
    _force_old_timestamps(db, "processed_mail", "processed_at", age_days=91)
    # Пересоздаём Storage — purge вызывается в __init__
    s2 = Storage(db)
    assert not s2.is_mail_processed("old-msg")


def test_purge_keeps_recent_records(tmp_path):
    db = tmp_path / "t.db"
    s = Storage(db)
    s.mark_mail_processed("new-msg", "a@b.com")
    # Не трогаем timestamp — запись свежая
    s2 = Storage(db)
    assert s2.is_mail_processed("new-msg")


def test_purge_old_history(tmp_path):
    db = tmp_path / "t.db"
    s = Storage(db)
    s.save_task("t1", "youtube", "PARSED", {})
    s.update_status("t1", "COMPLETED", "note")
    # Помечаем все записи истории устаревшими
    _force_old_timestamps(db, "history", "created_at", age_days=46)
    conn = connect(db)
    try:
        count_before = conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]
    finally:
        conn.close()
    assert count_before > 0
    # Новый Storage → purge чистит старые записи
    Storage(db)
    conn = connect(db)
    try:
        count_after = conn.execute("SELECT COUNT(*) FROM history").fetchone()[0]
    finally:
        conn.close()
    # После purge все старые записи должны быть удалены
    assert count_after == 0
