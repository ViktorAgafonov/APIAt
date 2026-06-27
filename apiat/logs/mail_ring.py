"""Кольцевой лог последних 50 писем и LLM-анализ для рекомендаций."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..models.email import IncomingMail


def _mask_token(text: str, token: str) -> str:
    """Заменяет токен в тексте на ***."""
    if not token:
        return text
    import re
    return re.sub(re.escape(token), "***", text, flags=re.IGNORECASE)

_RING_SIZE = 50
_RING_FILE = "mail_ring.json"
_REC_FILE = "recommendations.json"


class MailRingLog:
    """Кольцевой буфер последних N писем + хранилище рекомендаций.

    mail_ring.json  — удаляется при инициализации (каждый старт сервиса).
    recommendations.json — сохраняется между перезапусками.
    """

    def __init__(self, log_dir: Path, ring_size: int = _RING_SIZE) -> None:
        self._dir = log_dir
        self._ring_size = ring_size
        self._ring_path = log_dir / _RING_FILE
        self._rec_path = log_dir / _REC_FILE
        self._dir.mkdir(parents=True, exist_ok=True)
        # Удаляем кольцевой лог при каждом старте
        self._ring_path.unlink(missing_ok=True)
        self._ring: list[dict] = []

    def push(
        self,
        mail: "IncomingMail",
        task_type: str | None = None,
        status: str | None = None,
        secret_token: str = "",
    ) -> None:
        """Добавляет запись о письме; вытесняет самое старое если >ring_size."""
        preview = _mask_token(mail.body[:300], secret_token)
        subject = _mask_token(mail.subject, secret_token)
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "sender": mail.sender,
            "subject": subject,
            "body_preview": preview,
            "task_type": task_type,
            "status": status,
            "has_attachments": len(mail.attachments) > 0,
        }
        self._ring.append(entry)
        if len(self._ring) > self._ring_size:
            self._ring = self._ring[-self._ring_size:]
        self._ring_path.write_text(
            json.dumps(self._ring, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get_ring(self) -> list[dict]:
        return list(self._ring)

    def save_recommendation(self, text: str, author: str = "llm") -> None:
        """Сохраняет рекомендации (не удаляются при перезапуске)."""
        recs: list[dict] = []
        if self._rec_path.exists():
            try:
                recs = json.loads(self._rec_path.read_text(encoding="utf-8"))
            except Exception:  # noqa: BLE001
                recs = []
        recs.append({
            "ts": datetime.now(timezone.utc).isoformat(),
            "author": author,
            "text": text,
        })
        self._rec_path.write_text(
            json.dumps(recs, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def get_recommendations(self) -> list[dict]:
        """Возвращает все сохранённые рекомендации."""
        if not self._rec_path.exists():
            return []
        try:
            return json.loads(self._rec_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return []

    def format_recommendations(self) -> str:
        """Форматирует рекомендации для отправки пользователю."""
        recs = self.get_recommendations()
        if not recs:
            return "Рекомендаций пока нет. Запросите анализ командой 'анализ писем'."
        lines = [f"Всего рекомендаций: {len(recs)}\n"]
        for i, r in enumerate(reversed(recs), 1):
            ts = r.get("ts", "")[:16].replace("T", " ")
            lines.append(f"── [{ts}] ──────────────────")
            lines.append(r.get("text", ""))
            if i >= 5:  # показываем последние 5
                break
        if len(recs) > 5:
            lines.append(f"\n... и ещё {len(recs) - 5} ранних рекомендаций.")
        return "\n".join(lines)
