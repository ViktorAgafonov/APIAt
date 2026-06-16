"""Проверка Burr-движка на синтетической задаче (без LLM и почты)."""

import asyncio

from apiat.config import get_settings
from apiat.models.tasks import SearchTask
from apiat.tools.registry import ToolRegistry
from apiat.workflow.engine import WorkflowEngine


async def main() -> None:
    s = get_settings()
    s.ensure_dirs()
    engine = WorkflowEngine(ToolRegistry(s.data_dir), s.db_path)
    task = SearchTask(query="ubuntu server torrent")
    state = await engine.run(task)
    print("status:", state.get("status"))
    print("workflow:", state.get("workflow"))
    print("result:", state.get("result"))


if __name__ == "__main__":
    asyncio.run(main())
