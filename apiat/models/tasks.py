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
    url: str = ""
    format: YoutubeFormat = YoutubeFormat.MP4
    max_quality: int = 480           # по умолчанию 480p для экономии трафика
    subtitles: bool = False          # скачать субтитры
    metadata_only: bool = False      # только метаданные без скачивания
    thumbnail: bool = False          # скачать обложку (thumbnail)
    channel_search: str | None = None  # поиск канала по имени (если нет точного URL)


class BrowserTask(BaseTask):
    """Резервный режим: действия в Chromium через Playwright."""

    type: Literal[TaskType.BROWSER] = TaskType.BROWSER
    url: str
    instruction: str
    screenshot: bool = False  # сделать скриншот страницы и приложить к ответу


class FileTask(BaseTask):
    """Операции с файлами/архивами."""

    type: Literal[TaskType.FILE] = TaskType.FILE
    operation: Literal["archive", "split"]
    paths: list[str] = Field(default_factory=list)
    input_attachments: list[str] = Field(default_factory=list)


class SkillTask(BaseTask):
    """Запуск закреплённого навыка по имени."""

    type: Literal[TaskType.SKILL] = TaskType.SKILL
    skill_name: str
    params: dict = Field(default_factory=dict)


class ChainTask(BaseTask):
    """Запуск сохранённой цепочки навыков по имени."""

    type: Literal[TaskType.CHAIN] = TaskType.CHAIN
    chain_name: str
    params: dict = Field(default_factory=dict)


class ServerTask(BaseTask):
    """Серверная задача: анализ логов, статус сервиса, диски, процессы."""

    type: Literal[TaskType.SERVER] = TaskType.SERVER
    action: Literal["logs", "status", "disk", "processes", "custom"]
    query: str = ""  # дополнительный текст запроса оператора
    lines: int = 100  # сколько строк логов прочитать


# Дискриминируемое объединение для типобезопасного парсинга
AnyTask = Annotated[
    Union[SearchTask, NewsTask, DownloadTask, YoutubeTask, BrowserTask, FileTask, SkillTask, ChainTask, ServerTask],
    Field(discriminator="type"),
]
