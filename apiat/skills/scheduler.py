"""Scheduler: автоматический запуск навыков и цепочек по расписанию.

Расписания хранятся в data/schedules/<name>.schedule.json.
Проверяются каждую итерацию daemon-цикла.

Форматы расписаний (строка schedule):
  daily 18:00           — каждый день в 18:00
  daily 06:00,18:00     — дважды в день
  weekly fri 18:00      — каждую пятницу в 18:00
  weekly sat 09:00      — каждую субботу в 09:00

Уведомления отправляются ТОЛЬКО оператору-создателю (owner).
Рассылка по whitelist — только через явный навык внутри задач.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from ..utils.logging import get_logger
from .chain import ChainRunner, SkillChain

logger = get_logger(__name__)

_SCHEDULES_DIR = "schedules"

_WEEKDAYS = {
    "mon": 0, "tue": 1, "wed": 2, "thu": 3,
    "fri": 4, "sat": 5, "sun": 6,
}


@dataclass
class ScheduleTask:
    """Одна задача в расписании: навык или цепочка."""
    type: str  # "skill" | "chain"
    name: str
    params: dict[str, str] = field(default_factory=dict)


@dataclass
class Schedule:
    """Расписание запуска задач."""
    name: str
    description: str = ""
    schedule: str = ""  # "daily 18:00", "weekly fri 18:00"
    tasks: list[ScheduleTask] = field(default_factory=list)
    owner: str = ""  # email оператора-создателя
    last_run: str = ""  # ISO timestamp
    enabled: bool = True

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "schedule": self.schedule,
            "tasks": [
                {"type": t.type, "name": t.name, "params": t.params}
                for t in self.tasks
            ],
            "owner": self.owner,
            "last_run": self.last_run,
            "enabled": self.enabled,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Schedule":
        return cls(
            name=d["name"],
            description=d.get("description", ""),
            schedule=d.get("schedule", ""),
            tasks=[
                ScheduleTask(
                    type=t.get("type", "skill"),
                    name=t["name"],
                    params=t.get("params", {}),
                )
                for t in d.get("tasks", [])
            ],
            owner=d.get("owner", ""),
            last_run=d.get("last_run", ""),
            enabled=d.get("enabled", True),
        )


@dataclass
class ScheduleRunResult:
    """Результат выполнения расписания."""
    schedule_name: str
    success: bool
    outputs: list[str] = field(default_factory=list)
    error: str = ""


class Scheduler:
    """Управляет расписаниями: загрузка, проверка, запуск."""

    def __init__(self, data_dir: Path, chain_runner: ChainRunner) -> None:
        self._schedules_dir = data_dir / _SCHEDULES_DIR
        self._schedules_dir.mkdir(parents=True, exist_ok=True)
        self._chain_runner = chain_runner

    def list_schedules(self) -> list[Schedule]:
        """Возвращает все расписания."""
        result = []
        for p in sorted(self._schedules_dir.glob("*.schedule.json")):
            try:
                result.append(Schedule.from_dict(
                    json.loads(p.read_text(encoding="utf-8"))
                ))
            except Exception:  # noqa: BLE001
                logger.warning("Не удалось загрузить расписание: %s", p.name)
        return result

    def save_schedule(self, schedule: Schedule) -> Path:
        """Сохраняет расписание в JSON."""
        path = self._schedules_dir / f"{schedule.name}.schedule.json"
        path.write_text(
            json.dumps(schedule.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return path

    def delete_schedule(self, name: str) -> bool:
        """Удаляет расписание по имени."""
        path = self._schedules_dir / f"{name}.schedule.json"
        if path.exists():
            path.unlink()
            return True
        # Частичное совпадение
        matches = list(self._schedules_dir.glob(f"*{name}*.schedule.json"))
        if matches:
            matches[0].unlink()
            return True
        return False

    def set_enabled(self, name: str, enabled: bool) -> bool:
        """Включает/выключает расписание."""
        path = self._schedules_dir / f"{name}.schedule.json"
        if not path.exists():
            matches = list(self._schedules_dir.glob(f"*{name}*.schedule.json"))
            if not matches:
                return False
            path = matches[0]
        schedule = Schedule.from_dict(json.loads(path.read_text(encoding="utf-8")))
        schedule.enabled = enabled
        self.save_schedule(schedule)
        return True

    def check_and_run(self) -> list[ScheduleRunResult]:
        """Проверяет все расписания и запускает те, чьё время пришло.

        Возвращает список результатов запущенных расписаний.
        """
        results: list[ScheduleRunResult] = []
        now = datetime.now(timezone.utc)

        for schedule in self.list_schedules():
            if not schedule.enabled or not schedule.schedule:
                continue
            if not self._should_run(schedule, now):
                continue

            logger.info("Scheduler: запуск расписания '%s' (%s)", schedule.name, schedule.schedule)
            result = self._run_schedule(schedule)
            results.append(result)

            # Обновляем last_run
            schedule.last_run = now.isoformat()
            self.save_schedule(schedule)

        return results

    def _should_run(self, schedule: Schedule, now: datetime) -> bool:
        """Проверяет, должно ли расписание сработать сейчас."""
        # Парсим расписание
        parsed = _parse_schedule_str(schedule.schedule)
        if parsed is None:
            return False

        kind, times = parsed  # ("daily", [18, 0]) или ("weekly", [5, 9, 0])

        # Проверяем last_run — не запускаем дважды за один "слот"
        if schedule.last_run:
            try:
                last = datetime.fromisoformat(schedule.last_run)
                # Если последний запуск был сегодня (для daily) — пропускаем
                if last.date() == now.date() and kind == "daily":
                    # Но проверяем — может это другой слот времени
                    for hour, minute in times:
                        if last.hour < hour or (last.hour == hour and last.minute < minute):
                            if now.hour > hour or (now.hour == hour and now.minute >= minute):
                                return True
                    return False
                if kind == "weekly":
                    weekday, hour, minute = times[0], times[1], times[2]
                    if last.isocalendar()[1] == now.isocalendar()[1] and last.weekday() == weekday:
                        return False
            except Exception:  # noqa: BLE001
                pass

        if kind == "daily":
            for hour, minute in times:
                if now.hour == hour and now.minute >= minute and now.minute < minute + 5:
                    return True
            return False

        if kind == "weekly":
            weekday, hour, minute = times[0], times[1], times[2]
            if now.weekday() == weekday and now.hour == hour and now.minute >= minute and now.minute < minute + 5:
                return True
            return False

        return False

    def _run_schedule(self, schedule: Schedule) -> ScheduleRunResult:
        """Выполняет все задачи расписания по порядку."""
        result = ScheduleRunResult(schedule_name=schedule.name, success=True)

        for task in schedule.tasks:
            try:
                if task.type == "chain":
                    chain = self._chain_runner.load_chain(task.name)
                    if chain is None:
                        result.outputs.append(f"Цепочка '{task.name}' не найдена")
                        result.success = False
                        break
                    run_result = self._chain_runner.run(
                        chain, input_params=task.params,
                        max_steps=20,
                    )
                    output = run_result.report()
                    result.outputs.append(f"Цепочка '{task.name}':\n{output}")
                    if not run_result.success:
                        result.success = False
                        result.error = f"Цепочка '{task.name}' завершилась с ошибкой"
                        break

                elif task.type == "skill":
                    # Запуск навыка напрямую через sandbox
                    from .sandbox import DockerSandbox, SkillConfig
                    skill_path = self._chain_runner._skills_dir / f"{task.name}.py"
                    if not skill_path.exists():
                        result.outputs.append(f"Навык '{task.name}' не найден")
                        result.success = False
                        break
                    code = skill_path.read_text(encoding="utf-8")
                    cfg = SkillConfig.from_code(code)
                    sandbox = self._chain_runner._sandbox
                    sandbox_res = sandbox.run(code, cfg)
                    output = sandbox_res.stdout if sandbox_res.stdout else sandbox_res.stderr
                    result.outputs.append(f"Навык '{task.name}':\n{output[:2000]}")
                    if not sandbox_res.success:
                        result.success = False
                        result.error = f"Навык '{task.name}' завершился с ошибкой: {sandbox_res.stderr[:200]}"
                        break

            except Exception as exc:  # noqa: BLE001
                result.outputs.append(f"Ошибка выполнения '{task.name}': {exc}")
                result.success = False
                result.error = str(exc)
                break

        return result


def _parse_schedule_str(s: str) -> tuple[str, list[int]] | None:
    """Парсит строку расписания.

    Возвращает:
      ("daily", [(hour, minute), ...])
      ("weekly", [weekday, hour, minute])
      None если не распознано
    """
    s = s.strip().lower()

    # daily 18:00  или  daily 06:00,18:00
    m = re.match(r"daily\s+(\d{1,2}:\d{2}(?:,\d{1,2}:\d{2})*)", s)
    if m:
        times = []
        for part in m.group(1).split(","):
            h, _, minute = part.partition(":")
            times.append((int(h), int(minute)))
        return ("daily", times)

    # weekly fri 18:00
    m = re.match(r"weekly\s+(\w+)\s+(\d{1,2}):(\d{2})", s)
    if m:
        day_str = m.group(1)[:3]
        weekday = _WEEKDAYS.get(day_str)
        if weekday is None:
            return None
        return ("weekly", [weekday, int(m.group(2)), int(m.group(3))])

    return None


def format_schedule_report(results: list[ScheduleRunResult]) -> str:
    """Форматирует результаты запуска расписаний для отчёта."""
    if not results:
        return ""
    lines = []
    for r in results:
        status = "OK" if r.success else "ОШИБКА"
        lines.append(f"Расписание '{r.schedule_name}': {status}")
        for output in r.outputs:
            lines.append(output)
        if r.error:
            lines.append(f"Ошибка: {r.error}")
        lines.append("")
    return "\n".join(lines)
