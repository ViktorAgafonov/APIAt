"""Search Tool: новости через Google News RSS или LLM-дайджест."""

from __future__ import annotations

import urllib.parse
import xml.etree.ElementTree as ET
from typing import TYPE_CHECKING

import requests

from ..models.base import BaseTask, TaskType
from .base import Tool, ToolResult

if TYPE_CHECKING:
    from ..intent.router import LlmRouter

_MAX_RESULTS = 10
_GNEWS_RSS = "https://news.google.com/rss/search?q={query}&hl=ru&gl=RU&ceid=RU:ru"
_TIMEOUT = 15

_LLM_NEWS_PROMPT = """\
Подготовь дайджест новостей для оператора по теме: {topic}

Требования:
- {max_results} самых важных и актуальных новостей (на основе твоих знаний)
- Каждая новость: заголовок, краткое описание 1-2 предложения, источник если знаешь
- Формат plain text, удобный для чтения в письме
- Укажи дату актуальности своих знаний в конце

Не добавляй вводных фраз — сразу список новостей.
"""


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


def _format_rss(items: list[dict]) -> str:
    lines = []
    for i, r in enumerate(items, 1):
        title = r.get("title", "")
        url = r.get("url", "")
        date = r.get("date", "")[:16]
        source = r.get("source", "")
        line = f"{i}. {title}"
        meta = [x for x in (source, date) if x]
        if meta:
            line += f"  ({', '.join(meta)})"
        if url:
            line += f"\n   {url}"
        lines.append(line)
    return "\n\n".join(lines)


class SearchTool(Tool):
    """Поиск: RSS-режим (Google News) или LLM-дайджест."""

    name = "search"

    def __init__(self, llm_router: "LlmRouter | None" = None) -> None:
        self._router = llm_router

    async def execute(self, task: BaseTask) -> ToolResult:
        query = getattr(task, "query", None) or getattr(task, "topic", "")
        max_results = getattr(task, "max_results", _MAX_RESULTS)
        task_type = getattr(task, "type", None)
        use_rss = getattr(task, "use_rss", False)
        is_news = task_type == TaskType.NEWS

        if use_rss or not is_news:
            return await self._rss_search(query, max_results, is_news)
        return await self._llm_digest(query, max_results)

    async def _rss_search(self, query: str, max_results: int, is_news: bool) -> ToolResult:
        try:
            items = _fetch_gnews(query, max_results)
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False, summary=f"Ошибка RSS: {e}", error=str(e))

        label = "Новости (RSS)" if is_news else "Результаты"
        summary = f"{label} по запросу: {query!r} ({len(items)} шт.)\n\n{_format_rss(items)}"
        return ToolResult(success=True, summary=summary, data={"query": query, "results": items})

    async def _llm_digest(self, topic: str, max_results: int) -> ToolResult:
        if self._router is None:
            return await self._rss_search(topic, max_results, is_news=True)
        try:
            prompt = _LLM_NEWS_PROMPT.format(topic=topic, max_results=max_results)
            digest = await self._router.complete(prompt)
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False, summary=f"Ошибка LLM: {e}", error=str(e))
        return ToolResult(
            success=True,
            summary=f"Новости по теме: {topic!r}\n\n{digest}",
            data={"query": topic, "results": []},
        )
