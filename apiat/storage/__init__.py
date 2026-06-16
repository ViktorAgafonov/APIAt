"""Слой хранения данных (SQLite)."""

from .db import connect, init_db
from .repositories import Storage

__all__ = ["connect", "init_db", "Storage"]
