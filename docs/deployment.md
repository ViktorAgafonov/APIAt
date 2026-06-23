# Развёртывание APIAt на VPS

**Окружение:** Ubuntu 24.04, root, Python 3.12, 2 CPU / 4 GB RAM / 10 GB disk

## Установка (первый раз)

```bash
bash <(curl -fsSL https://raw.githubusercontent.com/ViktorAgafonov/APIAt/main/deploy/install.sh)
```

Или вручную:

```bash
git clone https://github.com/ViktorAgafonov/APIAt.git /opt/apiat
bash /opt/apiat/deploy/install.sh
```

После установки **заполнить** `/opt/apiat/.env` (шаблон: `.env.example`) и запустить:

```bash
systemctl start apiat
journalctl -u apiat -f   # проверить что всё ок
```

## Обновление кода

```bash
cd /opt/apiat && git pull origin main && systemctl restart apiat
```

## Управление

```bash
systemctl status apiat              # статус
systemctl restart apiat             # перезапуск (безопасно)
journalctl -u apiat -f              # логи live
journalctl -u apiat --since "1h ago"  # логи за час
PYTHONPATH=. .venv/bin/python -m apiat.cli --once  # один проход (отладка)
```

## Структура данных

```
/opt/apiat/.env          — секреты (chmod 600, не в git)
/opt/apiat/data/         — SQLite БД, загрузки, кэш (создаётся автоматически)
/opt/apiat/data/logs/    — кольцевой лог писем + рекомендации
```

## ⚠️ Важно: что нельзя трогать на VPS

На сервере работает VPN (AmneziaWG). Следующие команды **запрещены**:

- `reboot`, `shutdown`
- `systemctl restart docker`
- Изменения `iptables` / `nftables`
- Любые файлы в `/opt/amnezia/`

`systemctl restart apiat` — **безопасно**, VPN и Docker не затрагиваются.
