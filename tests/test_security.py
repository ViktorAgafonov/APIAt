"""Тесты проверок безопасности."""

from apiat.gateway.security import (
    extract_address,
    has_secret_token,
    is_authorized,
    is_whitelisted,
)
from apiat.models.email import IncomingMail


def test_extract_address():
    assert extract_address("Owner <Owner@X.com>") == "owner@x.com"


def test_whitelist():
    assert is_whitelisted("Owner <o@x.com>", ["o@x.com"])
    assert not is_whitelisted("bad@y.com", ["o@x.com"])


def test_secret_token():
    assert has_secret_token("привет черешня тут", "черешня")
    assert not has_secret_token("нет токена", "черешня")
    assert not has_secret_token("текст", "")


def test_is_authorized():
    mail = IncomingMail(
        message_id="m1", sender="o@x.com", subject="тема", body="код черешня"
    )
    assert is_authorized(mail, ["o@x.com"], "черешня")
    # нет токена
    assert not is_authorized(mail, ["o@x.com"], "малина")
    # не в whitelist
    assert not is_authorized(mail, ["other@x.com"], "черешня")
