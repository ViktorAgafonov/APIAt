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


class _PollMode:
    """Адаптивный интервал опроса IMAP.

    NORMAL  — базовый интервал ± 9 сек (из настроек, ~60 сек)
    FAST    — 17–24 сек: ≥2 запросов за последние 2 мин
    IDLE    — 2–7 мин: простой >30 мин
    Снижение FAST→NORMAL: нет запросов >9 мин.
    """

    NORMAL = "normal"
    FAST   = "fast"
    IDLE   = "idle"

    def __init__(self, base_interval: int) -> None:
        self._base = base_interval
        self._mode = self.NORMAL
        self._last_activity = time.monotonic()   # последний запрос
        self._recent: list[float] = []           # время последних запросов

    def record_activity(self, count: int) -> None:
        """Вызывается после итерации с ненулевым числом писем."""
        if count > 0:
            now = time.monotonic()
            self._last_activity = now
            self._recent.append(now)

    def next_sleep(self) -> float:
        now = time.monotonic()
        idle_sec = now - self._last_activity

        # Обрезаем recent: только последние 2 минуты
        self._recent = [t for t in self._recent if now - t <= 120]

        prev = self._mode

        if len(self._recent) >= 2:
            self._mode = self.FAST
        elif idle_sec > 30 * 60:
            self._mode = self.IDLE
        elif self._mode == self.FAST and idle_sec > 9 * 60:
            self._mode = self.NORMAL
        elif self._mode == self.IDLE and idle_sec <= 30 * 60:
            self._mode = self.NORMAL

        if self._mode != prev:
            logger.info("Polling mode: %s → %s (простой %.0f сек)", prev, self._mode, idle_sec)

        if self._mode == self.FAST:
            return random.uniform(17, 24)
        if self._mode == self.IDLE:
            return random.uniform(120, 420)
        return self._base + random.randint(-9, 9)


async def _run_daemon(agent: Agent, interval: int) -> None:
    logger.info("Запуск демона, базовый интервал: %d сек", interval)
    poller = _PollMode(interval)
    cleanup_counter = 0
    _CLEANUP_EVERY = 50  # каждые ~50 итераций (~50 мин при interval=60)
    while True:
        try:
            count = await agent.run_once()
            poller.record_activity(count)
        except Exception:  # noqa: BLE001 - демон не должен падать на одной итерации
            logger.exception("Ошибка итерации демона")
            count = 0
        # Периодическая очистка устаревших файлов
        cleanup_counter += 1
        if cleanup_counter >= _CLEANUP_EVERY:
            cleanup_counter = 0
            try:
                cleanup_stale(agent.settings.data_dir)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Ошибка cleanup_stale: %s", exc)
        sleep_sec = poller.next_sleep()
        logger.debug("Следующий опрос через %.0f сек (режим: %s)", sleep_sec, poller._mode)
        time.sleep(sleep_sec)


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
