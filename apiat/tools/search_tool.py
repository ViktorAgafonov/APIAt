"""Search Tool: новости через Google News RSS, веб-поиск через SearXNG."""

from __future__ import annotations

import urllib.parse
import xml.etree.ElementTree as ET

import requests

from ..models.base import BaseTask, TaskType
from .base import Tool, ToolResult

_MAX_RESULTS = 10
_GNEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=ru&gl=RU&ceid=RU:ru"
_TIMEOUT = 15


def _fetch_gnews(query: str, max_results: int) -> list[dict]:
    url = _GNEWS_RSS.format(query=urllib.parse.quote(query))
    resp = requests.get(url, timeout=_TIMEOUT, headers={"User-Agent": "Mozilla/5.0"})
    resp.raise_for_status()
    root = ET.fromstring(resp.content)
    items = []
    for item in root.findall(".//item")[:max_results]:
        title = (item.findtext("title") or "").strip()
        link = (item.findtext("link") or "").strip()
        pub_date = (item.findtext("pubDate") or "").strip()
        source_el = item.find("source")
        source = source_el.text.strip() if source_el is not None else ""
        items.append({"title": title, "url": link, "date": pub_date, "source": source})
    return items


def _format_results(items: list[dict], is_news: bool) -> str:
    lines = []
    for i, r in enumerate(items, 1):
        title = r.get("title", "")
        url = r.get("url", r.get("href", ""))
        date = r.get("date", "")
        source = r.get("source", "")
        body = r.get("body", r.get("description", ""))
        line = f"{i}. {title}"
        meta = []
        if source:
            meta.append(source)
        if date:
            meta.append(date[:16])
        if meta:
            line += f"  ({', '.join(meta)})"
        if body:
            line += f"\n   {body[:200]}"
        if url:
            line += f"\n   {url}"
        lines.append(line)
    return "\n\n".join(lines)


class SearchTool(Tool):
    """Поиск новостей через Google News RSS; веб-поиск через тот же RSS."""

    name = "search"

    async def execute(self, task: BaseTask) -> ToolResult:
        query = getattr(task, "query", None) or getattr(task, "topic", "")
        max_results = getattr(task, "max_results", _MAX_RESULTS)
        task_type = getattr(task, "type", None)
        is_news = task_type == TaskType.NEWS

        try:
            items = _fetch_gnews(query, max_results)
        except Exception as e:  # noqa: BLE001
            return ToolResult(
                success=False,
                summary=f"Ошибка поиска: {e}",
                error=str(e),
            )

        formatted = _format_results(items, is_news)
        label = "Новости" if is_news else "Результаты"
        summary = f"{label} по запросу: {query!r} ({len(items)} шт.)\n\n{formatted}"
        return ToolResult(
            success=True,
            summary=summary,
            data={"query": query, "results": items},
        )
