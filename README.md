# APIAt — Asynchronous Personal Internet Agent

Персональный интернет-агент: команды приходят по email, парсятся в типизированные
задачи (PydanticAI), исполняются как конечный автомат (Burr), результат отправляется
обратно по email. Браузер (Chromium/Playwright) — только резервный режим.

## Архитектура

```
Mail Gateway -> Intent Parser (LlmRouter/PydanticAI) -> Typed Task
   -> Task Planner -> Burr Workflow -> Tools -> Internet
   -> Result Generator -> Email Sender

Команды оператора (email):
   обнови код       -> git pull + pip install + restart
   самообучись: ... -> LLM → ревью → Docker sandbox → подтверждение
   список навыков   -> отчёт о закреплённых и pending навыках
   переключи llm    -> смена/статус LLM-провайдеров
```

Структура пакета `apiat/`:

- `config.py` — настройки из `.env` (LLM primary/fallback, IMAP, SMTP, безопасность).
- `models/` — строго типизированные модели задач, почты и метрик.
- `storage/` — SQLite: задачи, история, результаты, сессии, cookies, токены.
- `gateway/` — IMAP-клиент и проверки безопасности (whitelist, токен, dedup).
- `intent/` — LlmRouter (failover), парсер интентов, SelfCorrector.
- `planner/`, `workflow/` — выбор и исполнение Burr-workflow.
- `tools/` — абстракция инструмента, реестр и реализации.
- `skills/` — система навыков: генерация, ревью, Docker sandbox, подтверждение.
- `utils/` — логирование, файловый кэш (tmpfs/disk), TTL-очистка.

## Быстрый старт на VPS

```bash
# 1 — установка (клон + venv + зависимости + systemd enable)
bash <(curl -fsSL https://raw.githubusercontent.com/ViktorAgafonov/APIAt/main/deploy/install.sh)

# 2 — заполнить секреты (IMAP, SMTP, LLM, токен доступа)
nano /opt/apiat/.env

# 3 — запустить
systemctl start apiat

# 4 — проверить
journalctl -u apiat -f
```

Подробнее: [`docs/deployment.md`](docs/deployment.md)

## Локальная разработка

```bash
python -m venv .venv
source .venv/bin/activate        # Linux/macOS
# .\.venv\Scripts\Activate.ps1  # Windows PowerShell
pip install -r requirements.txt
cp .env.example .env             # заполнить значения
python -m apiat.cli --once       # один проход для проверки
```

## Smoke-тесты (проверка подключений)

```bash
# Проверить LLM (primary → fallback автоматически)
PYTHONPATH=. python scripts/llm_smoke.py

# Проверить IMAP + SMTP
PYTHONPATH=. python scripts/mail_smoke.py

# Проверить Burr workflow
PYTHONPATH=. python scripts/workflow_smoke.py
```

## Юнит-тесты

```bash
pytest
```

## Управление сервисом на сервере

```bash
systemctl status apiat           # статус
systemctl restart apiat          # перезапустить (безопасно)
journalctl -u apiat -f           # логи в реальном времени
journalctl -u apiat --since "1h ago"

# Ручное обновление кода
cd /opt/apiat && git pull origin main && systemctl restart apiat
```

## Команды оператора (в теле письма)

Все команды требуют авторизации: письмо должно быть с адреса из whitelist
и содержать секретный токен (задаётся в `.env`, не публикуется).

| Команда в письме | Действие |
|---|---|
| `обнови код` / `update code` | `git pull` + `pip install` + перезапуск; при ошибке — автооткат |
| `обнови среду` / `update sandbox` | Пересборка Docker-образа `apiat-sandbox` |
| `самообучись: <описание>` | Создать навык: LLM → ревью → Docker sandbox → валидация → подтверждение |
| `закрепи навык <имя>` | Переместить навык из `pending/` в `skills/` |
| `список навыков` | Закреплённые + ожидающие подтверждения |
| `цепочка: <задача>` | LLM строит и выполняет план из закреплённых навыков |
| `сохрани цепочку <имя>` | Сохранить последнюю цепочку как `.chain.json` |
| `выполни цепочку <имя>: key=val` | Запустить сохранённую цепочку с параметрами |
| `переключи llm` | Статус LLM-провайдеров |
| `переключи llm` + `KEY=value` | Сменить параметры LLM; при ошибке — автооткат |
| `анализ писем` | LLM анализирует последние 50 писем: паттерны, скрытые потребности, рекомендации по навыкам |
| `рекомендации` / `статус агента` | Показать последние сохранённые рекомендации |
| `покажи whitelist` | Список разрешённых адресов (env + динамический) |
| `добавь в whitelist <email>` | Добавить адрес в динамический whitelist |
| `убери из whitelist <email>` | Удалить адрес из whitelist |
| `авторизуйся: url=... логин=... пароль=...` | Войти на сайт через Chromium, сессия сохраняется в БД |
| `помощь` / `help` | Справка по командам + список навыков |

### Цикл самообучения

```
самообучись: <описание навыка>
  → LLM генерирует Python-модуль
  → LLM (ревью): "можно в релиз?"  ← только если primary LLM, не fallback
  → Docker sandbox: --network none, --memory 128m, --read-only
  → LLM валидирует: вывод соответствует заданию?
  → Письмо оператору: вывод навыка + инструкция подтвердить

закрепи навык <имя>
  → data/skills/pending/<имя>.py → data/skills/<имя>.py
```

### Цепочки навыков

```
цепочка: скачай файл по url, разбей по 10MB, упакуй zip, пришли
  → LLM анализирует задачу + список закреплённых навыков
  → Строит план: [download_file, split_file, pack_archive]
  → Выполняет последовательно, шаги обмениваются файлами через /data
  → Отчёт с планом + предложение сохранить

сохрани цепочку download_pack_send
  → data/skills/chains/download_pack_send.chain.json

выполни цепочку download_pack_send: url=https://... email=user@example.com
  → Запуск без LLM по сохранённому плану
```

**Передача данных между шагами** — общая рабочая директория `/data` (tmp/<run_id>/).
Каждый навык в цепочке работает с `profile=storage`, монтируется автоматически.
Stdout шага доступен следующему в params как `{prev_skill.output}`.

**Формат `.chain.json`:**
```json
{
  "name": "download_pack_send",
  "description": "Скачать, разбить, упаковать, отправить",
  "steps": [
    {"skill": "download_file", "params": {"url": "{input.url}"}},
    {"skill": "split_file",    "params": {"max_mb": "10"}},
    {"skill": "pack_archive",  "params": {}}
  ]
}
```

### Ручное написание модулей навыков

Навык — Python-файл в `data/skills/` (закреплённые) или `data/skills/pending/` (ожидают подтверждения).

**Обязательные требования:**

1. **Один файл** — весь код в одном `.py`, никаких относительных импортов.
2. **Имя файла** — `snake_case`: `server_status.py`, `parse_page.py`, `split_archive.py`.
3. **Вывод** — только через `print()` в stdout.
4. **Формат вывода** — plain text, удобно читаемый: разделы через пустую строку, значения через `: `.
5. **Зависимости** — только `stdlib` + `psutil` + `requests` (все установлены). Без `pip install`.
6. **Timeout** — код должен завершиться за время из метаданных (по умолчанию 30 сек).

**Профили изоляции sandbox:**

| Профиль | Сеть | Диск хоста | Типичное применение |
|---|---|---|---|
| `isolated` | нет | нет, только `/tmp` 32 MB | статус, отчёты, вычисления |
| `network` | HTTP/HTTPS | нет, только `/tmp` 32 MB | парсинг URL, HTTP-запросы |
| `storage` | нет | `/data` (rw, путь из метаданных) | архивы, split-файлы, обработка загрузок |

Профиль по умолчанию — `isolated`. Для смены — метаданные в начале файла.

**Метаданные лимитов** (первые строки файла, необязательны если устраивают дефолты):

```python
# skill:profile=isolated   # isolated | network | storage
# skill:memory=128m        # лимит RAM
# skill:timeout=30         # секунд до timeout
# skill:tmpfs=32m          # размер /tmp
# skill:storage_mount=/opt/apiat/data/downloads/done  # для profile=storage
```

**Шаблон: изолированный (статус сервера)**

```python
# skill:profile=isolated
import datetime, psutil

mem = psutil.virtual_memory()
disk = psutil.disk_usage("/")
uptime_h = round(float(open("/proc/uptime").read().split()[0]) / 3600, 1)

print("=== Статус сервера ===")
print(f"Время  : {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print(f"Uptime : {uptime_h} ч")
print(f"RAM    : {mem.used//1024**2}/{mem.total//1024**2} MB ({mem.percent}%)")
print(f"Disk   : {disk.used//1024**3}/{disk.total//1024**3} GB ({disk.percent}%)")
```

**Шаблон: сетевой (HTTP-запрос / парсинг)**

```python
# skill:profile=network
# skill:timeout=20
import requests

url = "https://example.com/api/status"
r = requests.get(url, timeout=10)
data = r.json()

print(f"Статус: {r.status_code}")
print(f"Ответ: {data}")
```

**Шаблон: storage (работа с файлами)**

```python
# skill:profile=storage
# skill:storage_mount=/opt/apiat/data/downloads/done
# skill:memory=256m
# skill:timeout=60
import os, zipfile

files = os.listdir("/data")
out = "/data/archive.zip"
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as zf:
    for f in files:
        if f != "archive.zip":
            zf.write(f"/data/{f}", f)

print(f"Архив создан: /data/archive.zip")
print(f"Файлов упаковано: {len(files)}")
print(f"Размер: {os.path.getsize(out)//1024} KB")
```

**Как добавить вручную:**

```bash
# Напрямую в закреплённые (без подтверждения по письму)
cp my_skill.py /opt/apiat/data/skills/my_skill.py

# Через pending (с подтверждением)
cp my_skill.py /opt/apiat/data/skills/pending/my_skill.py
# Затем письмо: закрепи навык my_skill
```

**Проверка на сервере:**

```bash
cd /opt/apiat
# Быстро без sandbox
.venv/bin/python data/skills/my_skill.py

# Точно как агент (isolated)
docker run --rm --network none --memory 128m --read-only \
  --tmpfs /tmp:size=32m \
  --volume $(pwd)/data/skills:/sandbox:ro \
  --workdir /sandbox python:3.12-slim python my_skill.py

# Для network-профиля
docker run --rm --network bridge --memory 128m --read-only \
  --tmpfs /tmp:size=32m \
  --volume $(pwd)/data/skills:/sandbox:ro \
  --workdir /sandbox python:3.12-slim python my_skill.py
```

### Формат команды смены LLM

```
<секретный токен>
переключи llm
LLM_BASE_URL=https://new-provider.example.com/api/
LLM_API_KEY=<новый ключ>
LLM_MODEL_NAME=<имя модели>
```

Допустимые ключи: `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL_NAME`,
`LLM_FALLBACK_API_KEY`, `LLM_FALLBACK_MODEL_NAME`.

## LLM Failover

Агент использует два LLM-провайдера (настраиваются в `.env`):

- **Primary** — основной (OpenAI-совместимый протокол)
- **Fallback** — резервный (Google Gemini), включается при недоступности primary

При сбое primary — автоматически переключается на fallback с cooldown 120 сек.
При восстановлении primary — возвращается на него автоматически.

## Конфигурация

Все секреты хранятся в `.env` (не публикуется, см. `.env.example`):

```
LLM_BASE_URL=          # URL primary провайдера (обязательно со слешем)
LLM_API_KEY=           # API-ключ primary
LLM_MODEL_NAME=        # имя модели primary
LLM_FALLBACK_API_KEY=  # API-ключ fallback (оставить пустым если не нужен)
LLM_FALLBACK_MODEL_NAME=gemini-2.5-flash
WHITELIST=             # адреса операторов через запятую (не публиковать)
SECRET_TOKEN=          # кодовое слово в письме (не публиковать)
```

## Статус

- [x] Каркас и инфраструктура (v0.1)
- [x] LLM failover router + self-correction (v0.2)
- [x] Самообновление по команде из письма
- [x] Развёртывание на VPS (Ubuntu 24.04, systemd, `deploy/install.sh`)
- [x] Система самообучения: LLM → ревью → Docker sandbox → подтверждение
- [x] Цепочки навыков (chain плanner + save/run)
- [x] YouTube: субтитры через `youtube-transcript-api` (без 429), качество 480p по умолчанию, TTL 2 ч
- [x] Adaptive polling (FAST/NORMAL/IDLE по активности)
- [x] Кольцевой лог 50 писем + LLM-анализ + persistent рекомендации
- [x] Динамический whitelist (env + SQLite)
- [x] Безопасность: path traversal, лимиты размеров, маскировка токена, auto-purge БД
- [ ] Расширенные браузерные сценарии (многошаговая авторизация)
