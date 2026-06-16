"""Search Tool: поиск информации (минимальная реализация-заглушка)."""

from __future__ import annotations

from ..models.base import BaseTask
from .base import Tool, ToolResult


class SearchTool(Tool):
    """Возвращает структурированные результаты поиска.

    Каркас: реальный движок поиска подключается на следующем этапе.
    """

    name = "search"

    async def execute(self, task: BaseTask) -> ToolResult:
        query = getattr(task, "query", None) or getattr(task, "topic", "")
        return ToolResult(
            success=True,
            summary=f"Поиск по запросу: {query!r} (заглушка)",
            data={"query": query, "results": []},
        )
