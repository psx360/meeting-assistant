#!/bin/bash
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run as root: sudo ./install-vps.sh" >&2
    exit 1
fi
repo="$(cd "$(dirname "$0")" && pwd)"

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y python3 ffmpeg nginx openssl curl
getent group meetingassistant >/dev/null || groupadd --system meetingassistant
id meetingassistant >/dev/null 2>&1 || useradd --system --gid meetingassistant --home /var/lib/meeting-assistant --shell /usr/sbin/nologin meetingassistant
install -d -o meetingassistant -g meetingassistant -m 0750 /var/lib/meeting-assistant /opt/meeting-assistant
install -o root -g meetingassistant -m 0750 "$repo/meeting-assistant-server.py" /opt/meeting-assistant/server.py
install -o root -g meetingassistant -m 0640 "$repo/meeting-documents.py" /opt/meeting-assistant/meeting-documents.py
install -o root -g meetingassistant -m 0750 "$repo/meeting-worker.py" /opt/meeting-assistant/worker.py
install -d -o root -g root -m 0755 /var/www/meeting-setup
install -o root -g root -m 0644 "$repo/bluefy-setup.html" /var/www/meeting-setup/index.html
install -o root -g root -m 0644 "$repo/meeting-assistant.service" /etc/systemd/system/
install -o root -g root -m 0644 "$repo/meeting-worker.service" /etc/systemd/system/

if [[ ! -f /etc/meeting-assistant.env ]]; then
    install -o root -g meetingassistant -m 0640 "$repo/vps.env.example" /etc/meeting-assistant.env
    sed -i "s/replace-with-a-long-random-value/$(openssl rand -hex 32)/" /etc/meeting-assistant.env
    echo "Edit /etc/meeting-assistant.env and replace all remaining placeholders."
fi

python3 -m py_compile /opt/meeting-assistant/server.py /opt/meeting-assistant/worker.py
install -m 0644 "$repo/meeting-assistant.nginx.conf" /etc/nginx/sites-available/meeting-assistant
ln -sfn /etc/nginx/sites-available/meeting-assistant /etc/nginx/sites-enabled/meeting-assistant
nginx -t
systemctl daemon-reload
systemctl enable --now meeting-assistant.service meeting-worker.service nginx.service
echo "VPS installation complete. Configure DNS/TLS and webhooks as described in README.md."
