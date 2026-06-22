"""Тесты динамического whitelist в Storage."""

from apiat.storage.repositories import Storage


def test_whitelist_add_and_get(tmp_path):
    s = Storage(tmp_path / "t.db")
    s.whitelist_add("user@example.com")
    assert "user@example.com" in s.whitelist_get()


def test_whitelist_remove(tmp_path):
    s = Storage(tmp_path / "t.db")
    s.whitelist_add("user@example.com")
    s.whitelist_remove("user@example.com")
    assert "user@example.com" not in s.whitelist_get()


def test_whitelist_get_excluded(tmp_path):
    s = Storage(tmp_path / "t.db")
    s.set_setting("wl_excluded:bad@example.com", "1")
    excluded = s.whitelist_get_excluded()
    assert "bad@example.com" in excluded


def test_whitelist_add_normalises_case(tmp_path):
    s = Storage(tmp_path / "t.db")
    s.whitelist_add("  USER@EXAMPLE.COM  ")
    assert "user@example.com" in s.whitelist_get()


def test_whitelist_duplicate_add_is_safe(tmp_path):
    s = Storage(tmp_path / "t.db")
    s.whitelist_add("a@b.com")
    s.whitelist_add("a@b.com")
    assert s.whitelist_get().count("a@b.com") == 1
