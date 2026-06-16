"""Инициализация LLM-клиента. Все параметры — из .env, без хардкода."""

from __future__ import annotations

from typing import Any

from ..config import Settings


def build_model(settings: Settings) -> Any:
    """Создаёт OpenAI-совместимую модель PydanticAI из настроек.

    Импорты ленивые: тяжёлые зависимости подтягиваются только при реальном
    использовании парсера, а не при импорте пакета.
    """
    from openai import AsyncOpenAI
    from pydantic_ai.models.openai import OpenAIModel
    from pydantic_ai.providers.openai import OpenAIProvider

    client = AsyncOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
    )
    provider = OpenAIProvider(openai_client=client)
    return OpenAIModel(settings.llm_model_name, provider=provider)
