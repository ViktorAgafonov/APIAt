"""Search Tool: веб-поиск и новости через DuckDuckGo (без API-ключей)."""

from __future__ import annotations

from duckduckgo_search import DDGS

from ..models.base import BaseTask, TaskType
from .base import Tool, ToolResult

_MAX_RESULTS = 10


def _format_results(items: list[dict]) -> str:
    lines = []
    for i, r in enumerate(items, 1):
        title = r.get("title", "").strip()
        body = r.get("body", r.get("description", "")).strip()
        url = r.get("href", r.get("url", "")).strip()
        date = r.get("date", "")
        line = f"{i}. {title}"
        if date:
            line += f" [{date}]"
        if body:
            line += f"\n   {body[:200]}"
        if url:
            line += f"\n   {url}"
        lines.append(line)
    return "\n\n".join(lines)


class SearchTool(Tool):
    """Веб-поиск и поиск новостей через DuckDuckGo."""

    name = "search"

    async def execute(self, task: BaseTask) -> ToolResult:
        query = getattr(task, "query", None) or getattr(task, "topic", "")
        max_results = getattr(task, "max_results", _MAX_RESULTS)
        task_type = getattr(task, "type", None)

        try:
            with DDGS() as ddgs:
                if task_type == TaskType.NEWS:
                    items = list(ddgs.news(query, max_results=max_results))
                else:
                    items = list(ddgs.text(query, max_results=max_results))
        except Exception as e:  # noqa: BLE001
            return ToolResult(
                success=False,
                summary=f"Ошибка поиска: {e}",
                error=str(e),
            )

        formatted = _format_results(items)
        summary = (
            f"{'Новости' if task_type == TaskType.NEWS else 'Результаты'} "
            f"по запросу: {query!r} ({len(items)} шт.)\n\n{formatted}"
        )
        return ToolResult(
            success=True,
            summary=summary,
            data={"query": query, "results": items},
        )
