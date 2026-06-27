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

ВНИМАНИЕ: актуальные новости недоступны (RSS не сработал).
На основе своих знаний подготовь {max_results} новостей по теме.
Обязательно укажи в начале: "ВНИМАНИЕ: данные из памяти модели, могут быть устаревшими."
- Каждая новость: заголовок, краткое описание 1-2 предложения, источник если знаешь
- Формат plain text, удобный для чтения в письме
- Укажи дату актуальности своих знаний в конце

Не добавляй вводных фраз — сразу список новостей.
"""

_LLM_SUMMARIZE_PROMPT = """\
Ты редактор новостей. На основе RSS-результатов ниже подготовь дайджест \
для оператора по теме: {topic}

Требования:
- Выбери {max_results} самых важных новостей из списка
- Каждая: заголовок, краткое описание 1-2 предложения (на основе заголовка и источника)
- Укажи источник
- Формат plain text, удобный для чтения в письме

Не добавляй вводных фраз — сразу список новостей.

## RSS-результаты:
{rss_data}
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

        if is_news:
            # NEWS: всегда сначала RSS, затем LLM-суммаризация реальных результатов
            return await self._news_search(query, max_results, use_rss)
        # SEARCH: RSS-режим
        return await self._rss_search(query, max_results, is_news=False)

    async def _news_search(self, query: str, max_results: int, raw_rss: bool) -> ToolResult:
        """Поиск новостей: RSS → LLM-суммаризация. Fallback на LLM-дайджест из памяти."""
        try:
            items = _fetch_gnews(query, max_results)
        except Exception as e:  # noqa: BLE001
            # RSS не сработал — fallback к LLM из памяти с предупреждением
            if self._router is not None:
                return await self._llm_digest(query, max_results)
            return ToolResult(success=False, summary=f"Ошибка RSS: {e}", error=str(e))

        if not items:
            if self._router is not None:
                return await self._llm_digest(query, max_results)
            return ToolResult(success=False, summary=f"RSS вернул пустой результат по запросу: {query!r}")

        # Если оператор запросил сырой RSS — отдаём как есть
        if raw_rss or self._router is None:
            summary = f"Новости (RSS) по запросу: {query!r} ({len(items)} шт.)\n\n{_format_rss(items)}"
            return ToolResult(success=True, summary=summary, data={"query": query, "results": items})

        # LLM-суммаризация реальных RSS-результатов
        try:
            rss_data = _format_rss(items)
            prompt = _LLM_SUMMARIZE_PROMPT.format(topic=query, max_results=max_results, rss_data=rss_data)
            digest = await self._router.complete(prompt)
            return ToolResult(
                success=True,
                summary=f"Новости по теме: {query!r}\n\n{digest}",
                data={"query": query, "results": items},
            )
        except Exception as e:  # noqa: BLE001
            # LLM не сработал — отдаём сырой RSS
            summary = f"Новости (RSS) по запросу: {query!r} ({len(items)} шт.)\n\n{_format_rss(items)}"
            return ToolResult(success=True, summary=summary, data={"query": query, "results": items})

    async def _rss_search(self, query: str, max_results: int, is_news: bool) -> ToolResult:
        try:
            items = _fetch_gnews(query, max_results)
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False, summary=f"Ошибка RSS: {e}", error=str(e))

        label = "Новости (RSS)" if is_news else "Результаты"
        summary = f"{label} по запросу: {query!r} ({len(items)} шт.)\n\n{_format_rss(items)}"
        return ToolResult(success=True, summary=summary, data={"query": query, "results": items})

    async def _llm_digest(self, topic: str, max_results: int) -> ToolResult:
        """Fallback: LLM-дайджест из памяти с предупреждением об устаревших данных."""
        if self._router is None:
            return ToolResult(success=False, summary=f"LLM недоступен, RSS не сработал для темы: {topic!r}")
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
