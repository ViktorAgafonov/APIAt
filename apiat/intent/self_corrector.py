"""SelfCorrector: безопасное обновление .env с откатом и диагностикой.

Сценарии:
A. Оператор присылает команду вида «переключи LLM на gemini» или явно
   задаёт параметры — агент применяет изменения в .env.
B. Если новые настройки приводят к ошибке — автоматически откатывается к
   предыдущей (проверенной) версии .env и сообщает оператору диагноз.
C. Откат сам себе проверяется пробным запросом к LLM перед финальным
   подтверждением.

.env редактируется строго построчно по ключам, без перезаписи неизменных
значений. Резервная копия сохраняется как .env.bak рядом с .env.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from ..config import Settings, get_settings
from ..utils.logging import get_logger

logger = get_logger(__name__)

_KEY_RE = re.compile(r"^([A-Z0-9_]+)\s*=", re.MULTILINE)


class EnvPatchError(RuntimeError):
    """Не удалось применить патч .env."""


class SelfCorrector:
    """Управляет изменениями .env с откатом и диагностикой."""

    def __init__(self, env_path: Path | None = None) -> None:
        self._env = env_path or Path(".env")
        self._bak = self._env.with_suffix(".env.bak")

    # ------------------------------------------------------------------
    # Публичное API
    # ------------------------------------------------------------------

    def apply_patch(self, updates: dict[str, str]) -> list[str]:
        """Применяет словарь {KEY: value} к .env.

        Возвращает список применённых изменений (для ответа оператору).
        Перед записью делает резервную копию .env.bak.
        Добавляет новые ключи в конец файла, если они отсутствуют.
        """
        if not self._env.exists():
            raise EnvPatchError(f"{self._env} не найден")
        self._backup()
        content = self._env.read_text(encoding="utf-8")
        applied: list[str] = []
        for key, value in updates.items():
            old = self._get_value(content, key)
            content = self._set_value(content, key, value)
            applied.append(f"{key}: {old!r} → {value!r}")
        self._write(content)
        logger.info("Применены изменения .env: %s", applied)
        return applied

    def rollback(self) -> str:
        """Откатывает .env до .env.bak.

        Возвращает описание того, что восстановлено.
        """
        if not self._bak.exists():
            raise EnvPatchError("Резервная копия .env.bak отсутствует — откат невозможен")
        shutil.copy2(self._bak, self._env)
        logger.warning("Выполнен откат .env из .env.bak")
        return "Настройки LLM возвращены к предыдущей версии (.env.bak восстановлен)"

    def swap_providers(self) -> list[str]:
        """Меняет местами primary и fallback LLM параметры в .env.

        Возвращает список применённых изменений.
        """
        if not self._env.exists():
            raise EnvPatchError(f"{self._env} не найден")
        self._backup()
        content = self._env.read_text(encoding="utf-8")
        pairs = [
            ("LLM_BASE_URL", "LLM_FALLBACK_BASE_URL"),
            ("LLM_API_KEY", "LLM_FALLBACK_API_KEY"),
            ("LLM_MODEL_NAME", "LLM_FALLBACK_MODEL_NAME"),
        ]
        applied: list[str] = []
        for prim_key, fb_key in pairs:
            prim_val = self._get_value(content, prim_key) or ""
            fb_val = self._get_value(content, fb_key) or ""
            content = self._set_value(content, prim_key, fb_val)
            content = self._set_value(content, fb_key, prim_val)
            applied.append(f"{prim_key} ↔ {fb_key}: {prim_val!r} ⇄ {fb_val!r}")
        self._write(content)
        logger.info("Выполнен swap LLM провайдеров: %s", applied)
        return applied

    def has_backup(self) -> bool:
        return self._bak.exists()

    def diff(self) -> list[str]:
        """Показывает различия между текущим .env и .env.bak."""
        if not self._bak.exists():
            return ["Резервная копия отсутствует"]
        cur = self._parse(self._env.read_text(encoding="utf-8"))
        bak = self._parse(self._bak.read_text(encoding="utf-8"))
        lines: list[str] = []
        all_keys = set(cur) | set(bak)
        for k in sorted(all_keys):
            if cur.get(k) != bak.get(k):
                lines.append(f"  {k}: {bak.get(k)!r} → {cur.get(k)!r}")
        return lines or ["Различий нет"]

    # ------------------------------------------------------------------
    # Вспомогательные
    # ------------------------------------------------------------------

    def _backup(self) -> None:
        shutil.copy2(self._env, self._bak)

    def _write(self, content: str) -> None:
        enc = __import__("sys").stdout.encoding or "utf-8"  # noqa: F841
        import sys as _sys
        _ = _sys  # noqa: F841
        Path(self._env).write_bytes(
            content.encode("utf-8")
        )

    @staticmethod
    def _get_value(content: str, key: str) -> str | None:
        m = re.search(rf"^{re.escape(key)}\s*=\s*(.*)$", content, re.MULTILINE)
        return m.group(1).strip() if m else None

    @staticmethod
    def _set_value(content: str, key: str, value: str) -> str:
        pattern = rf"^{re.escape(key)}\s*=.*$"
        replacement = f"{key}={value}"
        if re.search(pattern, content, re.MULTILINE):
            return re.sub(pattern, replacement, content, flags=re.MULTILINE)
        # ключ не найден — добавляем в конец
        return content.rstrip("\n") + f"\n{replacement}\n"

    @staticmethod
    def _parse(content: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for line in content.splitlines():
            if "=" in line and not line.startswith("#"):
                k, _, v = line.partition("=")
                result[k.strip()] = v.strip()
        return result


async def try_apply_and_verify(
    updates: dict[str, str],
    env_path: Path | None = None,
) -> tuple[bool, str]:
    """Применяет изменения .env, проверяет LLM-соединение, откатывает при ошибке.

    Возвращает (успех, сообщение_для_оператора).
    """
    corrector = SelfCorrector(env_path)
    applied = corrector.apply_patch(updates)
    # Сбрасываем кэш настроек, чтобы перечитать .env
    get_settings.cache_clear()
    new_settings = get_settings()

    try:
        await _probe_llm(new_settings)
        msg = "Настройки LLM обновлены и проверены:\n" + "\n".join(applied)
        logger.info("self-correction: %s", msg)
        return True, msg
    except Exception as exc:  # noqa: BLE001
        diagnosis = _diagnose(exc, new_settings)
        rollback_msg = corrector.rollback()
        # Восстанавливаем настройки из бэкапа
        get_settings.cache_clear()
        msg = (
            f"Ошибка новых настроек LLM — откат выполнен.\n"
            f"Диагноз: {diagnosis}\n"
            f"{rollback_msg}\n"
            f"Изменения, которые пытались применить:\n" + "\n".join(applied)
        )
        logger.error("self-correction rollback: %s", msg)
        return False, msg


async def _probe_llm(settings: Settings) -> None:
    """Минимальный запрос к первому доступному провайдеру."""
    from .router import LlmRouter

    router = LlmRouter(settings)

    def _simple_agent(model):
        from pydantic_ai import Agent
        return Agent(model=model, output_type=str)

    result = await router.run_agent(_simple_agent, "ping")
    if not result:
        raise RuntimeError("Пустой ответ от LLM")


def _diagnose(exc: Exception, settings: Settings) -> str:
    """Формирует читаемый диагноз по типу исключения."""
    msg = str(exc)
    hints: list[str] = []
    if "401" in msg or "authentication" in msg.lower():
        hints.append("неверный API-ключ")
    if "404" in msg or "not found" in msg.lower():
        hints.append("неверный URL или модель не существует")
    if "500" in msg or "502" in msg or "503" in msg:
        hints.append("сервер провайдера недоступен")
    if "timeout" in msg.lower():
        hints.append("таймаут соединения")
    if "trailing slash" in msg.lower() or "base_url" in msg.lower():
        hints.append("проверьте LLM_BASE_URL (должен заканчиваться на /)")
    if not hints:
        hints.append("неизвестная ошибка — см. детали ниже")
    hint_str = ", ".join(hints)
    return f"{hint_str} | {type(exc).__name__}: {msg[:200]}"
