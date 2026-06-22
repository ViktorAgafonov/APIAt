"""Тесты безопасности IMAP: path traversal, лимит размера вложения."""

import re
from pathlib import Path

import pytest


def _sanitize_filename(raw_name: str) -> str:
    """Копия логики из imap_client._extract_parts."""
    return re.sub(r"[^\w.\-]", "_", Path(raw_name).name)[:200] or "attachment.bin"


def test_path_traversal_blocked():
    assert "/" not in _sanitize_filename("../../etc/passwd")
    assert ".." not in _sanitize_filename("../../etc/passwd")


def test_path_traversal_windows():
    result = _sanitize_filename("..\\..\\windows\\system32\\evil.exe")
    assert "\\" not in result
    assert ".." not in result


def test_normal_filename_preserved():
    result = _sanitize_filename("report_2024.pdf")
    assert result == "report_2024.pdf"


def test_long_filename_truncated():
    long = "a" * 300 + ".txt"
    result = _sanitize_filename(long)
    assert len(result) <= 200


def test_attachment_size_limit():
    from apiat.gateway.imap_client import _MAX_ATTACHMENT_BYTES
    assert _MAX_ATTACHMENT_BYTES == 25 * 1024 * 1024


def test_download_size_limit():
    from apiat.tools.download_tool import MAX_DOWNLOAD
    assert MAX_DOWNLOAD == 100 * 1024 * 1024


def test_download_stream_aborts_on_limit(tmp_path):
    from apiat.tools.download_tool import DownloadTool, MAX_DOWNLOAD
    import urllib.request

    tool = DownloadTool(data_dir=tmp_path)
    target = tmp_path / "big.bin"

    # Эмулируем ответ с данными чуть больше лимита
    class FakeResponse:
        def __init__(self):
            self._chunks = iter([b"x" * (MAX_DOWNLOAD + 1)])
        def read(self, n):
            try:
                return next(self._chunks)
            except StopIteration:
                return b""
        def __enter__(self): return self
        def __exit__(self, *a): pass

    original_open = urllib.request.urlopen

    def fake_open(url):
        return FakeResponse()

    urllib.request.urlopen = fake_open
    try:
        with pytest.raises(ValueError, match="лимит"):
            tool._stream("http://fake", target)
    finally:
        urllib.request.urlopen = original_open

    assert not target.exists()
