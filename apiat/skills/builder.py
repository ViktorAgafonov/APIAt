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

ВЫБОР ПРОФИЛЯ (ОБЯЗАТЕЛЬНО):
- profile=network — если задача требует HTTP/HTTPS запросов (RSS, API, парсинг сайтов, скачивание)
- profile=isolated — если задача НЕ требует сети (вычисления, обработка данных, форматирование)
- profile=storage — если задача требует чтения/записи файлов на диск хоста (архивы, работа с data_dir)

ДОСТУПНЫЕ РЕСУРСЫ И API:
- Google News RSS: https://news.google.com/rss/search?q={query}&hl=ru&gl=RU&ceid=RU:ru (русские новости)
- Google News RSS (мир): https://news.google.com/rss/search?q={query}&hl=en&gl=US&ceid=US:en (мировые новости)
- data_dir структура: /opt/apiat/data/ — tmp/, downloads/done/, downloads/failed/, youtube/, browser/, attachments/, skills/, logs/
- Для profile=storage монтируется /data (read-write) — можно читать и писать файлы
- Для передачи данных между навыками в цепочке используется общий work_dir (profile=storage)

ПРАВИЛА:
- Не добавляй код, не относящийся к задаче (psutil, системная информация и т.п.)
- Если задача требует сеть — обязательно указывай # skill:profile=network и # skill:timeout=60
- Фокусируйся на ОДНОЙ ключевой функции — не пытайся решить многошаговую задачу в одном файле
- Код должен делать ТОЛЬКО то, что описано в задании — ничего лишнего
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

Проверь:
- Вывод содержит ТОЛЬКО результат, относящийся к заданию
- Нет нерелевантной информации (системные данные, psutil, отладка и т.п.)
- Нет дубликатов или пустых/бессмысленных строк
- Формат вывода соответствует заданию

Дай ответ строго "да" или "нет". Если "нет" — кратко укажи причину.
"""

# Промт оценки сложности — решает: один навык или цепочка
_COMPLEXITY_PROMPT = """\
Проанализируй задание и определи его сложность.

ЗАДАНИЕ:
{user_prompt}

ВОЗМОЖНОСТИ СИСТЕМЫ:
- Навык — это один Python-файл, выполняющий ОДНУ функцию
- Цепочка — последовательность навыков, где вывод каждого передаётся следующему
- Профили: network (HTTP/RSS), isolated (вычисления), storage (файлы на диске)
- Доступные модули: stdlib + requests + selectolax
- Google News RSS для новостей
- data_dir для хранения файлов между запусками

КРИТЕРИИ СЛОЖНОСТИ:
- simple (один навык) — задача решается одним HTTP-запросом или одной функцией
- complex (цепочка) — задача требует нескольких шагов с разными профилями
  (например: сбор данных → обработка → анализ → отправка)

Если complex — предложи разбиение на отдельные навыки.

Ответь в формате:
COMPLEXITY: simple|complex

Если complex, добавь строки навыков:
SKILL: <короткое имя> | <профиль> | <описание одной строкой>
SKILL: <короткое имя> | <профиль> | <описание одной строкой>
...

Пример complex-ответа:
COMPLEXITY: complex
SKILL: fetch_rss_ru | network | Сбор TOP-20 новостей России через Google News RSS
SKILL: fetch_rss_world | network | Сбор TOP-20 мировых новостей через Google News RSS
SKILL: translate_ru | isolated | Перевод текста новостей на русский язык
SKILL: weekly_aggregate | storage | Агрегация новостных файлов за неделю из data_dir
SKILL: analyze_digest | isolated | Глубокий анализ дайджеста: связи, последствия, значимость
SKILL: send_whitelist | isolated | Отправка сводки на адреса из белого списка
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


@dataclass
class ComplexityResult:
    """Результат оценки сложности задания."""
    is_complex: bool
    sub_skills: list[dict] = field(default_factory=list)  # [{"name": ..., "profile": ..., "description": ...}]
    raw: str = ""


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
        """Полный цикл построения навыка: оценка → генерация → ревью → sandbox → валидация → сохранение."""
        result = SkillBuildResult(success=False)
        using_fallback = self._router.is_using_fallback()

        # Шаг 0: оценка сложности
        result.steps.append("0. Оценка сложности...")
        logger.info("Skill build: оценка сложности для задания: %s", user_prompt[:100])
        complexity = await self.assess_complexity(user_prompt)
        if complexity.is_complex:
            result.steps.append(f"   Сложность: complex — предложено {len(complexity.sub_skills)} навыков")
            logger.info("Skill build: complex — %d навыков", len(complexity.sub_skills))
            skills_list = "\n".join(
                f"   • {s['name']} ({s['profile']}) — {s['description']}"
                for s in complexity.sub_skills
            )
            result.error = (
                f"ЗАДАЧА СЛОЖНАЯ — требуется цепочка из {len(complexity.sub_skills)} навыков.\n\n"
                f"Предложенное разбиение:\n{skills_list}\n\n"
                f"Для создания каждого навыка отправьте отдельные письма:\n"
            )
            for s in complexity.sub_skills:
                result.error += f"  создай навык: {s['description']} (профиль {s['profile']})\n"
            result.error += (
                f"\nПосле создания и закрепления всех навыков, отправьте:\n"
                f"  цепочка: <описание задачи>\n"
                f"для автоматического построения и выполнения цепочки."
            )
            result.steps.append("   Ожидание создания отдельных навыков оператором")
            return result
        result.steps.append("   Сложность: simple — один навык")
        logger.info("Skill build: simple — один навык")

        # Шаг 1: генерация кода
        result.steps.append("1. Генерация кода...")
        logger.info("Skill build: генерация кода для задания: %s", user_prompt[:100])
        code = await self._generate_code(user_prompt)
        if not code:
            result.error = "LLM не вернул код"
            logger.warning("Skill build: LLM не вернул код")
            return result
        cfg = SkillConfig.from_code(code)
        result.steps.append(f"   Сгенерировано {len(code)} символов, профиль: {cfg.profile}")
        logger.info("Skill build: код сгенерирован (%d символов, профиль=%s)", len(code), cfg.profile)

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
            logger.info("Skill build: ревью одобрено")
        else:
            result.steps.append("2. Ревью пропущено (работа через fallback LLM)")
            result.review_verdict = "пропущено (fallback)"
            logger.info("Skill build: ревью пропущено (fallback)")

        # Шаг 3: sandbox-тест
        result.steps.append(f"3. Запуск в Docker sandbox (profile={cfg.profile})...")
        sandbox_res: SandboxResult = self._sandbox.run(code, cfg)
        result.sandbox_output = sandbox_res.output
        if not sandbox_res.success:
            result.steps.append(f"   Sandbox: ошибка (exit {sandbox_res.exit_code})")
            result.error = f"Sandbox ошибка: {sandbox_res.stderr[:500]}"
            logger.warning("Skill build: sandbox ошибка (exit %d): %s", sandbox_res.exit_code, sandbox_res.stderr[:200])
            return result
        result.steps.append(f"   Sandbox: OK, вывод {len(sandbox_res.stdout)} символов")
        logger.info("Skill build: sandbox OK, вывод %d символов: %s", len(sandbox_res.stdout), sandbox_res.stdout[:150])

        # Шаг 4: валидация вывода той же LLM что генерировала
        result.steps.append("4. Валидация вывода LLM...")
        validate = await self._validate_output(user_prompt, sandbox_res.stdout)
        result.validate_verdict = validate
        verdict_lower = validate.lower().strip()
        if verdict_lower.startswith("нет") or verdict_lower.startswith("no"):
            result.steps.append(f"   Валидация: не соответствует — {validate}")
            result.error = f"Вывод не соответствует заданию: {validate}"
            logger.warning("Skill build: валидация отклонила: %s", validate[:200])
            return result
        result.steps.append("   Валидация: соответствует заданию")
        logger.info("Skill build: валидация прошла успешно")

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

    def list_skills(self) -> list[str]:
        """Алиас для list_confirmed — используется в команде помощи."""
        return self.list_confirmed()

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

    async def assess_complexity(self, user_prompt: str) -> ComplexityResult:
        """Оценивает сложность задания: один навык или цепочка."""
        prompt = _COMPLEXITY_PROMPT.format(user_prompt=user_prompt)
        try:
            raw = await self._router.complete(prompt)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Skill build: оценка сложности не удалась: %s", exc)
            return ComplexityResult(is_complex=False, raw=str(exc))
        return _parse_complexity(raw)

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


def _parse_complexity(raw: str) -> ComplexityResult:
    """Парсит ответ LLM оценки сложности."""
    raw = raw.strip()
    is_complex = "complex" in raw.lower().split("\n")[0]
    sub_skills: list[dict] = []
    for m in re.finditer(r"SKILL:\s*(\S+)\s*\|\s*(\w+)\s*\|\s*(.+)", raw):
        sub_skills.append({
            "name": m.group(1).strip(),
            "profile": m.group(2).strip(),
            "description": m.group(3).strip(),
        })
    return ComplexityResult(is_complex=is_complex, sub_skills=sub_skills, raw=raw)
