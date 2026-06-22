"""Email Tool: отправка ответов с вложениями через SMTP."""

from __future__ import annotations

import smtplib
from email.message import EmailMessage
from pathlib import Path

from ..config import Settings
from ..models.email import OutgoingMail
from ..utils.logging import get_logger

logger = get_logger(__name__)


_BODY_LIMIT = 50_000  # символов — если больше, бьём на части


class EmailSender:
    """Отправляет письма через SMTP. Поддерживает вложения."""

    def __init__(self, settings: Settings) -> None:
        self._s = settings

    def send(self, mail: OutgoingMail) -> None:
        """Отправляет письмо. Если тело > _BODY_LIMIT символов — разбивает на части."""
        if len(mail.body) <= _BODY_LIMIT:
            self._send_single(mail)
            return
        parts = [mail.body[i:i + _BODY_LIMIT] for i in range(0, len(mail.body), _BODY_LIMIT)]
        total = len(parts)
        for idx, chunk in enumerate(parts, 1):
            part_mail = mail.model_copy(update={
                "subject": f"{mail.subject} [{idx}/{total}]",
                "body": chunk,
                "attachments": mail.attachments if idx == total else [],
            })
            self._send_single(part_mail)
        logger.info("Большой ответ разбит на %d письма", total)

    def _send_single(self, mail: OutgoingMail) -> None:
        """Отправляет одно письмо."""
        msg = EmailMessage()
        msg["From"] = self._s.smtp_from or self._s.smtp_user
        msg["To"] = mail.to
        msg["Subject"] = mail.subject
        if mail.in_reply_to:
            msg["In-Reply-To"] = mail.in_reply_to
        msg.set_content(mail.body)

        for att in mail.attachments:
            if not att.path:
                continue
            data = Path(att.path).read_bytes()
            maintype, _, subtype = att.content_type.partition("/")
            msg.add_attachment(
                data,
                maintype=maintype or "application",
                subtype=subtype or "octet-stream",
                filename=att.filename,
            )

        self._transport(msg)
        logger.info("Письмо отправлено на %s (вложений: %d)", mail.to, len(mail.attachments))

    def _transport(self, msg: EmailMessage) -> None:
        if self._s.smtp_use_ssl:
            with smtplib.SMTP_SSL(self._s.smtp_host, self._s.smtp_port) as server:
                server.login(self._s.smtp_user, self._s.smtp_password)
                server.send_message(msg)
        else:
            with smtplib.SMTP(self._s.smtp_host, self._s.smtp_port) as server:
                server.starttls()
                server.login(self._s.smtp_user, self._s.smtp_password)
                server.send_message(msg)
