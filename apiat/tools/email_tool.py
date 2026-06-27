"""Email Tool: отправка ответов с вложениями через SMTP."""

from __future__ import annotations

import smtplib
import uuid
from email.message import EmailMessage
from pathlib import Path

from ..config import Settings
from ..models.email import OutgoingMail
from ..utils.logging import get_logger

logger = get_logger(__name__)


def _msg_id(value: str) -> str:
    """Нормализует Message-ID: оборачивает в <> если ещё не обёрнут."""
    v = value.strip()
    if not v:
        return v
    if v.startswith("<") and v.endswith(">"):
        return v
    return f"<{v}>"


def estimate_email_count(body_len: int, body_limit: int = 50_000) -> int:
    """Оценивает количество писем для заданного размера тела."""
    if body_len <= 0:
        return 0
    return (body_len + body_limit - 1) // body_limit


class EmailSender:
    """Отправляет письма через SMTP. Поддерживает вложения."""

    def __init__(self, settings: Settings) -> None:
        self._s = settings

    def send(self, mail: OutgoingMail) -> None:
        """Отправляет письмо. Если тело > body_limit символов — разбивает на части."""
        limit = self._s.body_limit
        if len(mail.body) <= limit:
            self._send_single(mail)
            return
        parts = [mail.body[i:i + limit] for i in range(0, len(mail.body), limit)]
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

        # Генерируем Message-ID для исходящего письма (RFC 2822)
        if mail.message_id:
            msg["Message-ID"] = _msg_id(mail.message_id)
        else:
            domain = (self._s.smtp_from or self._s.smtp_user or "apiat.local").split("@")[-1]
            msg["Message-ID"] = f"<apiat-{uuid.uuid4().hex}@{domain}>"

        # Re: prefix чтобы клиент показывал как ответ в теме
        subject = mail.subject
        if mail.in_reply_to and not subject.lower().startswith("re:"):
            subject = f"Re: {subject}"
        msg["Subject"] = subject
        if mail.in_reply_to:
            msg["In-Reply-To"] = _msg_id(mail.in_reply_to)
            # References = предыдущие ID + текущий In-Reply-To — обязателен для треда (RFC 2822)
            prev_ids = [_msg_id(m) for m in mail.references.split() if m.strip()]
            refs = " ".join(prev_ids + [_msg_id(mail.in_reply_to)])
            msg["References"] = refs
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
        logger.info("Письмо отправлено на %s (вложений: %d) \"%s\"", mail.to, len(mail.attachments), subject)

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
