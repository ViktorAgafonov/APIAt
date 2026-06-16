"""Минимальная проверка почты: IMAP-логин (без пометки прочитанным) + 1 SMTP-письмо."""

import imaplib
import sys

from apiat.config import get_settings
from apiat.models.email import OutgoingMail
from apiat.tools.email_tool import EmailSender


def check_imap(s) -> None:
    conn = imaplib.IMAP4_SSL(s.imap_host, s.imap_port)
    try:
        conn.login(s.imap_user, s.imap_password)
        # readonly=True, чтобы не менять флаги писем
        conn.select(s.imap_folder, readonly=True)
        status, data = conn.search(None, "UNSEEN")
        n = len(data[0].split()) if status == "OK" and data[0] else 0
        print(f"IMAP OK: вход выполнен, непрочитанных писем: {n}")
    finally:
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        conn.logout()


def send_test(s, to: str) -> None:
    EmailSender(s).send(
        OutgoingMail(
            to=to,
            subject="APIAt: проверка связи",
            body="Тестовое письмо от агента APIAt. Связь SMTP работает.",
        )
    )
    print(f"SMTP OK: тестовое письмо отправлено на {to}")


def main() -> None:
    s = get_settings()
    to = sys.argv[1] if len(sys.argv) > 1 else (s.whitelist[0] if s.whitelist else s.smtp_from)
    check_imap(s)
    send_test(s, to)


if __name__ == "__main__":
    main()
