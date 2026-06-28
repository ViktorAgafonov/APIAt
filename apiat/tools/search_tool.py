"""Search Tool: новости через Google News RSS + LLM-суммаризация.

Никогда не генерируем новости из памяти LLM — только реальные RSS-данные.
Если RSS недоступен — возвращаем ошибку, без фантазий.
"""

from __future__ import annotations

import base64
import re
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

_LLM_SUMMARIZE_PROMPT = """\
Ты редактор новостей. На основе RSS-результатов ниже подготовь дайджест \
для оператора по теме: {topic}

Требования:
- Выбери {max_results} самых важных новостей из списка
- Каждая: заголовок, краткое описание 1-2 предложения (на основе заголовка и источника)
- Укажи источник и дату
- Формат plain text, удобный для чтения в письме
- ОПЕРАТОР В ЗОНЕ БЛОКИРОВОК — НЕ включай ссылки, только текстовый пересказ
- Не добавляй вводных фраз — сразу список новостей
- Используй ТОЛЬКО данные из RSS-результатов ниже — не придумывай новости от себя

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
        real_url = _decode_gnews_url(link)
        items.append({"title": title, "url": real_url or link, "date": pub_date, "source": source})
    return items


def _decode_gnews_url(gnews_url: str) -> str:
    """Декодирует Google News redirect URL → реальный URL статьи."""
    if "news.google.com" not in gnews_url:
        return gnews_url
    m = re.search(r"/articles/([^?]+)", gnews_url)
    if not m:
        return ""
    encoded = m.group(1)
    try:
        padded = encoded + "=" * (4 - len(encoded) % 4)
        raw = base64.urlsafe_b64decode(padded)
    except Exception:  # noqa: BLE001
        return ""
    url_match = re.search(rb"https?://[^\x00-\x1f\x7f-\xff]+", raw)
    if url_match:
        return url_match.group(0).decode("utf-8", errors="ignore").rstrip("\x00")
    return ""


def _format_rss(items: list[dict]) -> str:
    """Форматирует RSS-результаты для передачи в LLM — без URL, только заголовок/источник/дата."""
    lines = []
    for i, r in enumerate(items, 1):
        title = r.get("title", "")
        date = r.get("date", "")[:16]
        source = r.get("source", "")
        line = f"{i}. {title}"
        meta = [x for x in (source, date) if x]
        if meta:
            line += f"  ({', '.join(meta)})"
        lines.append(line)
    return "\n\n".join(lines)


class SearchTool(Tool):
    """Поиск: Google News RSS → LLM-суммаризация. Без фантазий — только реальные данные."""

    name = "search"

    def __init__(self, llm_router: "LlmRouter | None" = None) -> None:
        self._router = llm_router

    async def execute(self, task: BaseTask) -> ToolResult:
        query = getattr(task, "query", None) or getattr(task, "topic", "")
        max_results = getattr(task, "max_results", _MAX_RESULTS)
        task_type = getattr(task, "type", None)
        is_news = task_type == TaskType.NEWS

        # И NEWS и SEARCH идут через один путь: RSS → LLM-суммаризация
        return await self._search(query, max_results, is_news)

    async def _search(self, query: str, max_results: int, is_news: bool) -> ToolResult:
        """RSS → LLM-суммаризация. Если RSS недоступен — ошибка, без фантазий."""
        try:
            items = _fetch_gnews(query, max_results)
        except Exception as e:  # noqa: BLE001
            label = "новостей" if is_news else "поиска"
            return ToolResult(
                success=False,
                summary=f"Ошибка получения данных для {label}: {e}",
                error=str(e),
            )

        if not items:
            label = "новостей" if is_news else "результатов"
            return ToolResult(
                success=False,
                summary=f"RSS вернул пустой результат по запросу {label}: {query!r}",
            )

        # LLM-суммаризация реальных RSS-результатов
        if self._router is not None:
            try:
                rss_data = _format_rss(items)
                prompt = _LLM_SUMMARIZE_PROMPT.format(
                    topic=query, max_results=max_results, rss_data=rss_data,
                )
                digest = await self._router.complete(prompt)
                label = "Новости" if is_news else "Результаты поиска"
                return ToolResult(
                    success=True,
                    summary=f"{label} по теме: {query!r}\n\n{digest}",
                    data={"query": query, "results": items},
                )
            except Exception:  # noqa: BLE001
                pass  # fallback к сырому формату

        # Fallback: сырой RSS без ссылок (если LLM недоступен)
        label = "Новости" if is_news else "Результаты"
        summary = f"{label} по запросу: {query!r} ({len(items)} шт.)\n\n{_format_rss(items)}"
        return ToolResult(success=True, summary=summary, data={"query": query, "results": items})
