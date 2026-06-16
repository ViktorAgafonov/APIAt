"""Базовые типы задач и статусы жизненного цикла."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from enum import Enum

from pydantic import BaseModel, Field


class TaskStatus(str, Enum):
    """Статусы конечного автомата задачи (Burr)."""

    WAITING = "WAITING"
    PARSED = "PARSED"
    PLANNED = "PLANNED"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class TaskType(str, Enum):
    """Типы задач, поддерживаемые планировщиком."""

    SEARCH = "search"
    DOWNLOAD = "download"
    YOUTUBE = "youtube"
    BROWSER = "browser"
    NEWS = "news"
    FILE = "file"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class BaseTask(BaseModel):
    """Общая модель задачи. Конкретные задачи наследуются и задают type."""

    task_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    type: TaskType
    source_email: str | None = None
    message_id: str | None = None
    created_at: datetime = Field(default_factory=_now)
