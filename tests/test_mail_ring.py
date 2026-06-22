"""Тесты кольцевого лога писем и рекомендаций."""

import json

import pytest

from apiat.logs.mail_ring import MailRingLog, _mask_token
from apiat.models.email import IncomingMail


def _mail(body: str = "hello", subject: str = "subj", sender: str = "a@b.com") -> IncomingMail:
    return IncomingMail(message_id="mid-1", sender=sender, subject=subject, body=body)


def test_push_adds_entry(tmp_path):
    ring = MailRingLog(tmp_path / "logs")
    ring.push(_mail(), task_type="youtube", status="COMPLETED")
    assert len(ring.get_ring()) == 1
    entry = ring.get_ring()[0]
    assert entry["task_type"] == "youtube"
    assert entry["status"] == "COMPLETED"
    assert entry["sender"] == "a@b.com"


def test_ring_evicts_oldest_when_full(tmp_path):
    ring = MailRingLog(tmp_path / "logs", ring_size=3)
    for i in range(5):
        ring.push(_mail(subject=f"s{i}"), task_type="search", status="OK")
    entries = ring.get_ring()
    assert len(entries) == 3
    subjects = [e["subject"] for e in entries]
    assert "s4" in subjects
    assert "s0" not in subjects


def test_ring_persisted_to_json(tmp_path):
    log_dir = tmp_path / "logs"
    ring = MailRingLog(log_dir)
    ring.push(_mail())
    data = json.loads((log_dir / "mail_ring.json").read_text(encoding="utf-8"))
    assert len(data) == 1


def test_ring_file_deleted_on_init(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "mail_ring.json").write_text("[{old: data}]", encoding="utf-8")
    ring = MailRingLog(log_dir)
    assert not (log_dir / "mail_ring.json").exists()
    assert ring.get_ring() == []


def test_recommendations_persist(tmp_path):
    ring = MailRingLog(tmp_path / "logs")
    ring.save_recommendation("улучшить поиск")
    ring2 = MailRingLog(tmp_path / "logs")  # новый экземпляр = перезапуск
    recs = ring2.get_recommendations()
    assert len(recs) == 1
    assert recs[0]["text"] == "улучшить поиск"


def test_format_recommendations_empty(tmp_path):
    ring = MailRingLog(tmp_path / "logs")
    msg = ring.format_recommendations()
    assert "нет" in msg.lower()


def test_format_recommendations_shows_last_5(tmp_path):
    ring = MailRingLog(tmp_path / "logs")
    for i in range(7):
        ring.save_recommendation(f"рек {i}")
    text = ring.format_recommendations()
    assert "7" in text or "и ещё" in text


def test_mask_token_replaces_secret():
    result = _mask_token("привет mysecret123 пока", "mysecret123")
    assert "mysecret123" not in result
    assert "***" in result


def test_mask_token_empty_token():
    original = "hello token"
    assert _mask_token(original, "") == original


def test_push_masks_token_in_preview(tmp_path):
    ring = MailRingLog(tmp_path / "logs")
    ring.push(_mail(body="задача mysecret делай"), status="OK", secret_token="mysecret")
    entry = ring.get_ring()[0]
    assert "mysecret" not in entry["body_preview"]
    assert "***" in entry["body_preview"]
