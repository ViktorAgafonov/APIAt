"""Тесты типизированных моделей задач."""

from pydantic import TypeAdapter

from apiat.models.tasks import AnyTask, YoutubeFormat, YoutubeTask


def test_youtube_task_defaults():
    task = YoutubeTask(url="https://youtu.be/x")
    assert task.type.value == "youtube"
    assert task.format == YoutubeFormat.MP4
    assert task.task_id  # сгенерирован автоматически


def test_discriminated_union_parsing():
    adapter = TypeAdapter(AnyTask)
    task = adapter.validate_python({"type": "youtube", "url": "u", "format": "mp3"})
    assert isinstance(task, YoutubeTask)
    assert task.format == YoutubeFormat.MP3


def test_roundtrip_serialization():
    adapter = TypeAdapter(AnyTask)
    task = YoutubeTask(url="u", format=YoutubeFormat.MP3)
    restored = adapter.validate_python(task.model_dump(mode="json"))
    assert restored.url == "u"
    assert restored.format == YoutubeFormat.MP3
