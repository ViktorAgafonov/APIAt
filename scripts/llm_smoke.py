"""Минимальный живой тест парсера интентов (1 запрос к LLM)."""

import asyncio

from apiat.config import get_settings
from apiat.intent.parser import IntentParser
from apiat.models.email import IncomingMail


async def main() -> None:
    settings = get_settings()
    parser = IntentParser(settings)
    mail = IncomingMail(
        message_id="smoke-1",
        sender="owner@example.com",
        subject="видео",
        body="Скачай это видео https://youtu.be/dQw4w9WgXcQ как mp3",
    )
    task = await parser.parse(mail)
    print("TYPE:", task.type.value)
    print("TASK:", task.model_dump(mode="json"))


if __name__ == "__main__":
    asyncio.run(main())
