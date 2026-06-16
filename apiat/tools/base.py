"""Абстракция инструмента и результат выполнения."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from pydantic import BaseModel, Field

from ..models.base import BaseTask
from ..models.email import Attachment


class ToolResult(BaseModel):
    """Унифицированный результат работы инструмента."""

    success: bool
    summary: str = ""
    data: dict[str, Any] = Field(default_factory=dict)
    attachments: list[Attachment] = Field(default_factory=list)
    error: str | None = None


class Tool(ABC):
    """Базовый класс инструмента. Инструменты не зависят друг от друга."""

    name: str = "tool"

    @abstractmethod
    async def execute(self, task: BaseTask) -> ToolResult:
        """Выполняет задачу и возвращает результат."""
        raise NotImplementedError
