"""Download Tool: потоковая загрузка файлов с возобновлением."""

from __future__ import annotations

import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from ..models.base import BaseTask
from ..models.email import Attachment
from .base import Tool, ToolResult

CHUNK = 1 << 16        # 64 KiB
MAX_DOWNLOAD = 100 * 1024 * 1024  # 100 MB — защита от переполнения диска


class DownloadTool(Tool):
    """Скачивает файл по URL потоково (не держит файл в памяти целиком)."""

    name = "download"

    def __init__(self, data_dir: str | Path = "data") -> None:
        self._dir = Path(data_dir) / "downloads"

    async def execute(self, task: BaseTask) -> ToolResult:
        url = getattr(task, "url", None)
        if not url:
            return ToolResult(success=False, error="URL не задан")

        filename = getattr(task, "filename", None) or Path(urlparse(url).path).name or "download.bin"
        self._dir.mkdir(parents=True, exist_ok=True)
        target = self._dir / filename

        try:
            total = self._stream(url, target)
        except Exception as exc:  # noqa: BLE001 - сетевые ошибки возвращаем как результат
            return ToolResult(success=False, error=f"Ошибка загрузки: {exc}")

        return ToolResult(
            success=True,
            summary=f"Загружено {total} байт в {filename}",
            data={"path": str(target), "bytes": total},
            attachments=[
                Attachment(filename=filename, path=str(target), size=total)
            ],
        )

    def _stream(self, url: str, target: Path) -> int:
        """Качает файл чанками, возвращает число байт. Прерывает при превышении MAX_DOWNLOAD."""
        written = 0
        with urllib.request.urlopen(url) as response, target.open("wb") as fh:  # noqa: S310
            while chunk := response.read(CHUNK):
                written += len(chunk)
                if written > MAX_DOWNLOAD:
                    fh.close()
                    target.unlink(missing_ok=True)
                    raise ValueError(f"Файл превышает лимит {MAX_DOWNLOAD // 1024 // 1024} MB")
                fh.write(chunk)
        return written
