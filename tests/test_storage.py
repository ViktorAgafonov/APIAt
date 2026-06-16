"""Тесты слоя хранения."""

from apiat.models.base import TaskStatus
from apiat.storage.repositories import Storage


def test_mail_dedup(tmp_path):
    db = tmp_path / "t.db"
    storage = Storage(db)
    assert not storage.is_mail_processed("m1")
    storage.mark_mail_processed("m1", "a@x.com")
    assert storage.is_mail_processed("m1")
    # повторная пометка не падает
    storage.mark_mail_processed("m1", "a@x.com")


def test_task_save_and_status(tmp_path):
    storage = Storage(tmp_path / "t.db")
    storage.save_task("t1", "youtube", TaskStatus.PARSED.value, {"url": "u"})
    storage.update_status("t1", TaskStatus.COMPLETED, "ok")
    task = storage.get_task("t1")
    assert task is not None
    assert task["status"] == "COMPLETED"
    assert task["payload"]["url"] == "u"


def test_result_and_settings(tmp_path):
    storage = Storage(tmp_path / "t.db")
    storage.save_task("t1", "search", "PARSED", {})
    storage.save_result("t1", "done", {"x": 1}, {"execution_time": 1.0})
    storage.set_setting("k", "v")
    assert storage.get_setting("k") == "v"
    assert storage.get_setting("missing") is None
