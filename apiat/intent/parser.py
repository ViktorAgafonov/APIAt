"""Intent Parsing Layer: свободный текст -> типизированная задача (PydanticAI)."""

from __future__ import annotations

from typing import Any

from ..config import Settings
from ..models.email import IncomingMail
from ..models.tasks import AnyTask
from ..utils.logging import get_logger
from .llm import build_model

logger = get_logger(__name__)

SYSTEM_PROMPT = (
    "Ты — парсер намерений персонального интернет-агента. "
    "Преобразуй текст письма пользователя в строго типизированную задачу. "
    "Выбери подходящий тип: search, news, download, youtube, browser, file. "
    "Извлекай URL, формат и параметры из текста. "
    "Если задача о видео/аудио с YouTube — используй youtube. "
    "Если нужен только поиск информации — search или news."
)


class IntentParser:
    """Обёртка над PydanticAI Agent для разбора интентов."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._agent: Any | None = None

    def _get_agent(self) -> Any:
        if self._agent is None:
            from pydantic_ai import Agent

            self._agent = Agent(
                model=build_model(self._settings),
                output_type=AnyTask,
                system_prompt=SYSTEM_PROMPT,
            )
        return self._agent

    async def parse(self, mail: IncomingMail) -> AnyTask:
        """Разбирает письмо в типизированную задачу."""
        text = f"Тема: {mail.subject}\n\n{mail.body}"
        result = await self._get_agent().run(text)
        task = result.output
        task.source_email = mail.sender
        task.message_id = mail.message_id
        logger.info("Распознана задача типа %s (task_id=%s)", task.type, task.task_id)
        return task
