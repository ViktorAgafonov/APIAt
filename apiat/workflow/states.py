"""Константы состояний конечного автомата задачи."""

from __future__ import annotations

from ..models.base import TaskStatus

# Допустимые переходы автомата (для валидации/документации)
TRANSITIONS: dict[TaskStatus, tuple[TaskStatus, ...]] = {
    TaskStatus.WAITING: (TaskStatus.PARSED, TaskStatus.FAILED),
    TaskStatus.PARSED: (TaskStatus.PLANNED, TaskStatus.FAILED),
    TaskStatus.PLANNED: (TaskStatus.EXECUTING, TaskStatus.FAILED),
    TaskStatus.EXECUTING: (TaskStatus.COMPLETED, TaskStatus.FAILED),
    TaskStatus.COMPLETED: (),
    TaskStatus.FAILED: (),
}


def can_transition(src: TaskStatus, dst: TaskStatus) -> bool:
    """Проверяет допустимость перехода между состояниями."""
    return dst in TRANSITIONS.get(src, ())
