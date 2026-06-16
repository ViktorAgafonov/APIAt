"""Burr-действия жизненного цикла задачи.

Граф: plan -> execute -> (complete | fail).
Состояние сериализуется и сохраняется persister'ом движка.
"""

from __future__ import annotations

from burr.core import State, action

from ..models.base import TaskStatus, TaskType
from ..planner.planner import select_workflow
from ..tools.registry import ToolRegistry

# Реестр инструментов создаётся движком и передаётся в действия через привязку.
_registry: ToolRegistry | None = None


def bind_registry(registry: ToolRegistry) -> None:
    """Привязывает реестр инструментов для действий execute."""
    global _registry
    _registry = registry


@action(reads=["task"], writes=["workflow", "status"])
def plan(state: State) -> State:
    """Выбирает workflow по типу задачи (PLANNED)."""
    task = state["task"]
    task_type = TaskType(task["type"])
    return state.update(
        workflow=select_workflow(task_type),
        status=TaskStatus.PLANNED.value,
    )


@action(reads=["task"], writes=["result", "status"])
async def execute(state: State) -> State:
    """Выполняет задачу нужным инструментом (EXECUTING -> результат)."""
    if _registry is None:
        return state.update(
            result={"success": False, "error": "Реестр инструментов не привязан"},
            status=TaskStatus.EXECUTING.value,
        )
    task_dict = state["task"]
    task_type = TaskType(task_dict["type"])
    tool = _registry.for_task_type(task_type)
    if tool is None:
        return state.update(
            result={"success": False, "error": f"Нет инструмента для {task_type}"},
            status=TaskStatus.EXECUTING.value,
        )
    # Восстанавливаем типизированную задачу из payload
    task = _rebuild_task(task_dict)
    tool_result = await tool.execute(task)
    return state.update(
        result=tool_result.model_dump(),
        status=TaskStatus.EXECUTING.value,
    )


@action(reads=["result"], writes=["status"])
def complete(state: State) -> State:
    """Финализирует успешную задачу (COMPLETED)."""
    return state.update(status=TaskStatus.COMPLETED.value)


@action(reads=["result"], writes=["status"])
def fail(state: State) -> State:
    """Финализирует неуспешную задачу (FAILED)."""
    return state.update(status=TaskStatus.FAILED.value)


def _rebuild_task(task_dict: dict):
    """Восстанавливает типизированную задачу из сохранённого payload."""
    from pydantic import TypeAdapter

    from ..models.tasks import AnyTask

    return TypeAdapter(AnyTask).validate_python(task_dict)
