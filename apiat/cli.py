"""Точка входа: режимы --once (cron/one-shot) и --daemon (цикл опроса IMAP)."""

from __future__ import annotations

import argparse
import asyncio
import time

from .config import get_settings
from .main import Agent
from .utils.cache import cleanup_stale
from .utils.logging import get_logger, setup_logging

logger = get_logger(__name__)


async def _run_once(agent: Agent) -> None:
    await agent.run_once()


async def _run_daemon(agent: Agent, interval: int) -> None:
    logger.info("Запуск демона, интервал опроса: %d сек", interval)
    while True:
        try:
            await agent.run_once()
        except Exception:  # noqa: BLE001 - демон не должен падать на одной итерации
            logger.exception("Ошибка итерации демона")
        time.sleep(interval)


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
    agent = Agent(settings)

    if args.once:
        asyncio.run(_run_once(agent))
    else:
        asyncio.run(_run_daemon(agent, settings.poll_interval))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
