"""Тесты загрузки настроек."""

from apiat.config import Settings


def test_whitelist_split(monkeypatch):
    monkeypatch.setenv("WHITELIST", "A@x.com, B@Y.com")
    s = Settings(_env_file=None)
    assert s.whitelist == ["a@x.com", "b@y.com"]


def test_llm_defaults():
    s = Settings(_env_file=None)
    assert s.llm_base_url.startswith("http")
    assert s.llm_model_name


def test_env_override(monkeypatch):
    monkeypatch.setenv("LLM_MODEL_NAME", "test-model")
    monkeypatch.setenv("POLL_INTERVAL", "5")
    s = Settings(_env_file=None)
    assert s.llm_model_name == "test-model"
    assert s.poll_interval == 5
