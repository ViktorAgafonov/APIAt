"""Строго типизированные модели задач. Строковые команды запрещены."""

from __future__ import annotations

from enum import Enum
from typing import Annotated, Literal, Union

from pydantic import Field

from .base import BaseTask, TaskType


class YoutubeFormat(str, Enum):
    MP3 = "mp3"
    MP4 = "mp4"


class SearchTask(BaseTask):
    """Поиск информации/документации/новостей."""

    type: Literal[TaskType.SEARCH] = TaskType.SEARCH
    query: str
    max_results: int = 5
    use_rss: bool = False


class NewsTask(BaseTask):
    """Поиск свежих новостей по теме."""

    type: Literal[TaskType.NEWS] = TaskType.NEWS
    topic: str
    max_results: int = 5
    use_rss: bool = False


class DownloadTask(BaseTask):
    """Загрузка произвольного файла по URL."""

    type: Literal[TaskType.DOWNLOAD] = TaskType.DOWNLOAD
    url: str
    filename: str | None = None


class YoutubeTask(BaseTask):
    """Скачивание видео/аудио через yt-dlp."""

    type: Literal[TaskType.YOUTUBE] = TaskType.YOUTUBE
    url: str
    format: YoutubeFormat = YoutubeFormat.MP4
    max_quality: int | None = None  # ограничение по высоте, напр. 720


class BrowserTask(BaseTask):
    """Резервный режим: действия в Chromium через Playwright."""

    type: Literal[TaskType.BROWSER] = TaskType.BROWSER
    url: str
    instruction: str


class FileTask(BaseTask):
    """Операции с файлами/архивами."""

    type: Literal[TaskType.FILE] = TaskType.FILE
    operation: Literal["archive", "split"]
    paths: list[str] = Field(default_factory=list)


# Дискриминируемое объединение для типобезопасного парсинга
AnyTask = Annotated[
    Union[SearchTask, NewsTask, DownloadTask, YoutubeTask, BrowserTask, FileTask],
    Field(discriminator="type"),
]
