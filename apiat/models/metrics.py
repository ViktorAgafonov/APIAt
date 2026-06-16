"""Метрики выполнения задачи."""

from __future__ import annotations

from pydantic import BaseModel


class TaskMetrics(BaseModel):
    """Метрики по одной задаче (см. раздел 'Метрики' в ТЗ)."""

    task_id: str
    execution_time: float = 0.0  # секунды
    input_size: int = 0
    output_size: int = 0
    emails_used: int = 0
    internet_requests: int = 0
    downloaded_bytes: int = 0
    result_size: int = 0
