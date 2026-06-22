"""LLM Router: авто-переключение между провайдерами с диагностикой.

Логика:
1. Пробуем провайдеров в порядке приоритета (primary → fallback).
2. При ошибке провайдера — фиксируем причину, переходим к следующему.
3. Если все провайдеры недоступны — бросаем LlmAllProvidersFailedError.
4. Активный провайдер кэшируется в памяти до первого сбоя (не дёргаем
   основной при каждом запросе, если он уже известен как сломанный).
"""

from __future__ import annotations

import asyncio
from typing import Any

from ..config import LlmProviderConfig, Settings
from ..utils.logging import get_logger

logger = get_logger(__name__)

_RETRY_AFTER = 120  # секунд до повторной проверки упавшего провайдера


class LlmAllProvidersFailedError(RuntimeError):
    """Все LLM-провайдеры недоступны."""

    def __init__(self, reasons: dict[str, str]) -> None:
        self.reasons = reasons
        parts = "; ".join(f"{k}: {v}" for k, v in reasons.items())
        super().__init__(f"Все LLM-провайдеры недоступны — {parts}")


class LlmRouter:
    """Роутер LLM-провайдеров с автоматическим failover и диагностикой."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        # provider_name -> время последней ошибки (monotonic)
        self._failed_at: dict[str, float] = {}
        # кэшированные pydantic-ai модели
        self._models: dict[str, Any] = {}

    def _is_cooling_down(self, name: str) -> bool:
        """Провайдер в паузе после ошибки."""
        t = self._failed_at.get(name)
        if t is None:
            return False
        return (asyncio.get_event_loop().time() - t) < _RETRY_AFTER

    def _mark_failed(self, name: str) -> None:
        self._failed_at[name] = asyncio.get_event_loop().time()
        self._models.pop(name, None)  # сбрасываем закэшированную модель

    def _reset(self, name: str) -> None:
        self._failed_at.pop(name, None)

    def _build_model(self, cfg: LlmProviderConfig) -> Any:
        if cfg.name in self._models:
            return self._models[cfg.name]
        model = _build_pydantic_ai_model(cfg)
        self._models[cfg.name] = model
        return model

    async def run_agent(self, agent_factory, prompt: str) -> Any:
        """Запускает PydanticAI-агент через каждый провайдер по порядку.

        agent_factory(model) -> pydantic_ai.Agent
        """
        providers = self._settings.llm_providers()
        reasons: dict[str, str] = {}

        for cfg in providers:
            if self._is_cooling_down(cfg.name):
                reasons[cfg.name] = "cooldown после ошибки"
                continue
            try:
                model = self._build_model(cfg)
                agent = agent_factory(model)
                result = await agent.run(prompt)
                if cfg.name in self._failed_at:
                    logger.info("Провайдер '%s' снова доступен", cfg.name)
                    self._reset(cfg.name)
                return result
            except Exception as exc:  # noqa: BLE001
                reason = f"{type(exc).__name__}: {exc}"
                logger.warning("Провайдер '%s' недоступен: %s", cfg.name, reason)
                reasons[cfg.name] = reason
                self._mark_failed(cfg.name)

        raise LlmAllProvidersFailedError(reasons)

    @property
    def active_provider_name(self) -> str:
        """Имя первого не-cooldown провайдера (для диагностики)."""
        for cfg in self._settings.llm_providers():
            if not self._is_cooling_down(cfg.name):
                return cfg.name
        return "none"


def _build_pydantic_ai_model(cfg: LlmProviderConfig) -> Any:
    """Создаёт pydantic-ai модель нужного типа."""
    if cfg.provider_type == "google":
        return _build_google_model(cfg)
    return _build_openai_model(cfg)


def _build_openai_model(cfg: LlmProviderConfig) -> Any:
    from openai import AsyncOpenAI
    from pydantic_ai.models.openai import OpenAIModel
    from pydantic_ai.providers.openai import OpenAIProvider

    client = AsyncOpenAI(base_url=cfg.base_url, api_key=cfg.api_key)
    provider = OpenAIProvider(openai_client=client)
    return OpenAIModel(cfg.model_name, provider=provider)


def _build_google_model(cfg: LlmProviderConfig) -> Any:
    from pydantic_ai.models.gemini import GeminiModel
    from pydantic_ai.providers.google import GoogleProvider

    provider = GoogleProvider(api_key=cfg.api_key)
    return GeminiModel(cfg.model_name, provider=provider)
