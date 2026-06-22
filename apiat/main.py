"""Оркестрация конвейера: gateway -> parser -> planner -> workflow -> email."""

from __future__ import annotations

import re
import subprocess
import time

from .config import Settings, get_settings
from .gateway.imap_client import ImapClient
from .gateway.security import is_authorized
from .intent.parser import IntentParser
from .intent.router import LlmAllProvidersFailedError
from .intent.self_corrector import SelfCorrector, try_apply_and_verify
from .models.base import TaskStatus
from .models.email import IncomingMail, OutgoingMail
from .skills.builder import SkillBuilder
from .storage.repositories import Storage
from .tools.email_tool import EmailSender
from .tools.registry import ToolRegistry
from .utils.logging import get_logger
from .workflow.engine import WorkflowEngine

logger = get_logger(__name__)

# Ключевые слова для команды переключения/настройки LLM от оператора
_LLM_CMD_RE = re.compile(
    r"(переключи\s*llm|смени\s*нейромодель|switch\s*llm|llm\s*config)",
    re.IGNORECASE,
)

# Команда самообновления кода с GitHub
_UPDATE_CMD_RE = re.compile(
    r"(обнови\s*код|update\s*code|git\s*pull)",
    re.IGNORECASE,
)

# Команда самообучения: "самообучись: <описание>"
_LEARN_CMD_RE = re.compile(
    r"^(самообучись|learn)\s*[:\-]?\s*(.+)",
    re.IGNORECASE | re.DOTALL,
)

# Подтверждение навыка: "подтверди навык <имя>"
_CONFIRM_SKILL_RE = re.compile(
    r"(подтверди\s*навык|confirm\s*skill)\s*[:\-]?\s*(\S+)",
    re.IGNORECASE,
)


class Agent:
    """Сборка компонентов APIAt и обработка входящих писем."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.settings.ensure_dirs()
        self.storage = Storage(self.settings.db_path)
        self.imap = ImapClient(self.settings)
        self.parser = IntentParser(self.settings)
        self.registry = ToolRegistry(self.settings.data_dir)
        self.engine = WorkflowEngine(self.registry, self.settings.db_path)
        self.sender = EmailSender(self.settings)
        self.skill_builder = SkillBuilder(self.settings, self.settings.data_dir / "skills")

    async def process_mail(self, mail: IncomingMail) -> None:
        """Полный цикл обработки одного письма."""
        # Защита от повторной обработки
        if self.storage.is_mail_processed(mail.message_id):
            logger.info("Письмо %s уже обработано, пропуск", mail.message_id)
            return

        # Безопасность: whitelist + секретный токен
        if not is_authorized(mail, self.settings.whitelist, self.settings.secret_token):
            logger.warning("Письмо от %s отклонено (нет доступа/токена)", mail.sender)
            self.storage.mark_mail_processed(mail.message_id, mail.sender)
            return

        self.storage.mark_mail_processed(mail.message_id, mail.sender)

        # Команда оператора: обновить код с GitHub
        if _UPDATE_CMD_RE.search(mail.body):
            await self._handle_update_command(mail)
            return

        # Команда оператора: самообучение
        learn_match = _LEARN_CMD_RE.search(mail.body)
        if learn_match:
            await self._handle_learn_command(mail, learn_match.group(2).strip())
            return

        # Команда оператора: подтвердить навык
        confirm_match = _CONFIRM_SKILL_RE.search(mail.body)
        if confirm_match:
            await self._handle_confirm_skill(mail, confirm_match.group(2).strip())
            return

        # Команда оператора: изменить настройки LLM
        if _LLM_CMD_RE.search(mail.body):
            await self._handle_llm_config_command(mail)
            return

        started = time.monotonic()

        try:
            task = await self.parser.parse(mail)
        except LlmAllProvidersFailedError as exc:
            logger.error("Все LLM-провайдеры недоступны: %s", exc)
            self._reply_error(
                mail,
                f"Все LLM-провайдеры недоступны:\n{exc}\n\n"
                f"Отправьте команду 'переключи LLM' с новыми параметрами.",
            )
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("Ошибка парсинга интента")
            self._reply_error(mail, f"Не удалось разобрать задачу: {exc}")
            return

        self.storage.save_task(
            task.task_id, task.type.value, TaskStatus.PARSED.value, task.model_dump(mode="json")
        )
        self.storage.update_status(task.task_id, TaskStatus.PARSED)

        try:
            state = await self.engine.run(task)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Ошибка workflow")
            self.storage.update_status(task.task_id, TaskStatus.FAILED, str(exc))
            self._reply_error(mail, f"Ошибка выполнения: {exc}", task_name=task.type.value)
            return

        status = state.get("status", TaskStatus.FAILED.value)
        result = state.get("result", {})
        elapsed = time.monotonic() - started

        self.storage.update_status(task.task_id, status)
        self.storage.save_result(
            task.task_id,
            summary=result.get("summary", ""),
            data=result.get("data", {}),
            metrics={"execution_time": round(elapsed, 2)},
        )
        self._reply_result(mail, task.type.value, status, result, elapsed)

    async def _handle_update_command(self, mail: IncomingMail) -> None:
        """git pull + pip install + systemctl restart apiat.

        Выполняется только если письмо авторизовано (whitelist + токен).
        Перед обновлением делает snapshot текущего HEAD для отката.
        """
        from pathlib import Path

        project_dir = Path(__file__).parent.parent
        lines: list[str] = []

        def _run(cmd: list[str], cwd=project_dir) -> tuple[int, str]:
            r = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=120)
            return r.returncode, (r.stdout + r.stderr).strip()

        # Сохраняем текущий HEAD для возможного отката
        rc, old_head = _run(["git", "rev-parse", "HEAD"])
        old_head = old_head.strip()
        lines.append(f"Текущий HEAD: {old_head[:12]}")

        # git pull
        rc, out = _run(["git", "pull", "origin", "main"])
        lines.append(f"git pull: {'OK' if rc == 0 else 'ОШИБКА'}\n{out}")
        if rc != 0:
            self._safe_send(OutgoingMail(
                to=mail.sender, subject="APIAt: обновление FAILED",
                body="\n".join(lines), in_reply_to=mail.message_id,
            ))
            return

        rc, new_head = _run(["git", "rev-parse", "HEAD"])
        new_head = new_head.strip()
        if old_head == new_head:
            lines.append("Код уже актуален, перезапуск не требуется.")
            self._safe_send(OutgoingMail(
                to=mail.sender, subject="APIAt: уже актуален",
                body="\n".join(lines), in_reply_to=mail.message_id,
            ))
            return
        lines.append(f"Новый HEAD: {new_head[:12]}")

        # pip install (только если изменился requirements.txt)
        venv_pip = project_dir / ".venv" / "bin" / "pip"
        if not venv_pip.exists():
            venv_pip = project_dir / ".venv" / "Scripts" / "pip"
        rc, out = _run([str(venv_pip), "install", "-r", "requirements.txt", "-q"])
        lines.append(f"pip install: {'OK' if rc == 0 else 'ОШИБКА'}\n{out[:300]}")
        if rc != 0:
            # Откатываем git
            _run(["git", "checkout", old_head])
            lines.append(f"Откат к {old_head[:12]} выполнен.")
            self._safe_send(OutgoingMail(
                to=mail.sender, subject="APIAt: обновление FAILED (откат)",
                body="\n".join(lines), in_reply_to=mail.message_id,
            ))
            return

        # Перезапуск сервиса
        rc, out = _run(["systemctl", "restart", "apiat"])
        lines.append(f"systemctl restart: {'OK' if rc == 0 else 'ОШИБКА'}\n{out}")

        logger.info("Самообновление: %s -> %s", old_head[:12], new_head[:12])
        self._safe_send(OutgoingMail(
            to=mail.sender,
            subject=f"APIAt: обновлён до {new_head[:12]}",
            body="\n".join(lines),
            in_reply_to=mail.message_id,
        ))

    async def _handle_learn_command(self, mail: IncomingMail, user_prompt: str) -> None:
        """Запускает цикл самообучения: генерация → ревью → sandbox → валидация."""
        logger.info("Самообучение по запросу: %s", user_prompt[:80])
        result = await self.skill_builder.build(user_prompt)

        steps_text = "\n".join(result.steps)
        if not result.success:
            body = (
                f"Навык не получен: {result.error}\n\n"
                f"Шаги выполнения:\n{steps_text}"
            )
            self._safe_send(OutgoingMail(
                to=mail.sender,
                subject="APIAt: навык не получен",
                body=body,
                in_reply_to=mail.message_id,
            ))
            return

        body = (
            f"Навык готов к проверке: <b>{result.skill_name}</b>\n\n"
            f"Шаги выполнения:\n{steps_text}\n\n"
            f"--- Вывод навыка ---\n{result.sandbox_output}\n\n"
            f"Если результат вас устраивает, ответьте:\n"
            f"подтверди навык {result.skill_name}"
        )
        self._safe_send(OutgoingMail(
            to=mail.sender,
            subject=f"APIAt: навык '{result.skill_name}' ожидает подтверждения",
            body=body,
            in_reply_to=mail.message_id,
        ))

    async def _handle_confirm_skill(self, mail: IncomingMail, skill_name: str) -> None:
        """Перемещает навык из pending/ в skills/."""
        ok = self.skill_builder.confirm(skill_name)
        pending = self.skill_builder.list_pending()
        if ok:
            body = (
                f"Навык '{skill_name}' закреплён в data/skills/.\n"
                f"Ожидают подтверждения: {', '.join(pending) or 'нет'}"
            )
            subject = f"APIAt: навык '{skill_name}' закреплён"
        else:
            body = (
                f"Навык '{skill_name}' не найден в pending/.\n"
                f"Доступные навыки для подтверждения: {', '.join(pending) or 'нет'}"
            )
            subject = f"APIAt: навык '{skill_name}' не найден"
        self._safe_send(OutgoingMail(
            to=mail.sender,
            subject=subject,
            body=body,
            in_reply_to=mail.message_id,
        ))

    async def _handle_llm_config_command(self, mail: IncomingMail) -> None:
        """Разбирает команду оператора и применяет/откатывает изменения .env LLM.

        Формат команды (в теле письма):
            переключи llm
            LLM_BASE_URL=https://...
            LLM_API_KEY=...
            LLM_MODEL_NAME=...

        Без дополнительных ключей — показывает текущее состояние и diff с бэкапом.
        """
        corrector = SelfCorrector(self.settings.db_path.parent.parent / ".env")
        updates = _parse_env_updates(mail.body)

        if not updates:
            # Нет новых значений — статус провайдеров
            providers = self.settings.llm_providers()
            router = self.parser.router
            lines = ["Текущие LLM-провайдеры:"]
            for p in providers:
                status = "⚠ cooldown" if router._is_cooling_down(p.name) else "✓ доступен"
                lines.append(f"  {p.name} ({p.provider_type}): {p.model_name} — {status}")
            if corrector.has_backup():
                lines.append("\nОтличия от .env.bak:")
                lines.extend(corrector.diff())
            self._safe_send(OutgoingMail(
                to=mail.sender,
                subject="APIAt: LLM статус",
                body="\n".join(lines),
                in_reply_to=mail.message_id,
            ))
            return

        success, msg = await try_apply_and_verify(updates)
        if success:
            # Перезагружаем parser с новыми настройками
            get_settings.cache_clear()
            self.settings = get_settings()
            self.parser = IntentParser(self.settings)
        self._safe_send(OutgoingMail(
            to=mail.sender,
            subject=f"APIAt: LLM {'обновлён' if success else 'откат'}",
            body=msg,
            in_reply_to=mail.message_id,
        ))

    def _reply_result(
        self, mail: IncomingMail, task_name: str, status: str, result: dict, elapsed: float
    ) -> None:
        """Формирует и отправляет ответ с результатом."""
        from .models.email import Attachment

        attachments = [Attachment(**a) for a in result.get("attachments", [])]
        body = (
            f"Task: {task_name}\n"
            f"Status: {status}\n\n"
            f"{result.get('summary') or result.get('error', '')}\n\n"
            f"Execution Time: {elapsed:.1f} sec"
        )
        self._safe_send(
            OutgoingMail(
                to=mail.sender,
                subject=f"APIAt: {task_name} [{status}]",
                body=body,
                attachments=attachments,
                in_reply_to=mail.message_id,
            )
        )

    def _reply_error(self, mail: IncomingMail, message: str, task_name: str = "unknown") -> None:
        self._safe_send(
            OutgoingMail(
                to=mail.sender,
                subject=f"APIAt: {task_name} [FAILED]",
                body=f"Task: {task_name}\nStatus: FAILED\n\n{message}",
                in_reply_to=mail.message_id,
            )
        )

    def _safe_send(self, mail: OutgoingMail) -> None:
        try:
            self.sender.send(mail)
        except Exception:  # noqa: BLE001
            logger.exception("Не удалось отправить ответ на %s", mail.to)

    async def run_once(self) -> int:
        """Один проход: забрать письма и обработать. Возвращает число писем."""
        mails = self.imap.fetch_unseen()
        logger.info("Получено новых писем: %d", len(mails))
        for mail in mails:
            await self.process_mail(mail)
        return len(mails)


def _parse_env_updates(body: str) -> dict[str, str]:
    """Извлекает KEY=value из тела письма (только LLM-ключи)."""
    allowed = {"LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL_NAME",
               "LLM_FALLBACK_API_KEY", "LLM_FALLBACK_MODEL_NAME"}
    result: dict[str, str] = {}
    for line in body.splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            key, _, value = line.partition("=")
            key = key.strip()
            if key in allowed and value.strip():
                result[key] = value.strip()
    return result
