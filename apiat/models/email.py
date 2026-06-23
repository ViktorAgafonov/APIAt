"""Модели входящей и исходящей почты."""

from __future__ import annotations

from datetime import datetime, timezone

from pydantic import BaseModel, Field


class Attachment(BaseModel):
    """Вложение письма."""

    filename: str
    content_type: str = "application/octet-stream"
    # Путь к файлу на диске (большие вложения не держим в памяти)
    path: str | None = None
    size: int = 0


class IncomingMail(BaseModel):
    """Разобранное входящее письмо."""

    message_id: str
    sender: str
    subject: str = ""
    body: str = ""
    received_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    attachments: list[Attachment] = Field(default_factory=list)
    references: str = ""  # заголовок References из входящего письма


class OutgoingMail(BaseModel):
    """Исходящее письмо с результатом."""

    to: str
    subject: str
    body: str
    attachments: list[Attachment] = Field(default_factory=list)
    in_reply_to: str | None = None
    references: str = ""  # для правильного треда в почтовом клиенте
