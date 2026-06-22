"""SkillBuilder: генерация Python-навыка через LLM, ревью, sandbox-тест, сохранение."""

from __future__ import annotations

import re
import shutil
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from ..config import Settings
from ..intent.router import LlmRouter
from ..utils.logging import get_logger
from .sandbox import DockerSandbox, SandboxResult

logger = get_logger(__name__)

# Контекст среды — передаётся в промт генерации
_ENV_CONTEXT = """
Среда выполнения:
- Python 3.12, Linux (Ubuntu 24.04), VPS сервер
- Доступные stdlib-модули: os, sys, subprocess, pathlib, json, datetime, psutil (установлен)
- НЕТ доступа к сети внутри sandbox (--network none)
- НЕТ доступа к файловой системе хоста (только /tmp)
- RAM лимит: 128 MB, CPU: 50%, timeout: 30 сек
- Результат должен быть напечатан через print() в stdout
- Ответ пользователю должен быть в формате HTML (теги <h2>, <p>, <ul>, <li>, <b>)
- Код должен быть полностью самодостаточным, в одном файле, без внешних зависимостей кроме stdlib и psutil
"""

_GENERATE_PROMPT = """\
Напиши Python-модуль (один файл, весь код) по следующему заданию:

ЗАДАНИЕ:
{user_prompt}

ТЕХНИЧЕСКИЕ ТРЕБОВАНИЯ:
{env_context}

Верни ТОЛЬКО код Python без пояснений и без markdown-блоков. Первая строка — сам код.
"""

_REVIEW_PROMPT = """\
Оцени можно ли запускать в релиз или нет следующее решение.

ИСХОДНОЕ ЗАДАНИЕ:
{user_prompt}

КОД РЕШЕНИЯ:
{code}

Дай ответ строго "да" или "нет". Если "нет" — укажи что доработать (кратко).
"""

_VALIDATE_PROMPT = """\
Соответствует ли следующий вывод программы заданию пользователя?

ЗАДАНИЕ:
{user_prompt}

ВЫВОД ПРОГРАММЫ:
{output}

Дай ответ строго "да" или "нет". Если "нет" — кратко укажи причину.
"""


@dataclass
class SkillBuildResult:
    success: bool
    skill_name: str = ""
    pending_path: Path | None = None
    sandbox_output: str = ""
    review_verdict: str = ""
    validate_verdict: str = ""
    error: str = ""
    steps: list[str] = field(default_factory=list)


class SkillBuilder:
    """Полный цикл создания навыка: генерация → ревью → sandbox → валидация → сохранение."""

    def __init__(self, settings: Settings, skills_dir: Path) -> None:
        self._settings = settings
        self._router = LlmRouter(settings.llm_providers())
        self._sandbox = DockerSandbox()
        self._skills_dir = skills_dir
        self._pending_dir = skills_dir / "pending"
        self._pending_dir.mkdir(parents=True, exist_ok=True)
        skills_dir.mkdir(parents=True, exist_ok=True)

    async def build(self, user_prompt: str) -> SkillBuildResult:
        """Полный цикл построения навыка."""
        result = SkillBuildResult(success=False)
        using_fallback = self._router.is_using_fallback()

        # Шаг 1: генерация кода
        result.steps.append("1. Генерация кода...")
        code = await self._generate_code(user_prompt)
        if not code:
            result.error = "LLM не вернул код"
            return result
        result.steps.append(f"   Сгенерировано {len(code)} символов кода")

        # Шаг 2: ревью (только если работаем через primary LLM)
        if not using_fallback:
            result.steps.append("2. Ревью кода (primary LLM)...")
            review = await self._review_code(user_prompt, code)
            result.review_verdict = review
            verdict_lower = review.lower().strip()
            if verdict_lower.startswith("нет") or verdict_lower.startswith("no"):
                result.steps.append(f"   Ревью: отклонено — {review}")
                result.error = f"Ревью отклонило код: {review}"
                return result
            result.steps.append(f"   Ревью: одобрено")
        else:
            result.steps.append("2. Ревью пропущено (работа через fallback LLM)")
            result.review_verdict = "пропущено (fallback)"

        # Шаг 3: sandbox-тест
        result.steps.append("3. Запуск в Docker sandbox...")
        sandbox_res: SandboxResult = self._sandbox.run(code)
        result.sandbox_output = sandbox_res.output
        if not sandbox_res.success:
            result.steps.append(f"   Sandbox: ошибка (exit {sandbox_res.exit_code})")
            result.error = f"Sandbox ошибка: {sandbox_res.stderr[:500]}"
            return result
        result.steps.append(f"   Sandbox: OK, вывод {len(sandbox_res.stdout)} символов")

        # Шаг 4: валидация вывода той же LLM что генерировала
        result.steps.append("4. Валидация вывода LLM...")
        validate = await self._validate_output(user_prompt, sandbox_res.stdout)
        result.validate_verdict = validate
        verdict_lower = validate.lower().strip()
        if verdict_lower.startswith("нет") or verdict_lower.startswith("no"):
            result.steps.append(f"   Валидация: не соответствует — {validate}")
            result.error = f"Вывод не соответствует заданию: {validate}"
            return result
        result.steps.append("   Валидация: соответствует заданию")

        # Шаг 5: сохранение в pending
        skill_name = _slug(user_prompt)
        pending_path = self._pending_dir / f"{skill_name}.py"
        pending_path.write_text(code, encoding="utf-8")
        result.steps.append(f"5. Сохранено в pending: {pending_path.name}")

        result.success = True
        result.skill_name = skill_name
        result.pending_path = pending_path
        return result

    def confirm(self, skill_name: str) -> bool:
        """Перемещает навык из pending/ в skills/."""
        src = self._pending_dir / f"{skill_name}.py"
        if not src.exists():
            # Ищем по частичному совпадению
            matches = list(self._pending_dir.glob(f"*{skill_name}*.py"))
            if not matches:
                return False
            src = matches[0]
        dst = self._skills_dir / src.name
        shutil.move(str(src), str(dst))
        logger.info("Навык закреплён: %s", dst.name)
        return True

    def list_pending(self) -> list[str]:
        return [p.stem for p in self._pending_dir.glob("*.py")]

    async def _generate_code(self, user_prompt: str) -> str:
        prompt = _GENERATE_PROMPT.format(
            user_prompt=user_prompt,
            env_context=_ENV_CONTEXT,
        )
        raw = await self._router.complete(prompt)
        return _extract_code(raw)

    async def _review_code(self, user_prompt: str, code: str) -> str:
        prompt = _REVIEW_PROMPT.format(user_prompt=user_prompt, code=code)
        return await self._router.complete(prompt)

    async def _validate_output(self, user_prompt: str, output: str) -> str:
        prompt = _VALIDATE_PROMPT.format(user_prompt=user_prompt, output=output[:2000])
        return await self._router.complete(prompt)


def _extract_code(raw: str) -> str:
    """Извлекает Python-код из ответа LLM (убирает markdown-блоки)."""
    raw = raw.strip()
    block = re.search(r"```(?:python)?\n(.*?)```", raw, re.DOTALL)
    if block:
        return block.group(1).strip()
    return raw


def _slug(text: str, max_len: int = 40) -> str:
    """Создаёт имя файла из произвольного текста."""
    slug = re.sub(r"[^\w\s-]", "", text.lower())
    slug = re.sub(r"[\s_-]+", "_", slug).strip("_")[:max_len]
    return slug or uuid.uuid4().hex[:8]
