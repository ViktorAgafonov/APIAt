"""Browser Tool: резервный режим через Playwright/Chromium.

Браузер запускается только по запросу (ленивый импорт), чтобы не держать
тяжёлый Chromium в памяти в режиме ожидания.
"""

from __future__ import annotations

from ..models.base import BaseTask
from ..utils.logging import get_logger
from .base import Tool, ToolResult

logger = get_logger(__name__)


class BrowserTool(Tool):
    """Открывает страницу и извлекает текст. Запускается только при необходимости."""

    name = "browser"

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

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                page = await browser.new_page()
                await page.goto(url, wait_until="domcontentloaded")
                return await page.inner_text("body")
            finally:
                await browser.close()
