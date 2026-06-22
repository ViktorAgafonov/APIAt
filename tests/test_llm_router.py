"""Тесты LlmRouter: failover, cooldown, self-correction rollback."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from apiat.config import LlmProviderConfig, Settings
from apiat.intent.router import LlmAllProvidersFailedError, LlmRouter
from apiat.intent.self_corrector import SelfCorrector


# ---------------------------------------------------------------------------
# Вспомогательные настройки без реального .env
# ---------------------------------------------------------------------------

def _make_settings(**kwargs) -> Settings:
    defaults = dict(
        llm_base_url="https://primary.example.com/",
        llm_api_key="primary-key",
        llm_model_name="test-model",
        llm_fallback_api_key="fallback-key",
        llm_fallback_model_name="gemini-test",
        imap_host="", smtp_host="",
        whitelist=[],
        secret_token="x",
    )
    defaults.update(kwargs)
    return Settings.model_validate(defaults)


# ---------------------------------------------------------------------------
# Тест: успешный вызов primary
# ---------------------------------------------------------------------------

def test_router_providers_order():
    s = _make_settings()
    providers = s.llm_providers()
    assert providers[0].name == "primary"
    assert providers[1].name == "fallback"
    assert providers[0].priority < providers[1].priority


def test_router_no_fallback_when_key_empty():
    s = Settings(_env_file=None, llm_fallback_api_key="", llm_base_url="https://x.com/",
                 llm_api_key="k", llm_model_name="m", imap_host="", smtp_host="",
                 whitelist=[], secret_token="x")
    assert len(s.llm_providers()) == 1


@pytest.mark.asyncio
async def test_router_uses_primary_on_success():
    s = _make_settings()
    router = LlmRouter(s)
    call_log: list[str] = []

    def factory(model):
        agent = AsyncMock()
        agent.run = AsyncMock(return_value="result_primary")
        call_log.append(model._name if hasattr(model, "_name") else "model")
        return agent

    with patch("apiat.intent.router._build_pydantic_ai_model", side_effect=lambda cfg: cfg):
        result = await router.run_agent(factory, "test")

    assert result == "result_primary"


@pytest.mark.asyncio
async def test_router_falls_back_on_primary_error():
    s = _make_settings()
    router = LlmRouter(s)
    attempts: list[str] = []

    def factory(model):
        agent = AsyncMock()
        if model.name == "primary":
            agent.run = AsyncMock(side_effect=RuntimeError("primary down"))
        else:
            agent.run = AsyncMock(return_value="fallback_result")
        attempts.append(model.name)
        return agent

    with patch("apiat.intent.router._build_pydantic_ai_model", side_effect=lambda cfg: cfg):
        result = await router.run_agent(factory, "test")

    assert result == "fallback_result"
    assert "primary" in attempts
    assert "fallback" in attempts


@pytest.mark.asyncio
async def test_router_all_fail_raises():
    s = _make_settings()
    router = LlmRouter(s)

    def factory(model):
        agent = AsyncMock()
        agent.run = AsyncMock(side_effect=RuntimeError("down"))
        return agent

    with patch("apiat.intent.router._build_pydantic_ai_model", side_effect=lambda cfg: cfg):
        with pytest.raises(LlmAllProvidersFailedError) as exc_info:
            await router.run_agent(factory, "test")

    assert "primary" in exc_info.value.reasons
    assert "fallback" in exc_info.value.reasons


@pytest.mark.asyncio
async def test_router_cooldown_skips_failed_provider():
    s = _make_settings()
    router = LlmRouter(s)
    attempts: list[str] = []

    def factory(model):
        agent = AsyncMock()
        if model.name == "primary":
            agent.run = AsyncMock(side_effect=RuntimeError("primary down"))
        else:
            agent.run = AsyncMock(return_value="ok")
        attempts.append(model.name)
        return agent

    with patch("apiat.intent.router._build_pydantic_ai_model", side_effect=lambda cfg: cfg):
        # Первый запрос: primary падает, переходим к fallback
        await router.run_agent(factory, "test")
        attempts.clear()
        # Второй запрос: primary в cooldown, сразу fallback
        result = await router.run_agent(factory, "test")

    assert result == "ok"
    assert "primary" not in attempts  # cooldown, не трогаем


# ---------------------------------------------------------------------------
# Тесты SelfCorrector
# ---------------------------------------------------------------------------

def test_self_corrector_apply_and_rollback(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_BASE_URL=https://old.example.com/\nLLM_API_KEY=old-key\n")
    corrector = SelfCorrector(env_file)

    applied = corrector.apply_patch({"LLM_API_KEY": "new-key"})
    assert "new-key" in env_file.read_text()
    assert any("new-key" in line for line in applied)
    assert corrector.has_backup()

    rollback_msg = corrector.rollback()
    assert "old-key" in env_file.read_text()
    assert "восстановлен" in rollback_msg.lower() or "откат" in rollback_msg.lower()


def test_self_corrector_adds_new_key(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_BASE_URL=https://x.com/\n")
    corrector = SelfCorrector(env_file)
    corrector.apply_patch({"LLM_FALLBACK_API_KEY": "new-fallback"})
    assert "LLM_FALLBACK_API_KEY=new-fallback" in env_file.read_text()


def test_self_corrector_diff(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("LLM_API_KEY=old\n")
    corrector = SelfCorrector(env_file)
    corrector.apply_patch({"LLM_API_KEY": "new"})
    diff = corrector.diff()
    assert any("LLM_API_KEY" in line for line in diff)


def test_parse_env_updates_filters_allowed():
    from apiat.main import _parse_env_updates
    body = "переключи llm\nLLM_API_KEY=abc\nIMAP_HOST=evil.com\nLLM_MODEL_NAME=new-model"
    result = _parse_env_updates(body)
    assert "LLM_API_KEY" in result
    assert "LLM_MODEL_NAME" in result
    assert "IMAP_HOST" not in result  # не LLM-ключ
