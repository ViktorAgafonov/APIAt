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
from .sandbox import DockerSandbox, SkillConfig, SandboxResult

logger = get_logger(__name__)

# Базовый контекст среды — передаётся в промт генерации
_ENV_CONTEXT_BASE = """
Среда выполнения:
- Python 3.12, Linux (Ubuntu 24.04), VPS сервер
- Доступные модули: stdlib + psutil + requests + selectolax (все установлены)
- Результат должен быть напечатан через print() в stdout
- Ответ пользователю — plain text, удобно читаемый: разделы через пустую строку, выравнивание через пробелы, значения через :
- Код должен быть полностью самодостаточным, в одном файле, без внешних import кроме stdlib/psutil/requests/selectolax
- Первые строки кода — метаданные лимитов sandbox (# skill:key=value):
    # skill:profile=isolated|network|storage
    # skill:memory=128m      (лимит RAM)
    # skill:timeout=30       (секунд)
    # skill:tmpfs=32m        (размер /tmp)
    # skill:storage_mount=/opt/apiat/data/downloads/done  (для profile=storage)
Параметры sandbox для этого навыка:
{skill_config}
"""

_GENERATE_PROMPT = """\
Напиши Python-модуль (один файл, весь код) по следующему заданию:

ЗАДАНИЕ:
{user_prompt}

ТЕХНИЧЕСКИЕ ТРЕБОВАНИЯ:
{env_context}

Верни ТОЛЬКО код Python без пояснений и без markdown-блоков.
Первые строки файла — метаданные (# skill:...) если нужны нестандартные лимиты, затем сам код.
"""

_REVIEW_PROMPT = """\
Проверь код на КРИТИЧЕСКИЕ проблемы безопасности и работоспособности.

ИСХОДНОЕ ЗАДАНИЕ:
{user_prompt}

КОД РЕШЕНИЯ:
{code}

Отвечай "нет" ТОЛЬКО если код:
- содержит вредоносный код (удаление файлов, отправка данных, rm -rf и т.п.)
- импортирует запрещённые модули (socket, subprocess с внешними командами, ctypes)
- содержит синтаксическую ошибку из-за которой не запустится
- делает принципиально не то что просили

Замечания по стилю, отсутствие try-except, отсутствие комментариев — НЕ являются причиной для "нет".
Код запускается в изолированном Docker sandbox без сети и с ограничением ресурсов — это безопасно.

Дай ответ строго "да" или "нет". Если "нет" — одна строка причины.
"""

_NAME_PROMPT = """\
Придумай короткое английское имя файла Python-навыка в формате snake_case, 2-4 слова, максимум 30 символов.
Описание навыка: {user_prompt}
Верни ТОЛЬКО имя файла без расширения .py, без пояснений.
Примеры: server_status, disk_report, ram_usage, parse_url
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
        self._data_dir = settings.data_dir
        self._sandbox = DockerSandbox(data_dir=settings.data_dir)
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
        cfg = SkillConfig.from_code(code)
        result.steps.append(f"   Сгенерировано {len(code)} символов, профиль: {cfg.profile}")

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
        result.steps.append(f"3. Запуск в Docker sandbox (profile={cfg.profile})...")
        sandbox_res: SandboxResult = self._sandbox.run(code, cfg)
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
        skill_name = await self._generate_name(user_prompt)
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
        return sorted(p.stem for p in self._pending_dir.glob("*.py"))

    def list_confirmed(self) -> list[str]:
        return sorted(p.stem for p in self._skills_dir.glob("*.py"))

    def skills_report(self) -> str:
        """Текстовый отчёт о навыках для отправки оператору."""
        confirmed = self.list_confirmed()
        pending = self.list_pending()
        lines: list[str] = []

        if confirmed:
            lines.append(f"✓ Закреплённые навыки ({len(confirmed)}):")
            for name in confirmed:
                lines.append(f"  • {name}")
        else:
            lines.append("✓ Закреплённые навыки: нет")

        lines.append("")

        if pending:
            lines.append(f"⏳ Ожидают подтверждения ({len(pending)}):")
            for name in pending:
                lines.append(f"  • {name}")
                lines.append(f"    → чтобы закрепить: закрепи навык {name}")
        else:
            lines.append("⏳ Ожидают подтверждения: нет")

        lines.append("")
        lines.append("Чтобы создать новый навык:")
        lines.append("  самообучись: <описание навыка>")

        return "\n".join(lines)

    async def _generate_name(self, user_prompt: str) -> str:
        raw = await self._router.complete(_NAME_PROMPT.format(user_prompt=user_prompt))
        name = raw.strip().splitlines()[0].strip().replace(".py", "")
        name = re.sub(r"[^\w]", "_", name.lower()).strip("_")[:30]
        return name or _slug(user_prompt)

    async def _generate_code(self, user_prompt: str) -> str:
        default_cfg = SkillConfig()
        env_context = _ENV_CONTEXT_BASE.format(skill_config=default_cfg.describe())
        prompt = _GENERATE_PROMPT.format(
            user_prompt=user_prompt,
            env_context=env_context,
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
