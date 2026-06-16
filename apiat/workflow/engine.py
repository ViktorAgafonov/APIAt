"""Workflow Engine на Burr: конечный автомат с персистентностью в SQLite."""

from __future__ import annotations

from pathlib import Path

from burr.core import ApplicationBuilder, default, expr
from burr.core.persistence import SQLLitePersister

from ..models.base import TaskStatus
from ..models.tasks import AnyTask
from ..tools.registry import ToolRegistry
from ..utils.logging import get_logger
from . import actions

logger = get_logger(__name__)


class WorkflowEngine:
    """Создаёт и исполняет Burr-приложение для одной задачи.

    Состояние сохраняется в SQLite (отдельная таблица), что позволяет
    возобновлять выполнение после перезапуска процесса.
    """

    def __init__(self, registry: ToolRegistry, db_path: str | Path) -> None:
        self._registry = registry
        self._db_path = str(db_path)
        actions.bind_registry(registry)

    def _build_app(self, task: AnyTask):
        persister = SQLLitePersister(db_path=self._db_path, table_name="burr_state")
        persister.initialize()

        builder = (
            ApplicationBuilder()
            .with_actions(
                plan=actions.plan,
                execute=actions.execute,
                complete=actions.complete,
                fail=actions.fail,
            )
            .with_transitions(
                ("plan", "execute"),
                ("execute", "fail", expr("result['success'] == False")),
                ("execute", "complete", default),
            )
            .with_identifiers(app_id=task.task_id)
            .with_state_persister(persister)
            .initialize_from(
                persister,
                resume_at_next_action=True,
                default_state={
                    "task": task.model_dump(mode="json"),
                    "status": TaskStatus.PARSED.value,
                    "result": {},
                    "workflow": "",
                },
                default_entrypoint="plan",
            )
        )
        return builder.build()

    async def run(self, task: AnyTask) -> dict:
        """Исполняет задачу до терминального состояния, возвращает финальный state."""
        app = self._build_app(task)
        logger.info("Запуск workflow для задачи %s", task.task_id)
        _, _, state = await app.arun(halt_after=["complete", "fail"])
        return dict(state.get_all())
