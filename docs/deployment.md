# Развёртывание APIAt на VPS

## Характеристики сервера
- **SSH port**: *port number*, пользователь: *root*
- **ОС**: Ubuntu 24.04.4 LTS
- **Python**: 3.12.3 (системный)
- **Ресурсы**: 2 CPU / 4 GB RAM / 10 GB disk
- **ВАЖНО**: не трогать сетевые интерфейсы, firewall

## Структура на сервере

```
/opt/apiat/          — корень проекта (git clone)
/opt/apiat/.venv/    — Python виртуальное окружение
/opt/apiat/.env      — секреты (chmod 600, не в git)
/opt/apiat/data/     — SQLite БД и загрузки (создаётся автоматически)
/etc/systemd/system/apiat.service  — systemd unit
```

## Процедура первичного развёртывания

```bash
# 1. Системные зависимости (только pip/venv, сеть не трогается)
apt-get install -y python3-pip python3-venv

# 2. Клонирование репозитория
git clone https://github.com/ViktorAgafonov/APIAt.git /opt/apiat

# 3. Виртуальное окружение и зависимости
cd /opt/apiat
python3 -m venv .venv
.venv/bin/pip install --upgrade pip -q
.venv/bin/pip install -r requirements.txt -q

# 4. Создать .env (секреты — вручную, без git)
cat > /opt/apiat/.env << 'EOF'
LLM_BASE_URL=...
...
EOF
chmod 600 /opt/apiat/.env

# 5. Smoke-тест LLM
PYTHONPATH=. .venv/bin/python scripts/llm_smoke.py

# 6. Регистрация systemd service
# (см. /etc/systemd/system/apiat.service ниже)
systemctl daemon-reload
systemctl enable apiat
systemctl start apiat
```

## systemd unit (/etc/systemd/system/apiat.service)

```ini
[Unit]
Description=APIAt Personal Internet Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/apiat
Environment=PYTHONPATH=/opt/apiat
ExecStart=/opt/apiat/.venv/bin/python -m apiat.cli --daemon
Restart=on-failure
RestartSec=30
StandardOutput=journal
StandardError=journal
SyslogIdentifier=apiat

[Install]
WantedBy=multi-user.target
```

## Управление сервисом

```bash
systemctl status apiat          # статус
systemctl stop apiat            # остановить
systemctl start apiat           # запустить
systemctl restart apiat         # перезапустить (безопасно)
journalctl -u apiat -f          # логи в реальном времени
journalctl -u apiat --since "1h ago"  # логи за час
```

## Обновление кода

```bash
cd /opt/apiat
git pull origin main
systemctl restart apiat
```

## Ручной запуск (один проход, для отладки)

```bash
cd /opt/apiat
PYTHONPATH=. .venv/bin/python -m apiat.cli --once
```

## Проверка VPN после любых операций

```bash
ps aux | grep -iE '**this name servisec on VPS**' | grep -v grep | wc -l
# Должно быть столько же процессов, сколько до развертывания?
```

## Важные ограничения

- **Нежелательно выполнять**: `reboot`, `shutdown`, `systemctl restart docker`, изменения `iptables`/`nftables`, изменения `/opt/amnezia/`
- `systemctl restart apiat` — **безопасно**, Docker и VPN не затрагиваются
- При обновлении зависимостей (`pip install`) VPN не затрагивается
