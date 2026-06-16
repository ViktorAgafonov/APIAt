"""IMAP-клиент: выборка новых писем и сохранение вложений."""

from __future__ import annotations

import email
import imaplib
from email.header import decode_header, make_header
from email.message import Message
from pathlib import Path

from ..config import Settings
from ..models.email import Attachment, IncomingMail
from ..utils.logging import get_logger

logger = get_logger(__name__)


def _decode(value: str | None) -> str:
    if not value:
        return ""
    try:
        return str(make_header(decode_header(value)))
    except Exception:  # noqa: BLE001 - заголовок может быть битым
        return value


class ImapClient:
    """Тонкая обёртка над imaplib для получения непрочитанных писем."""

    def __init__(self, settings: Settings) -> None:
        self._s = settings

    def _connect(self) -> imaplib.IMAP4:
        if self._s.imap_use_ssl:
            conn: imaplib.IMAP4 = imaplib.IMAP4_SSL(self._s.imap_host, self._s.imap_port)
        else:
            conn = imaplib.IMAP4(self._s.imap_host, self._s.imap_port)
        conn.login(self._s.imap_user, self._s.imap_password)
        return conn

    def fetch_unseen(self) -> list[IncomingMail]:
        """Возвращает непрочитанные письма и помечает их как прочитанные."""
        conn = self._connect()
        mails: list[IncomingMail] = []
        try:
            conn.select(self._s.imap_folder)
            status, data = conn.search(None, "UNSEEN")
            if status != "OK":
                return mails
            for num in data[0].split():
                fetch_status, msg_data = conn.fetch(num, "(RFC822)")
                if fetch_status != "OK" or not msg_data or not msg_data[0]:
                    continue
                raw = msg_data[0][1]
                if not isinstance(raw, (bytes, bytearray)):
                    continue
                mails.append(self._parse(email.message_from_bytes(raw)))
        finally:
            try:
                conn.close()
            except Exception:  # noqa: BLE001
                pass
            conn.logout()
        return mails

    def _parse(self, msg: Message) -> IncomingMail:
        message_id = (msg.get("Message-ID") or "").strip()
        sender = _decode(msg.get("From"))
        subject = _decode(msg.get("Subject"))
        body, attachments = self._extract_parts(msg)
        return IncomingMail(
            message_id=message_id or f"no-id-{hash(subject + sender)}",
            sender=sender,
            subject=subject,
            body=body,
            attachments=attachments,
        )

    def _extract_parts(self, msg: Message) -> tuple[str, list[Attachment]]:
        body_parts: list[str] = []
        attachments: list[Attachment] = []
        attach_dir = Path(self._s.data_dir) / "attachments"

        for part in msg.walk():
            if part.is_multipart():
                continue
            disposition = (part.get("Content-Disposition") or "").lower()
            content_type = part.get_content_type()

            if "attachment" in disposition:
                filename = _decode(part.get_filename()) or "attachment.bin"
                payload = part.get_payload(decode=True) or b""
                attach_dir.mkdir(parents=True, exist_ok=True)
                file_path = attach_dir / filename
                file_path.write_bytes(payload)
                attachments.append(
                    Attachment(
                        filename=filename,
                        content_type=content_type,
                        path=str(file_path),
                        size=len(payload),
                    )
                )
            elif content_type == "text/plain":
                payload = part.get_payload(decode=True) or b""
                charset = part.get_content_charset() or "utf-8"
                body_parts.append(payload.decode(charset, errors="replace"))

        return "\n".join(body_parts).strip(), attachments
