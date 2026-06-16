"""Настройки приложения. Все параметры читаются из .env, без хардкода."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Annotated

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    """Конфигурация комплекса, загружается из переменных окружения / .env."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- LLM (OpenAI-совместимый протокол) ---
    llm_base_url: str = Field(default="https://ai.wormsoft.ru/api/gpt/", alias="LLM_BASE_URL")
    llm_api_key: str = Field(default="your-wormsoft-token", alias="LLM_API_KEY")
    llm_model_name: str = Field(default="wormsoft/agent/medium", alias="LLM_MODEL_NAME")

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

    def ensure_dirs(self) -> None:
        """Создаёт каталоги данных при необходимости."""
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """Возвращает кэшированный экземпляр настроек."""
    return Settings()
