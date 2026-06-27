"""Утилиты для сжатия и разбиения больших текстов."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path


def zip_text(text: str, filename: str = "output.txt", zip_name: str = "output.zip") -> Path:
    """Сжимает текст в zip-архив и возвращает путь к файлу."""
    import tempfile
    tmp = Path(tempfile.gettempdir()) / zip_name
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.writestr(filename, text)
    return tmp


def split_zip(zip_path: Path, parts: int) -> list[Path]:
    """Разбивает zip-файл на parts частей примерно равного размера.

    Возвращает список путей к частям (part.001, part.002, ...).
    """
    if parts < 1:
        parts = 1
    data = zip_path.read_bytes()
    chunk_size = (len(data) + parts - 1) // parts
    result: list[Path] = []
    stem = zip_path.stem
    for i in range(parts):
        chunk = data[i * chunk_size : (i + 1) * chunk_size]
        part_path = zip_path.parent / f"{stem}.part{i + 1:03d}"
        part_path.write_bytes(chunk)
        result.append(part_path)
    return result


def zip_file(file_path: Path, zip_name: str | None = None) -> Path:
    """Сжимает файл в zip-архив и возвращает путь к архиву."""
    import tempfile
    name = zip_name or f"{file_path.stem}.zip"
    tmp = Path(tempfile.gettempdir()) / name
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.write(file_path, file_path.name)
    return tmp


def estimate_zip_size(text: str, filename: str = "output.txt") -> int:
    """Оценивает размер zip-архива для текста без записи на диск."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zf:
        zf.writestr(filename, text)
    return buf.tell()
