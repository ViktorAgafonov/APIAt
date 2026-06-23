#!/bin/bash
# APIAt — установка на VPS (Ubuntu 24.04, root)
# Использование: bash install.sh
set -e

REPO="https://github.com/ViktorAgafonov/APIAt.git"
DIR="/opt/apiat"
SERVICE="/etc/systemd/system/apiat.service"

echo "=== [1/5] Системные зависимости ==="
apt-get install -y python3-pip python3-venv ffmpeg -q

echo "=== [2/5] Клонирование репозитория ==="
if [ -d "$DIR/.git" ]; then
    git -C "$DIR" pull origin main
else
    git clone "$REPO" "$DIR"
fi

echo "=== [3/5] Python-окружение ==="
python3 -m venv "$DIR/.venv"
"$DIR/.venv/bin/pip" install --upgrade pip -q
"$DIR/.venv/bin/pip" install -r "$DIR/requirements.txt" -q
"$DIR/.venv/bin/pip" install --break-system-packages youtube-transcript-api -q 2>/dev/null || \
"$DIR/.venv/bin/pip" install youtube-transcript-api -q
"$DIR/.venv/bin/playwright" install chromium -q

echo "=== [4/5] .env — секреты ==="
if [ ! -f "$DIR/.env" ]; then
    cp "$DIR/.env.example" "$DIR/.env"
    chmod 600 "$DIR/.env"
    echo ""
    echo "  >> Заполните $DIR/.env вашими данными, затем запустите:"
    echo "  >> systemctl start apiat"
    echo ""
else
    echo "  .env уже существует, пропускаем"
fi

echo "=== [5/5] systemd сервис ==="
cat > "$SERVICE" << 'EOF'
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
EOF

systemctl daemon-reload
systemctl enable apiat

echo ""
echo "=== Готово ==="
echo "Заполните /opt/apiat/.env, затем: systemctl start apiat"
echo "Логи: journalctl -u apiat -f"
