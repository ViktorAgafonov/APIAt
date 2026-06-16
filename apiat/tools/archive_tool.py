"""Archive Tool: упаковка в zip/tar.gz и разбиение на части."""

from __future__ import annotations

import tarfile
import zipfile
from pathlib import Path

from ..models.base import BaseTask
from ..models.email import Attachment
from .base import Tool, ToolResult


class ArchiveTool(Tool):
    """Архивирование файлов и разбиение архива на части фиксированного размера."""

    name = "archive"

    def __init__(self, data_dir: str | Path = "data") -> None:
        self._dir = Path(data_dir) / "archives"

    async def execute(self, task: BaseTask) -> ToolResult:
        paths = [Path(p) for p in getattr(task, "paths", [])]
        if not paths:
            return ToolResult(success=False, error="Список файлов пуст")
        self._dir.mkdir(parents=True, exist_ok=True)
        archive = self.create_zip(paths, self._dir / "result.zip")
        size = archive.stat().st_size
        return ToolResult(
            success=True,
            summary=f"Создан архив {archive.name} ({size} байт)",
            data={"path": str(archive), "bytes": size},
            attachments=[Attachment(filename=archive.name, path=str(archive), size=size)],
        )

    @staticmethod
    def create_zip(paths: list[Path], target: Path) -> Path:
        """Упаковывает файлы в zip."""
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
            for path in paths:
                if path.exists():
                    zf.write(path, arcname=path.name)
        return target

    @staticmethod
    def create_targz(paths: list[Path], target: Path) -> Path:
        """Упаковывает файлы в tar.gz."""
        with tarfile.open(target, "w:gz") as tf:
            for path in paths:
                if path.exists():
                    tf.add(path, arcname=path.name)
        return target

    @staticmethod
    def split_file(path: Path, part_size: int) -> list[Path]:
        """Разбивает файл на части по part_size байт."""
        parts: list[Path] = []
        with path.open("rb") as fh:
            index = 1
            while chunk := fh.read(part_size):
                part = path.with_suffix(path.suffix + f".{index:03d}")
                part.write_bytes(chunk)
                parts.append(part)
                index += 1
        return parts
