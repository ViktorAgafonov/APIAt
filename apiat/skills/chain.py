"""Цепочки навыков: динамическое построение LLM + сохранение как .chain.json.

Поток:
  1. LLM получает задачу + список доступных навыков → строит план (список шагов)
  2. ChainRunner выполняет навыки последовательно через DockerSandbox
     - общая рабочая директория /data (tmp/<run_id>) монтируется во все шаги
     - stdout каждого шага передаётся в context следующего
  3. Оператор подтверждает → цепочка сохраняется как .chain.json
  4. Следующий вызов: "выполни цепочку <name>: param=value"
     → ChainRunner загружает .chain.json и запускает без LLM
"""

from __future__ import annotations

import json
import re
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..intent.router import LlmRouter
from ..utils.logging import get_logger
from .sandbox import DockerSandbox, SkillConfig, SandboxResult

logger = get_logger(__name__)


# ── Модели данных ────────────────────────────────────────────────────────────

@dataclass
class ChainStep:
    skill: str              # имя файла навыка без .py
    params: dict[str, str] = field(default_factory=dict)  # {key: value или {input.key}}
    description: str = ""


@dataclass
class SkillChain:
    name: str
    description: str
    steps: list[ChainStep] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "steps": [
                {"skill": s.skill, "params": s.params, "description": s.description}
                for s in self.steps
            ],
        }

    @classmethod
    def from_dict(cls, d: dict) -> "SkillChain":
        return cls(
            name=d["name"],
            description=d.get("description", ""),
            steps=[
                ChainStep(
                    skill=s["skill"],
                    params=s.get("params", {}),
                    description=s.get("description", ""),
                )
                for s in d.get("steps", [])
            ],
        )

    @classmethod
    def from_file(cls, path: Path) -> "SkillChain":
        return cls.from_dict(json.loads(path.read_text(encoding="utf-8")))

    def save(self, chains_dir: Path) -> Path:
        path = chains_dir / f"{self.name}.chain.json"
        path.write_text(json.dumps(self.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
        return path


@dataclass
class ChainStepResult:
    step: ChainStep
    success: bool
    output: str
    error: str = ""


@dataclass
class ChainRunResult:
    success: bool
    chain_name: str
    steps: list[ChainStepResult] = field(default_factory=list)
    error: str = ""

    def report(self) -> str:
        lines = [f"Цепочка: {self.chain_name}"]
        for i, sr in enumerate(self.steps, 1):
            status = "✓" if sr.success else "✗"
            lines.append(f"{status} Шаг {i}: {sr.step.skill}")
            if sr.output:
                lines.append(f"   {sr.output[:300]}")
            if sr.error:
                lines.append(f"   Ошибка: {sr.error}")
        if not self.success:
            lines.append(f"\nЦепочка остановлена: {self.error}")
        return "\n".join(lines)


# ── Промты LLM ───────────────────────────────────────────────────────────────

_PLAN_PROMPT = """\
Ты планировщик задач. У тебя есть список доступных навыков и задача пользователя.
Построй план выполнения задачи: список шагов, каждый шаг — один навык.

ЗАДАЧА:
{task}

ДОСТУПНЫЕ НАВЫКИ:
{skills_list}

ВХОДНЫЕ ПАРАМЕТРЫ (из письма пользователя):
{input_params}

Верни ТОЛЬКО JSON массив шагов, без пояснений:
[
  {{"skill": "имя_навыка", "params": {{"key": "value"}}, "description": "что делает этот шаг"}},
  ...
]

Правила:
- Используй ТОЛЬКО навыки из списка выше. Если нужного навыка нет — пропусти шаг.
- В params можно ссылаться на входные параметры как {{input.key}}.
- Если навык storage-профиля — он автоматически получит /data с общими файлами.
- Порядок важен: каждый шаг работает с результатами предыдущего через /data.
"""


# ── ChainRunner ──────────────────────────────────────────────────────────────

class ChainRunner:
    """Выполняет цепочку навыков с общей рабочей директорией."""

    def __init__(self, skills_dir: Path, data_dir: Path) -> None:
        self._skills_dir = skills_dir
        self._chains_dir = skills_dir / "chains"
        self._chains_dir.mkdir(parents=True, exist_ok=True)
        self._data_dir = data_dir
        self._sandbox = DockerSandbox(data_dir=data_dir)

    def run(
        self,
        chain: SkillChain,
        input_params: dict[str, str] | None = None,
        max_steps: int = 10,
    ) -> ChainRunResult:
        """Выполняет все шаги цепочки последовательно.

        Превышение max_steps — аварийная остановка (защита от зацикливания).
        """
        input_params = input_params or {}
        run_id = uuid.uuid4().hex[:8]
        work_dir = self._data_dir / "tmp" / f"chain_{run_id}"
        work_dir.mkdir(parents=True, exist_ok=True)

        result = ChainRunResult(success=True, chain_name=chain.name)
        context: dict[str, str] = {}  # накапливаемый контекст между шагами

        try:
            for i, step in enumerate(chain.steps):
                if i >= max_steps:
                    result.success = False
                    result.error = f"Превышен лимит шагов ({max_steps}). Цепочка остановлена."
                    logger.warning("Chain '%s' aborted: max_steps=%d exceeded", chain.name, max_steps)
                    break
                step_result = self._run_step(step, input_params, context, work_dir)
                result.steps.append(step_result)
                if not step_result.success:
                    result.success = False
                    result.error = f"Шаг '{step.skill}' завершился с ошибкой"
                    break
                # stdout шага доступен следующему через context
                context[f"{step.skill}.output"] = step_result.output
        finally:
            shutil.rmtree(work_dir, ignore_errors=True)

        return result

    def _run_step(
        self,
        step: ChainStep,
        input_params: dict[str, str],
        context: dict[str, str],
        work_dir: Path,
    ) -> ChainStepResult:
        skill_file = self._skills_dir / f"{step.skill}.py"
        if not skill_file.exists():
            return ChainStepResult(
                step=step, success=False, output="",
                error=f"Навык '{step.skill}' не найден в {self._skills_dir}",
            )

        code = skill_file.read_text(encoding="utf-8")

        # Подставляем параметры: {input.key} и {prev_skill.output}
        resolved = _resolve_params(step.params, input_params, context)
        if resolved:
            # Инжектируем параметры как переменные окружения через prepend
            env_lines = "\n".join(
                f'{k.upper()} = {json.dumps(v)}' for k, v in resolved.items()
            )
            code = f"# --- chain params ---\n{env_lines}\n# ---\n{code}"

        cfg = SkillConfig.from_code(code)
        # Все шаги цепочки получают общую рабочую директорию
        if cfg.profile == "storage" or not cfg.storage_mount:
            cfg.profile = "storage"
            cfg.storage_mount = str(work_dir)

        sandbox_res = self._sandbox.run(code, cfg)
        return ChainStepResult(
            step=step,
            success=sandbox_res.success,
            output=sandbox_res.stdout,
            error=sandbox_res.stderr if not sandbox_res.success else "",
        )

    def load_chain(self, name: str) -> SkillChain | None:
        """Загружает .chain.json по имени."""
        path = self._chains_dir / f"{name}.chain.json"
        if not path.exists():
            # Частичное совпадение
            matches = list(self._chains_dir.glob(f"*{name}*.chain.json"))
            if not matches:
                return None
            path = matches[0]
        return SkillChain.from_file(path)

    def save_chain(self, chain: SkillChain) -> Path:
        return chain.save(self._chains_dir)

    def list_chains(self) -> list[str]:
        return sorted(p.stem.replace(".chain", "") for p in self._chains_dir.glob("*.chain.json"))


# ── ChainPlanner (LLM) ───────────────────────────────────────────────────────

class ChainPlanner:
    """Строит план цепочки через LLM на основе задачи и доступных навыков."""

    def __init__(self, router: LlmRouter, skills_dir: Path) -> None:
        self._router = router
        self._skills_dir = skills_dir

    async def plan(
        self,
        task: str,
        input_params: dict[str, str] | None = None,
    ) -> SkillChain | None:
        """Возвращает SkillChain или None если LLM не смог построить план."""
        skills_list = self._describe_skills()
        if not skills_list:
            return None

        prompt = _PLAN_PROMPT.format(
            task=task,
            skills_list=skills_list,
            input_params=json.dumps(input_params or {}, ensure_ascii=False),
        )
        raw = await self._router.complete(prompt)
        return _parse_chain_from_llm(raw, task)

    def _describe_skills(self) -> str:
        lines = []
        for path in sorted(self._skills_dir.glob("*.py")):
            code = path.read_text(encoding="utf-8", errors="ignore")
            cfg = SkillConfig.from_code(code)
            # Берём первый комментарий-описание из кода
            desc = _first_docline(code)
            lines.append(f"- {path.stem} (profile={cfg.profile}): {desc}")
        return "\n".join(lines) if lines else "нет доступных навыков"


# ── Вспомогательные функции ──────────────────────────────────────────────────

def _resolve_params(
    params: dict[str, str],
    input_params: dict[str, str],
    context: dict[str, str],
) -> dict[str, str]:
    resolved = {}
    for k, v in params.items():
        m = re.match(r"\{input\.(\w+)\}", v)
        if m:
            resolved[k] = input_params.get(m.group(1), v)
            continue
        m = re.match(r"\{(\w+)\.output\}", v)
        if m:
            resolved[k] = context.get(f"{m.group(1)}.output", v)
            continue
        resolved[k] = v
    return resolved


def _parse_chain_from_llm(raw: str, task: str) -> SkillChain | None:
    """Извлекает JSON-массив шагов из ответа LLM."""
    raw = raw.strip()
    # Убираем markdown-блоки
    m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", raw, re.DOTALL)
    if m:
        raw = m.group(1)
    else:
        m = re.search(r"(\[.*\])", raw, re.DOTALL)
        if m:
            raw = m.group(1)

    try:
        steps_data = json.loads(raw)
        steps = [
            ChainStep(
                skill=s["skill"],
                params=s.get("params", {}),
                description=s.get("description", ""),
            )
            for s in steps_data
            if "skill" in s
        ]
        name = re.sub(r"[^\w]", "_", task.lower())[:40].strip("_")
        return SkillChain(name=name, description=task, steps=steps)
    except (json.JSONDecodeError, KeyError, TypeError):
        return None


def _first_docline(code: str) -> str:
    """Берёт первую значимую строку комментария или кода как описание."""
    for line in code.splitlines():
        line = line.strip()
        if line.startswith("# skill:"):
            continue
        if line.startswith("#") and len(line) > 2:
            return line[1:].strip()
        if line.startswith('"""') or line.startswith("'''"):
            return line.strip('"\'').strip()
    return ""
