"""Точка входа: режимы --once (cron/one-shot) и --daemon (цикл опроса IMAP)."""

from __future__ import annotations

import argparse
import asyncio
import random
import subprocess
import time
from pathlib import Path

from .config import get_settings
from .main import Agent
from .utils.cache import cleanup_stale
from .utils.logging import get_logger, setup_logging

logger = get_logger(__name__)

_DOCKERFILE = Path(__file__).parent.parent / "docker" / "skill-sandbox.Dockerfile"


def _ensure_sandbox_image() -> None:
    """Собирает образ apiat-sandbox если его нет или Dockerfile новее образа."""
    try:
        r = subprocess.run(
            ["docker", "image", "inspect", "apiat-sandbox:latest"],
            capture_output=True, timeout=10,
        )
        if r.returncode == 0:
            return  # образ уже есть
        if not _DOCKERFILE.exists():
            logger.warning("Dockerfile для sandbox не найден: %s", _DOCKERFILE)
            return
        logger.info("Сборка образа apiat-sandbox...")
        subprocess.run(
            ["docker", "build", "-f", str(_DOCKERFILE), "-t", "apiat-sandbox:latest",
             str(_DOCKERFILE.parent)],
            check=True, timeout=120,
        )
        logger.info("Образ apiat-sandbox собран")
    except (subprocess.SubprocessError, FileNotFoundError):
        logger.warning("Docker недоступен, sandbox навыков не будет работать")


async def _run_once(agent: Agent) -> None:
    await agent.run_once()


async def _run_daemon(agent: Agent, interval: int) -> None:
    logger.info("Запуск демона, базовый интервал: %d сек", interval)
    while True:
        try:
            await agent.run_once()
        except Exception:  # noqa: BLE001 - демон не должен падать на одной итерации
            logger.exception("Ошибка итерации демона")
        jitter = random.randint(-9, 9)
        time.sleep(interval + jitter)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="apiat", description="APIAt — персональный интернет-агент")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--once", action="store_true", help="однократная обработка и выход")
    group.add_argument("--daemon", action="store_true", help="постоянный опрос IMAP")
    args = parser.parse_args(argv)

    settings = get_settings()
    setup_logging(settings.log_level)
    settings.ensure_dirs()
    cleanup_stale(settings.data_dir)
    _ensure_sandbox_image()
    agent = Agent(settings)

    if args.once:
        asyncio.run(_run_once(agent))
    else:
        asyncio.run(_run_daemon(agent, settings.poll_interval))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
