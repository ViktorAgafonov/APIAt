# APIAt — Asynchronous Personal Internet Agent

Персональный интернет-агент: команды приходят по email, парсятся в типизированные
задачи (PydanticAI), исполняются как конечный автомат (Burr), результат отправляется
обратно по email. Браузер (Chromium/Playwright) — только резервный режим.

## Архитектура

```
Mail Gateway -> Intent Parser (PydanticAI) -> Typed Task
   -> Task Planner -> Burr Workflow -> Tools -> Internet
   -> Result Generator -> Email Sender
```

Структура пакета `apiat/`:

- `config.py` — настройки из `.env` (LLM / IMAP / SMTP / безопасность).
- `models/` — строго типизированные модели задач, почты и метрик.
- `storage/` — SQLite: задачи, история, результаты, сессии, cookies, токены.
- `gateway/` — IMAP-клиент и проверки безопасности (whitelist, токен, dedup).
- `intent/` — инициализация LLM-клиента и парсер интентов.
- `planner/`, `workflow/` — выбор и исполнение Burr-workflow.
- `tools/` — абстракция инструмента, реестр и реализации.
- `utils/` — логирование и трассировка.

## Установка

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
playwright install chromium
copy .env.example .env   # затем заполнить значения
```

## Запуск

```powershell
# Однократная обработка (cron / one-shot)
python -m apiat.cli --once

# Демон с периодическим опросом IMAP
python -m apiat.cli --daemon
```

## Тесты

```powershell
pytest
```

## Статус

Реализован каркас и инфраструктура. Полная бизнес-логика отдельных инструментов
(сложные browser-сценарии, докачка, разбиение архивов под лимит почты) и
премиальный плагин браузера — следующие этапы.
