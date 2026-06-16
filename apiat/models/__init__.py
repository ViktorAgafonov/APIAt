"""Модели данных APIAt."""

from .base import BaseTask, TaskStatus, TaskType
from .email import Attachment, IncomingMail, OutgoingMail
from .metrics import TaskMetrics
from .tasks import (
    AnyTask,
    BrowserTask,
    DownloadTask,
    FileTask,
    NewsTask,
    SearchTask,
    YoutubeFormat,
    YoutubeTask,
)

__all__ = [
    "BaseTask",
    "TaskStatus",
    "TaskType",
    "Attachment",
    "IncomingMail",
    "OutgoingMail",
    "TaskMetrics",
    "AnyTask",
    "SearchTask",
    "NewsTask",
    "DownloadTask",
    "YoutubeTask",
    "YoutubeFormat",
    "BrowserTask",
    "FileTask",
]
