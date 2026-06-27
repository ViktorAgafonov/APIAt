"""Настройки приложения. Все параметры читаются из .env, без хардкода."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class LlmProviderConfig(BaseModel):
    """Конфигурация одного LLM-провайдера."""

    name: str
    provider_type: Literal["openai", "google"] = "openai"
    base_url: str = ""
    api_key: str = ""
    model_name: str = ""
    priority: int = 0  # меньше = выше приоритет

    @property
    def is_openai_compatible(self) -> bool:
        return self.provider_type == "openai"


class Settings(BaseSettings):
    """Конфигурация комплекса, загружается из переменных окружения / .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM Primary (OpenAI-совместимый, бесплатный, отладочный) ---
    llm_base_url: str = Field(default="https://ai.wormsoft.ru/api/gpt/", alias="LLM_BASE_URL")
    llm_api_key: str = Field(default="your-wormsoft-token", alias="LLM_API_KEY")
    llm_model_name: str = Field(default="wormsoft/agent/medium", alias="LLM_MODEL_NAME")

    # --- LLM Fallback (Google Gemini, платный, резервный) ---
    llm_fallback_base_url: str = Field(default="", alias="LLM_FALLBACK_BASE_URL")
    llm_fallback_api_key: str = Field(default="", alias="LLM_FALLBACK_API_KEY")
    llm_fallback_model_name: str = Field(default="gemini-2.5-flash", alias="LLM_FALLBACK_MODEL_NAME")

    # --- IMAP ---
    imap_host: str = Field(default="", alias="IMAP_HOST")
    imap_port: int = Field(default=993, alias="IMAP_PORT")
    imap_user: str = Field(default="", alias="IMAP_USER")
    imap_password: str = Field(default="", alias="IMAP_PASSWORD")
    imap_folder: str = Field(default="INBOX", alias="IMAP_FOLDER")
    imap_use_ssl: bool = Field(default=True, alias="IMAP_USE_SSL")

    # --- SMTP ---
    smtp_host: str = Field(default="", alias="SMTP_HOST")
    smtp_port: int = Field(default=465, alias="SMTP_PORT")
    smtp_user: str = Field(default="", alias="SMTP_USER")
    smtp_password: str = Field(default="", alias="SMTP_PASSWORD")
    smtp_use_ssl: bool = Field(default=True, alias="SMTP_USE_SSL")
    smtp_from: str = Field(default="", alias="SMTP_FROM")

    # --- Безопасность ---
    whitelist: Annotated[list[str], NoDecode] = Field(default_factory=list, alias="WHITELIST")
    secret_token: str = Field(default="", alias="SECRET_TOKEN")

    # --- Рантайм / хранилище ---
    db_path: Path = Field(default=Path("data/apiat.db"), alias="DB_PATH")
    data_dir: Path = Field(default=Path("data"), alias="DATA_DIR")
    poll_interval: int = Field(default=60, alias="POLL_INTERVAL")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")

    @field_validator("whitelist", mode="before")
    @classmethod
    def _split_whitelist(cls, value: object) -> object:
        # Разбираем строку "a@x, b@y" в список адресов
        if isinstance(value, str):
            return [item.strip().lower() for item in value.split(",") if item.strip()]
        return value

    @field_validator("llm_base_url")
    @classmethod
    def _ensure_trailing_slash(cls, value: str) -> str:
        # SDK добавляет относительный путь chat/completions — слеш обязателен
        return value if value.endswith("/") else value + "/"

    def llm_providers(self) -> list[LlmProviderConfig]:
        """Возвращает список провайдеров по приоритету (primary первый, fallback второй)."""
        providers: list[LlmProviderConfig] = [
            LlmProviderConfig(
                name="primary",
                provider_type="openai",
                base_url=self.llm_base_url,
                api_key=self.llm_api_key,
                model_name=self.llm_model_name,
                priority=0,
            ),
        ]
        if self.llm_fallback_api_key:
            # Если fallback_base_url задан — это openai-совместимый провайдер
            fb_type = "openai" if self.llm_fallback_base_url else "google"
            providers.append(
                LlmProviderConfig(
                    name="fallback",
                    provider_type=fb_type,
                    base_url=self.llm_fallback_base_url,
                    api_key=self.llm_fallback_api_key,
                    model_name=self.llm_fallback_model_name,
                    priority=1,
                )
            )
        return sorted(providers, key=lambda p: p.priority)

    def ensure_dirs(self) -> None:
        """Создаёт каталоги данных при необходимости."""
        for sub in [
            self.data_dir,
            self.data_dir / "downloads" / "pending",
            self.data_dir / "downloads" / "done",
            self.data_dir / "downloads" / "failed",
            self.data_dir / "browser" / "sessions",
            self.data_dir / "browser" / "screenshots",
            self.data_dir / "browser" / "cookies",
            self.data_dir / "archive" / "parts",
            self.data_dir / "tmp",
            self.data_dir / "skills",
            self.data_dir / "skills" / "pending",
            self.data_dir / "skills" / "chains",
        ]:
            sub.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """Возвращает кэшированный экземпляр настроек."""
    return Settings()
