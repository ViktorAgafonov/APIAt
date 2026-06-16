"""Реестр инструментов по имени. Новые инструменты добавляются здесь."""

from __future__ import annotations

from pathlib import Path

from ..models.base import TaskType
from .archive_tool import ArchiveTool
from .base import Tool
from .browser_tool import BrowserTool
from .download_tool import DownloadTool
from .search_tool import SearchTool
from .youtube_tool import YoutubeTool


class ToolRegistry:
    """Хранит и выдаёт инструменты по имени/типу задачи."""

    def __init__(self, data_dir: str | Path = "data") -> None:
        self._tools: dict[str, Tool] = {
            "search": SearchTool(),
            "youtube": YoutubeTool(data_dir),
            "download": DownloadTool(data_dir),
            "browser": BrowserTool(),
            "archive": ArchiveTool(data_dir),
        }
        # Соответствие тип задачи -> имя инструмента
        self._by_type: dict[TaskType, str] = {
            TaskType.SEARCH: "search",
            TaskType.NEWS: "search",
            TaskType.YOUTUBE: "youtube",
            TaskType.DOWNLOAD: "download",
            TaskType.BROWSER: "browser",
            TaskType.FILE: "archive",
        }

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def for_task_type(self, task_type: TaskType) -> Tool | None:
        name = self._by_type.get(task_type)
        return self._tools.get(name) if name else None

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool
