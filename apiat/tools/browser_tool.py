"""Browser Tool: резервный режим через Playwright/Chromium.

Браузер запускается только по запросу (ленивый импорт), чтобы не держать
тяжёлый Chromium в памяти в режиме ожидания.
Сессии/cookies сохраняются в SQLite между перезапусками.
"""

from __future__ import annotations

import re
import time
from typing import TYPE_CHECKING, Any
from pathlib import Path
from urllib.parse import urlparse

from ..config import Settings
from ..models.base import BaseTask
from ..utils.logging import get_logger
from .base import Tool, ToolResult

if TYPE_CHECKING:
    from ..storage.repositories import Storage

logger = get_logger(__name__)


class BrowserTool(Tool):
    """Открывает страницу, извлекает текст и может сделать скриншот."""

    name = "browser"

    def __init__(self, storage: "Storage | None" = None) -> None:
        self._storage = storage

    async def execute(self, task: BaseTask) -> ToolResult:
        url = getattr(task, "url", None)
        if not url:
            return ToolResult(success=False, error="URL не задан")
        screenshot = getattr(task, "screenshot", False)
        try:
            text, screenshot_path = await self._fetch_page(url, screenshot=screenshot)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, error=f"Ошибка браузера: {exc}")
        data: dict[str, Any] = {"url": url, "text": text[:5000]}
        attachments: list[dict[str, Any]] = []
        if screenshot_path:
            data["screenshot"] = str(screenshot_path)
            attachments.append({
                "filename": screenshot_path.name,
                "content_type": "image/png",
                "path": str(screenshot_path),
                "size": screenshot_path.stat().st_size,
            })
        return ToolResult(
            success=True,
            summary=f"Извлечена страница {url} ({len(text)} симв.)",
            data=data,
            attachments=attachments,
        )

    async def _fetch_page(self, url: str, screenshot: bool = False) -> tuple[str, Path | None]:
        from playwright.async_api import async_playwright  # ленивый импорт

        domain = urlparse(url).netloc
        saved_state = self._storage.load_browser_session(domain) if self._storage else None
        screenshot_path: Path | None = None

        async with async_playwright() as pw:
            browser = await pw.chromium.launch(headless=True)
            try:
                ctx_opts = {"storage_state": saved_state} if saved_state else {}
                context = await browser.new_context(**ctx_opts)
                page = await context.new_page()
                await page.goto(url, wait_until="domcontentloaded")
                text = await page.inner_text("body")

                if screenshot:
                    shot_dir = Path(Settings().data_dir) / "screenshots"
                    shot_dir.mkdir(parents=True, exist_ok=True)
                    ts = time.strftime("%Y%m%d_%H%M%S")
                    safe_domain = re.sub(r"[^\w.-]", "_", domain)[:50]
                    screenshot_path = shot_dir / f"{safe_domain}_{ts}.png"
                    await page.screenshot(path=str(screenshot_path), full_page=True)
                    logger.info("Скриншот сохранён: %s", screenshot_path)

                # Сохраняем состояние сессии
                if self._storage:
                    state = await context.storage_state()
                    self._storage.save_browser_session(domain, state)
                    if state.get("cookies"):
                        self._storage.save_cookies(domain, state["cookies"])
                return text, screenshot_path
            finally:
                await browser.close()
