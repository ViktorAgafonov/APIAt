"""Intent Parsing Layer: свободный текст -> типизированная задача (PydanticAI)."""

from __future__ import annotations

from ..config import Settings
from ..models.email import IncomingMail
from ..models.tasks import AnyTask
from ..utils.logging import get_logger
from .router import LlmRouter

logger = get_logger(__name__)

SYSTEM_PROMPT = (
    "Ты — парсер намерений персонального интернет-агента. "
    "Преобразуй текст письма пользователя в строго типизированную задачу. "
    "Выбери подходящий тип: search, news, download, youtube, browser, file. "
    "Извлекай URL, формат и параметры из текста. "
    "Если задача о видео/аудио с YouTube — используй youtube. "
    "Если нужен только поиск информации — search или news. "
    "Если в тексте есть слово 'rss' или 'RSS' — установи use_rss=true в задаче. "
    "Без этого слова use_rss=false."
)


class IntentParser:
    """Обёртка над LlmRouter + PydanticAI Agent для разбора интентов."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._router = LlmRouter(settings)

    def _agent_factory(self, model):
        from pydantic_ai import Agent
        return Agent(model=model, output_type=AnyTask, system_prompt=SYSTEM_PROMPT)

    async def parse(self, mail: IncomingMail) -> AnyTask:
        """Разбирает письмо в типизированную задачу через LlmRouter."""
        text = f"Тема: {mail.subject}\n\n{mail.body}"
        if mail.attachments:
            att_info = ", ".join(
                f"{a.filename} ({a.content_type}, {a.size} bytes)"
                for a in mail.attachments
            )
            text += f"\n\n[Вложения от пользователя: {att_info}]"
        result = await self._router.run_agent(self._agent_factory, text)
        task = result.output
        task.source_email = mail.sender
        task.message_id = mail.message_id
        logger.info(
            "Распознана задача типа %s (task_id=%s) [провайдер: %s]",
            task.type, task.task_id, self._router.active_provider_name,
        )
        return task

    @property
    def router(self) -> LlmRouter:
        return self._router
