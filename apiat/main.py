"""Оркестрация конвейера: gateway -> parser -> planner -> workflow -> email."""

from __future__ import annotations

import time

from .config import Settings, get_settings
from .gateway.imap_client import ImapClient
from .gateway.security import is_authorized
from .intent.parser import IntentParser
from .models.base import TaskStatus
from .models.email import IncomingMail, OutgoingMail
from .storage.repositories import Storage
from .tools.email_tool import EmailSender
from .tools.registry import ToolRegistry
from .utils.logging import get_logger
from .workflow.engine import WorkflowEngine

logger = get_logger(__name__)


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
        started = time.monotonic()

        try:
            task = await self.parser.parse(mail)
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
