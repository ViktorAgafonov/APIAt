"""Server Tool: анализ логов, статус сервиса, диски, процессы на сервере."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

from ..models.base import BaseTask
from .base import Tool, ToolResult

if TYPE_CHECKING:
    from ..intent.router import LlmRouter

_LOG_LINES_DEFAULT = 100
_LOG_LINES_MAX = 500

_LLM_ANALYZE_PROMPT = """\
Ты системный администратор. Проанализируй логи приложения и подготовь отчёт оператору.

ЗАПРОС ОПЕРАТОРА:
{query}

ЛОГИ (последние {lines} строк):
{logs}

Требования:
- Найди ошибки, предупреждения, аномалии
- Укажи возможные причины проблем
- Дай рекомендации по исправлению
- Формат plain text, кратко и по делу
- Не включай сырые логи в ответ — только анализ
"""


class ServerTool(Tool):
    """Серверные задачи: чтение и анализ логов, статус сервиса, диски."""

    name = "server"

    def __init__(self, llm_router: "LlmRouter | None" = None) -> None:
        self._router = llm_router

    async def execute(self, task: BaseTask) -> ToolResult:
        action = getattr(task, "action", "custom")
        query = getattr(task, "query", "")
        lines = min(getattr(task, "lines", _LOG_LINES_DEFAULT), _LOG_LINES_MAX)

        if action == "logs":
            return await self._handle_logs(query, lines)
        elif action == "status":
            return self._handle_status()
        elif action == "disk":
            return self._handle_disk()
        elif action == "processes":
            return self._handle_processes()
        else:
            # custom — пытаемся понять по query
            if any(w in query.lower() for w in ("лог", "log", "журнал", "error", "ошибк")):
                return await self._handle_logs(query, lines)
            if any(w in query.lower() for w in ("статус", "status", "состояние")):
                return self._handle_status()
            if any(w in query.lower() for w in ("диск", "disk", "место", "space")):
                return self._handle_disk()
            return await self._handle_logs(query, lines)

    async def _handle_logs(self, query: str, lines: int) -> ToolResult:
        """Читает логи journalctl и анализирует через LLM."""
        try:
            result = subprocess.run(
                ["journalctl", "-u", "apiat", "--no-pager", "-n", str(lines)],
                capture_output=True, text=True, timeout=15,
            )
            logs = result.stdout
            if not logs:
                return ToolResult(success=False, summary="Логи пусты или journalctl недоступен")
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False, summary=f"Ошибка чтения логов: {e}", error=str(e))

        # LLM-анализ логов
        if self._router is not None and query:
            try:
                prompt = _LLM_ANALYZE_PROMPT.format(query=query, lines=lines, logs=logs[-8000:])
                analysis = await self._router.complete(prompt)
                return ToolResult(
                    success=True,
                    summary=f"Анализ логов APIAt (последние {lines} строк):\n\n{analysis}",
                )
            except Exception:  # noqa: BLE001
                pass  # fallback к сырым логам

        # Fallback: последние строки логов без анализа
        tail = "\n".join(logs.splitlines()[-50:])
        return ToolResult(
            success=True,
            summary=f"Логи APIAt (последние {min(50, lines)} строк):\n\n{tail}",
        )

    def _handle_status(self) -> ToolResult:
        """Статус сервиса apiat."""
        try:
            result = subprocess.run(
                ["systemctl", "status", "apiat", "--no-pager"],
                capture_output=True, text=True, timeout=10,
            )
            status_text = result.stdout or result.stderr
            return ToolResult(
                success=True,
                summary=f"Статус сервиса apiat:\n\n{status_text[:3000]}",
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False, summary=f"Ошибка получения статуса: {e}", error=str(e))

    def _handle_disk(self) -> ToolResult:
        """Использование диска."""
        try:
            result = subprocess.run(
                ["df", "-h", "/"],
                capture_output=True, text=True, timeout=10,
            )
            return ToolResult(
                success=True,
                summary=f"Использование диска:\n\n{result.stdout}",
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False, summary=f"Ошибка df: {e}", error=str(e))

    def _handle_processes(self) -> ToolResult:
        """Топ процессов по памяти."""
        try:
            result = subprocess.run(
                ["ps", "aux", "--sort=-%mem"],
                capture_output=True, text=True, timeout=10,
            )
            # Только топ-15
            lines = result.stdout.splitlines()[:16]
            return ToolResult(
                success=True,
                summary=f"Топ процессов по памяти:\n\n{chr(10).join(lines)}",
            )
        except Exception as e:  # noqa: BLE001
            return ToolResult(success=False, summary=f"Ошибка ps: {e}", error=str(e))
