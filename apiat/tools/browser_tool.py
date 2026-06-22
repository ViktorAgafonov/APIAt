"""Browser Tool: резервный режим через Playwright/Chromium.

Браузер запускается только по запросу (ленивый импорт), чтобы не держать
тяжёлый Chromium в памяти в режиме ожидания.
Сессии/cookies сохраняются в SQLite между перезапусками.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from urllib.parse import urlparse

from ..models.base import BaseTask
from ..utils.logging import get_logger
from .base import Tool, ToolResult

if TYPE_CHECKING:
    from ..storage.repositories import Storage

logger = get_logger(__name__)


class BrowserTool(Tool):
    """Открывает страницу и извлекает текст. Запускается только при необходимости."""

    name = "browser"

    def __init__(self, storage: "Storage | None" = None) -> None:
        self._storage = storage

    async def execute(self, task: BaseTask) -> ToolResult:
        url = getattr(task, "url", None)
        if not url:
            return ToolResult(success=False, error="URL не задан")
        try:
            text = await self._fetch_text(url)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, error=f"Ошибка браузера: {exc}")
        return ToolResult(
            success=True,
            summary=f"Извлечена страница {url} ({len(text)} симв.)",
            data={"url": url, "text": text[:5000]},
        )

    async def _fetch_text(self, url: str) -> str:
        from playwright.async_api import async_playwright  # ленивый импорт

        domain = urlparse(url).netloc
        saved_state = self._storage.load_browser_session(domain) if self._storage else None

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                ctx_opts = {"storage_state": saved_state} if saved_state else {}
                context = await browser.new_context(**ctx_opts)
                page = await context.new_page()
                await page.goto(url, wait_until="domcontentloaded")
                text = await page.inner_text("body")
                # Сохраняем состояние сессии
                if self._storage:
                    state = await context.storage_state()
                    self._storage.save_browser_session(domain, state)
                    if state.get("cookies"):
                        self._storage.save_cookies(domain, state["cookies"])
                return text
            finally:
                await browser.close()
