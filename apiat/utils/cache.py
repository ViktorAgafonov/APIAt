"""Утилиты файлового кэша: tmpfs/disk, очистка устаревших файлов."""

from __future__ import annotations

import os
import shutil
import time
from pathlib import Path

# /dev/shm недоступен на Windows — используем системный tmp
_SHM_ROOT = Path("/dev/shm/apiat") if Path("/dev/shm").exists() else Path(
    os.environ.get("TEMP", "/tmp")
) / "apiat"

_SHM_FILL_THRESHOLD = 0.80  # fallback на диск если shm занято > 80%


def get_temp_dir(task_id: str, data_dir: Path) -> Path:
    """Возвращает временную директорию для задачи.

    На Linux предпочитает /dev/shm (RAM), при заполнении > 80% — data/tmp/.
    На Windows всегда использует data/tmp/.
    Директория создаётся автоматически.
    """
    shm_dir = _SHM_ROOT / task_id
    if _SHM_ROOT.parent.exists() and hasattr(os, "statvfs"):
        try:
            stat = os.statvfs(str(_SHM_ROOT.parent))
            used_ratio = 1.0 - stat.f_bavail / stat.f_blocks if stat.f_blocks else 1.0
        except (OSError, ZeroDivisionError):
            used_ratio = 1.0
        if used_ratio < _SHM_FILL_THRESHOLD:
            shm_dir.mkdir(parents=True, exist_ok=True)
            return shm_dir

    fallback = data_dir / "tmp" / task_id
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback


def release_temp_dir(task_dir: Path) -> None:
    """Удаляет временную директорию задачи после завершения."""
    shutil.rmtree(task_dir, ignore_errors=True)


def cleanup_stale(data_dir: Path) -> None:
    """Удаляет устаревшие файлы по TTL.

    Вызывается при старте демона и периодически.
    """
    _remove_old(data_dir / "tmp", max_age_sec=3600)
    _remove_old(data_dir / "browser" / "screenshots", max_age_sec=86400)
    _remove_old(data_dir / "downloads" / "done", max_age_sec=7 * 86400)
    _remove_old(data_dir / "downloads" / "failed", max_age_sec=3 * 86400)
    _remove_old(data_dir / "youtube" / "pending", max_age_sec=2 * 3600)
    _remove_old(data_dir / "attachments", max_age_sec=24 * 3600)
    _remove_old(data_dir / "skills" / "pending", max_age_sec=7 * 86400)
    # Осиротевшие shm-директории (если процесс упал)
    if _SHM_ROOT.exists():
        _remove_old(_SHM_ROOT, max_age_sec=3600)


def _remove_old(directory: Path, max_age_sec: int) -> None:
    if not directory.exists():
        return
    now = time.time()
    for entry in directory.iterdir():
        try:
            age = now - entry.stat().st_mtime
            if age > max_age_sec:
                if entry.is_dir():
                    shutil.rmtree(entry, ignore_errors=True)
                else:
                    entry.unlink(missing_ok=True)
        except OSError:
            pass


def check_disk_limit(directory: Path, limit_mb: int) -> bool:
    """Возвращает True если директория не превышает лимит."""
    try:
        used = sum(f.stat().st_size for f in directory.rglob("*") if f.is_file())
        return used < limit_mb * 1024 * 1024
    except OSError:
        return True


def disk_usage_mb(directory: Path) -> float:
    """Возвращает размер директории в MB."""
    try:
        return sum(f.stat().st_size for f in directory.rglob("*") if f.is_file()) / (1024 * 1024)
    except OSError:
        return 0.0
