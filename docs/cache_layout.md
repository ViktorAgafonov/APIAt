# Файловый кэш и хранилище данных APIAt

## Ресурсы сервера (актуально на момент развёртывания)

| Ресурс | Всего | Занято | Свободно |
|---|---|---|---|
| RAM | 1.9 GB | ~580 MB (Docker/VPN/OS) | ~1.4 GB |
| Disk `/` | 30 GB | ~6.5 GB | ~22 GB |
| tmpfs `/dev/shm` | 984 MB | — | ~984 MB |

**Принцип**: RAM — только для активных операций (не дольше времени задачи).
Диск — персистентное хранение результатов, сессий, кэша страниц.

---

## Структура директорий `/opt/apiat/data/`

```
/opt/apiat/data/                  chmod 750
│
├── apiat.db                      SQLite: задачи, история, результаты, настройки
│
├── downloads/                    chmod 750  Загрузки файлов (DownloadTool)
│   ├── pending/                  Файлы в процессе загрузки
│   ├── done/                     Завершённые загрузки (до отправки по email)
│   └── failed/                   Неудачные загрузки (для диагностики)
│
├── browser/                      chmod 750  Браузерный инструмент (BrowserTool)
│   ├── sessions/                 Профили Playwright (cookies, localStorage)
│   ├── screenshots/              Скриншоты страниц (временно, TTL 24h)
│   └── cookies/                  Экспортированные cookie-файлы
│
├── archive/                      chmod 750  Архивы и разбиение (ArchiveTool)
│   └── parts/                    Части split-архивов до отправки
│
└── tmp/                          chmod 750  Временные файлы любых инструментов
                                  Очищается при старте демона и по TTL
```

---

## Что хранить в RAM (`/dev/shm`)

`/dev/shm` — tmpfs в памяти, 984 MB. **Не переживает перезапуск процесса.**

| Что | Путь | Лимит | Обоснование |
|---|---|---|---|
| HTML-контент страницы (browser scrape) | `/dev/shm/apiat/page_<id>.html` | 10 MB/задача | Нужен только пока парсим, потом выбрасываем |
| yt-dlp промежуточный буфер | `/dev/shm/apiat/yt_<id>/` | 200 MB | Быстрая запись потока, потом mv на диск |
| Временный zip перед отправкой | `/dev/shm/apiat/zip_<id>.zip` | 50 MB | Собираем в RAM, сразу отдаём в SMTP |

**Жёсткие ограничения RAM:**
- Не держать в памяти более 1 задачи одновременно (1 CPU, daemon)
- После отправки письма — немедленно `shutil.rmtree` временной директории
- Если `/dev/shm` заполнится > 80% — fallback на `/opt/apiat/data/tmp/`

---

## Что хранить на диске

| Что | Путь | TTL | Обоснование |
|---|---|---|---|
| Результаты задач (JSON) | `apiat.db` → таблица `results` | навсегда | История для оператора |
| Browser-сессии / cookies | `browser/sessions/` | 30 дней | Авторизация на сайтах |
| Скриншоты | `browser/screenshots/` | 24 часа | Только для диагностики |
| Завершённые загрузки | `downloads/done/` | 7 дней | Повторная отправка если письмо потерялось |
| Неудачные загрузки | `downloads/failed/` | 3 дня | Диагностика |
| Части архивов | `archive/parts/` | до отправки | Удалять сразу после успешного SMTP |
| Временные файлы | `tmp/` | 1 час | Всё что не поместилось в RAM |

---

## Лимиты на диске

Диска 22 GB свободно. Разумные лимиты для инструментов:

| Инструмент | Лимит на задачу | Лимит директории |
|---|---|---|
| DownloadTool | 500 MB | 5 GB (`downloads/`) |
| YoutubeTool | 1 GB (видео) / 100 MB (аудио) | 3 GB |
| BrowserTool | 50 MB | 500 MB (`browser/`) |
| ArchiveTool | 2 GB (входные данные) | 4 GB |

При превышении лимита директории — удалять самые старые файлы в `done/`.

---

## Реализация в коде

### Путь к tmpfs в инструментах

```python
import os, shutil
from pathlib import Path

SHM_DIR = Path("/dev/shm/apiat")
SHM_THRESHOLD = 0.8  # fallback на диск если занято > 80%

def get_temp_dir(task_id: str, data_dir: Path) -> Path:
    shm = SHM_DIR / task_id
    shm_stat = os.statvfs("/dev/shm")
    used_ratio = 1 - shm_stat.f_bavail / shm_stat.f_blocks
    if used_ratio < SHM_THRESHOLD:
        shm.mkdir(parents=True, exist_ok=True)
        return shm
    fallback = data_dir / "tmp" / task_id
    fallback.mkdir(parents=True, exist_ok=True)
    return fallback
```

### Очистка при старте демона

```python
def cleanup_stale(data_dir: Path, max_age_hours: int = 1) -> None:
    import time
    now = time.time()
    for p in (data_dir / "tmp").glob("*"):
        if now - p.stat().st_mtime > max_age_hours * 3600:
            shutil.rmtree(p, ignore_errors=True)
    # Скриншоты старше 24h
    for p in (data_dir / "browser" / "screenshots").glob("*"):
        if now - p.stat().st_mtime > 86400:
            p.unlink(missing_ok=True)
```

### Настройки лимитов в `.env`

```
DOWNLOAD_MAX_MB=500
YOUTUBE_MAX_MB=1024
BROWSER_CACHE_DAYS=30
SCREENSHOT_TTL_HOURS=24
DONE_FILES_TTL_DAYS=7
```

---

## Права доступа

Всё принадлежит `root` (сервис запускается от root):

```bash
chown -R root:root /opt/apiat/data
chmod 750 /opt/apiat/data          # rwxr-x---
chmod -R 750 /opt/apiat/data/      # все поддиректории
# SQLite-файл
chmod 640 /opt/apiat/data/apiat.db # rw-r-----
```

`/dev/shm/apiat/` создаётся динамически при запуске задачи,
удаляется сразу после её завершения.
