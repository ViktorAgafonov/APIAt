"""Intent Parsing Layer: свободный текст -> типизированная задача (PydanticAI)."""

from __future__ import annotations

from ..config import Settings
from ..models.email import IncomingMail
from ..models.tasks import AnyTask
from ..utils.logging import get_logger
from .router import LlmRouter

logger = get_logger(__name__)

SYSTEM_PROMPT = (
    "Ты — оператор интеллектуального персонального агента APIAt. "
    "Система обходит жёсткие сетевые блокировки через канал электронной почты, "
    "выполняя роль живого человека вне зоны ограничения доступа к интернету. "
    "Пользователь пишет простым человеческим языком, иногда намеренно завуалированно. "
    "Твоя задача — понять, что именно ему нужно, и выбрать правильный инструмент.\n\n"
    "Доступные инструменты:\n"
    "- search: поиск информации, новостей, документации\n"
    "- news: свежие новости по теме (можно с RSS)\n"
    "- download: скачать файл по URL (лимит 100 MB)\n"
    "- youtube: видео/аудио/субтитры/обложка с YouTube\n"
    "- browser: открыть страницу, авторизоваться, извлечь текст, сделать скриншот\n"
    "- file: упаковать вложения в zip или разбить файл на части\n"
    "- skill: запустить один из закреплённых навыков (если запрос явно про функцию навыка)\n"
    "- chain: запустить сохранённую цепочку навыков (если запрос требует несколько шагов)\n\n"
    "Правила:\n"
    "1. Если запрос просит что-то конкретное из списка выше — выбери соответствующий тип.\n"
    "2. Если запрос подходит под один из закреплённых навыков — верни type=skill и skill_name.\n"
    "3. Если запрос требует несколько шагов известных навыков — верни type=chain и chain_name.\n"
    "4. Если ничего не подходит — выбери search или news.\n"
    "5. Извлекай URL, формат и параметры из текста.\n"
    "6. Для YouTube: канал по имени — channel_search, url пустой; обложка — thumbnail=true; субтитры — subtitles=true; только метаданные — metadata_only=true.\n"
    "7. Если в тексте есть 'rss' или 'RSS' — установи use_rss=true.\n"
    "8. Если запрос просит скриншот или снимок страницы — выбери type=browser и установи screenshot=true."
)


class IntentParser:
    """Обёртка над LlmRouter + PydanticAI Agent для разбора интентов."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._router = LlmRouter(settings)

    def _agent_factory(self, model):
        from pydantic_ai import Agent
        return Agent(model=model, output_type=AnyTask, system_prompt=SYSTEM_PROMPT)

    async def parse(self, mail: IncomingMail, skills: list[str] | None = None,
                    chains: list[str] | None = None,
                    thread_context: str = "") -> AnyTask:
        """Разбирает письмо в типизированную задачу через LlmRouter."""
        text = f"Тема: {mail.subject}\n\n{mail.body}"
        if mail.attachments:
            att_info = ", ".join(
                f"{a.filename} ({a.content_type}, {a.size} bytes)"
                for a in mail.attachments
            )
            text += f"\n\n[Вложения от пользователя: {att_info}]"

        # Контекст возможностей системы и истории переписки для LLM-оператора
        ctx = []
        if thread_context:
            ctx.append(thread_context)
        if skills:
            ctx.append(f"Закреплённые навыки: {', '.join(skills)}")
        if chains:
            ctx.append(f"Сохранённые цепочки: {', '.join(chains)}")
        if ctx:
            text = "\n\n".join(ctx) + "\n\n" + text

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
