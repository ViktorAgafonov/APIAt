# APIAt — Asynchronous Personal Internet Agent

Персональный интернет-агент: команды приходят по email, парсятся в типизированные
задачи (PydanticAI), исполняются как конечный автомат (Burr), результат отправляется
обратно по email. Браузер (Chromium/Playwright) — только резервный режим.

## Архитектура

```
Mail Gateway -> Intent Parser (LlmRouter/PydanticAI) -> Typed Task
   -> Task Planner -> Burr Workflow -> Tools -> Internet
   -> Result Generator -> Email Sender
```

Структура пакета `apiat/`:

- `config.py` — настройки из `.env` (LLM primary/fallback, IMAP, SMTP, безопасность).
- `models/` — строго типизированные модели задач, почты и метрик.
- `storage/` — SQLite: задачи, история, результаты, сессии, cookies, токены.
- `gateway/` — IMAP-клиент и проверки безопасности (whitelist, токен, dedup).
- `intent/` — LlmRouter (failover), парсер интентов, SelfCorrector.
- `planner/`, `workflow/` — выбор и исполнение Burr-workflow.
- `tools/` — абстракция инструмента, реестр и реализации.
- `utils/` — логирование и трассировка.

## Установка

```bash
python -m venv .venv
source .venv/bin/activate        # Linux/macOS
# .\.venv\Scripts\Activate.ps1  # Windows PowerShell
pip install -r requirements.txt
playwright install chromium
cp .env.example .env             # затем заполнить значения
```

## Запуск

```bash
# Однократная обработка (cron / one-shot)
python -m apiat.cli --once

# Демон с периодическим опросом IMAP
python -m apiat.cli --daemon
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
| `обнови код` / `update code` | `git pull` + `pip install` + перезапуск сервиса; при ошибке — автооткат |
| `самообучись: <описание>` / `learn: <описание>` | Сгенерировать навык через LLM → ревью → Docker sandbox → валидация → ожидание подтверждения |
| `подтверди навык <имя>` / `confirm skill <имя>` | Переместить навык из `pending/` в `skills/` |
| `переключи llm` | Показать статус LLM-провайдеров и diff с резервной копией `.env` |
| `переключи llm` + строки `KEY=value` | Применить новые LLM-параметры, проверить пробным запросом; при ошибке — откат |

### Цикл самообучения

```
самообучись: <описание навыка>
  → LLM генерирует Python-модуль
  → LLM (ревью): "можно в релиз?"  ← только если primary LLM, не fallback
  → Docker sandbox: --network none, --memory 128m, --read-only
  → LLM валидирует: вывод соответствует заданию?
  → Письмо оператору: вывод навыка + инструкция подтвердить

подтверди навык <имя>
  → data/skills/pending/<имя>.py → data/skills/<имя>.py
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
- [x] Развёртывание на VPS (Ubuntu 24.04, systemd)
- [x] Система самообучения: LLM → ревью → Docker sandbox → подтверждение
- [ ] Полная бизнес-логика инструментов (browser, download, archive)
- [ ] Расширенные сценарии Burr-workflow
