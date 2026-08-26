#!/bin/sh
set -eu

install -d -o meetingassistant -g meetingassistant -m 750 /var/lib/meeting-assistant
install -o root -g meetingassistant -m 750 /tmp/meeting-assistant-server.py /opt/meeting-assistant/server.py
install -o root -g root -m 644 /tmp/meeting-assistant.service /etc/systemd/system/meeting-assistant.service

if ! grep -q '^TELEGRAM_WEBHOOK_SECRET=.' /etc/meeting-assistant.env; then
    printf 'TELEGRAM_WEBHOOK_SECRET=%s\n' "$(openssl rand -hex 32)" >> /etc/meeting-assistant.env
fi
if ! grep -q '^MEETING_API_TOKEN=.' /etc/meeting-assistant.env; then
    printf 'MEETING_API_TOKEN=%s\n' "$(openssl rand -hex 32)" >> /etc/meeting-assistant.env
fi
chown root:meetingassistant /etc/meeting-assistant.env
chmod 640 /etc/meeting-assistant.env

python3 -m py_compile /opt/meeting-assistant/server.py
systemctl daemon-reload
systemctl restart meeting-assistant.service
sleep 1
systemctl is-active meeting-assistant.service
