"""Smoke-тест файлового кэша: проверяет директории, tmpfs, get_temp_dir, cleanup."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from apiat.config import get_settings
from apiat.utils.cache import (
    check_disk_limit,
    cleanup_stale,
    disk_usage_mb,
    get_temp_dir,
    release_temp_dir,
)

EXPECTED_DIRS = [
    "downloads/pending",
    "downloads/done",
    "downloads/failed",
    "browser/sessions",
    "browser/screenshots",
    "browser/cookies",
    "archive/parts",
    "tmp",
]

PURPOSE = {
    "downloads/pending": "файлы в процессе загрузки (DownloadTool, YoutubeTool)",
    "downloads/done":    "завершённые загрузки, ожидающие отправки по email (TTL 7 дней)",
    "downloads/failed":  "неудачные загрузки для диагностики (TTL 3 дня)",
    "browser/sessions":  "профили Playwright: cookies, localStorage (TTL 30 дней)",
    "browser/screenshots": "скриншоты страниц для диагностики (TTL 24 часа)",
    "browser/cookies":   "экспортированные cookie-файлы (BrowserTool)",
    "archive/parts":     "части split-архивов до отправки по email (ArchiveTool)",
    "tmp":               "временные файлы инструментов, fallback если /dev/shm заполнен (TTL 1 час)",
}


def main() -> None:
    settings = get_settings()
    settings.ensure_dirs()
    data_dir = settings.data_dir

    print(f"\n=== Корневая директория данных: {data_dir.resolve()} ===\n")

    all_ok = True
    for rel in EXPECTED_DIRS:
        path = data_dir / rel
        exists = path.exists() and path.is_dir()
        size_mb = disk_usage_mb(path)
        status = "✓" if exists else "✗ ОТСУТСТВУЕТ"
        print(f"  {status}  {rel}/")
        print(f"       Назначение: {PURPOSE[rel]}")
        print(f"       Размер: {size_mb:.2f} MB")
        if not exists:
            all_ok = False

    print(f"\n=== tmpfs / get_temp_dir ===\n")
    tmp_dir = get_temp_dir("smoke-test-task", data_dir)
    in_shm = "/dev/shm" in str(tmp_dir)
    print(f"  Временная директория для задачи: {tmp_dir}")
    print(f"  {'✓ RAM (/dev/shm)' if in_shm else '✓ Диск (fallback)'}")

    # Пишем тестовый файл и читаем
    test_file = tmp_dir / "hello.txt"
    test_file.write_text("cache smoke ok")
    assert test_file.read_text() == "cache smoke ok", "Чтение из tmp не совпало"
    print(f"  ✓ Запись/чтение в tmp работают")

    release_temp_dir(tmp_dir)
    assert not tmp_dir.exists(), "release_temp_dir не удалил директорию"
    print(f"  ✓ release_temp_dir — директория удалена")

    print(f"\n=== check_disk_limit / disk_usage_mb ===\n")
    ok = check_disk_limit(data_dir, limit_mb=10000)
    total_mb = disk_usage_mb(data_dir)
    print(f"  Текущий размер data/: {total_mb:.2f} MB")
    print(f"  ✓ Лимит 10 GB {'не превышен' if ok else 'ПРЕВЫШЕН'}")

    print(f"\n=== cleanup_stale (dry run) ===\n")
    cleanup_stale(data_dir)
    print(f"  ✓ cleanup_stale выполнен без ошибок")

    print(f"\n{'='*50}")
    if all_ok:
        print("РЕЗУЛЬТАТ: все директории на месте, кэш работает корректно ✓")
    else:
        print("РЕЗУЛЬТАТ: часть директорий ОТСУТСТВУЕТ ✗")
        sys.exit(1)


if __name__ == "__main__":
    main()
