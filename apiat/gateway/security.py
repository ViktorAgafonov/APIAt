"""Проверки безопасности входящих писем."""

from __future__ import annotations

from email.utils import parseaddr

from ..models.email import IncomingMail


def extract_address(raw: str) -> str:
    """Возвращает чистый email-адрес в нижнем регистре."""
    _, addr = parseaddr(raw)
    return addr.strip().lower()


def is_whitelisted(sender: str, whitelist: list[str]) -> bool:
    """Проверяет, что отправитель в whitelist."""
    return extract_address(sender) in {a.lower() for a in whitelist}


def has_secret_token(text: str, token: str) -> bool:
    """Проверяет наличие секретного токена в тексте (без учёта регистра)."""
    if not token:
        return False
    return token.lower() in (text or "").lower()


def is_authorized(mail: IncomingMail, whitelist: list[str], token: str) -> bool:
    """Полная проверка: whitelist + наличие токена в теме или теле письма."""
    if not is_whitelisted(mail.sender, whitelist):
        return False
    return has_secret_token(mail.subject, token) or has_secret_token(mail.body, token)
