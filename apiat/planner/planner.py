"""Task Planner: выбор workflow по типу задачи."""

from __future__ import annotations

from ..models.base import TaskType

# Соответствие тип задачи -> имя workflow
TASK_WORKFLOW_MAP: dict[TaskType, str] = {
    TaskType.SEARCH: "SearchWorkflow",
    TaskType.NEWS: "SearchWorkflow",
    TaskType.YOUTUBE: "YoutubeWorkflow",
    TaskType.DOWNLOAD: "DownloadWorkflow",
    TaskType.BROWSER: "BrowserWorkflow",
    TaskType.FILE: "FileWorkflow",
    TaskType.SKILL: "SkillWorkflow",
    TaskType.CHAIN: "ChainWorkflow",
}


def select_workflow(task_type: TaskType) -> str:
    """Возвращает имя workflow для типа задачи."""
    return TASK_WORKFLOW_MAP.get(task_type, "SearchWorkflow")
