#!/bin/bash
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "Run as root: sudo ./install-radxa.sh" >&2
    exit 1
fi

repo="$(cd "$(dirname "$0")" && pwd)"
device_user="${RADXA_USER:-radxa}"
device_home="$(getent passwd "$device_user" | cut -d: -f6)"
if [[ -z "$device_home" ]]; then
    echo "User $device_user does not exist" >&2
    exit 1
fi
if [[ "$(id -u "$device_user")" != "1000" ]]; then
    echo "Current controller code expects $device_user to have UID 1000" >&2
    exit 1
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
    ffmpeg sox curl device-tree-compiler i2c-tools gpiod python3-libgpiod \
    python3-dbus python3-gi bluez network-manager pipewire pipewire-pulse wireplumber

install -d -m 0755 /usr/local/sbin /var/lib/meeting-recorder
install -m 0755 "$repo/recorder-controller.py" /usr/local/sbin/recorder-controller.py
install -m 0755 "$repo/oled-dashboard.py" /usr/local/sbin/oled-dashboard.py
install -m 0755 "$repo/bt-wifi-gatt.py" /usr/local/sbin/bt-wifi-gatt.py
install -m 0755 "$repo/bt-pairing-mode.sh" /usr/local/sbin/bt-pairing-mode.sh
install -m 0755 "$repo/wifi-watchdog.sh" /usr/local/sbin/wifi-watchdog.sh
install -m 0644 "$repo/recorder-controller.service" /etc/systemd/system/
install -m 0644 "$repo/oled-dashboard.service" /etc/systemd/system/
install -m 0644 "$repo/bt-pairing-mode.service" /etc/systemd/system/
install -m 0644 "$repo/wifi-watchdog.service" /etc/systemd/system/

install -m 0755 "$repo/audio-split-test.sh" "$device_home/audio-split-test.sh"
install -m 0755 "$repo/meeting-upload.sh" "$device_home/meeting-upload.sh"
install -d -o "$device_user" -g "$device_user" -m 0755 "$device_home/.config/systemd/user"
install -m 0644 "$repo/audio-recorder.service" "$device_home/.config/systemd/user/"
install -m 0644 "$repo/meeting-upload.service" "$device_home/.config/systemd/user/"
install -m 0644 "$repo/meeting-upload.timer" "$device_home/.config/systemd/user/"
chown "$device_user:$device_user" "$device_home/audio-split-test.sh" "$device_home/meeting-upload.sh" "$device_home/.config/systemd/user/"*

install -d -o "$device_user" -g "$device_user" -m 0700 "$device_home/.config"
if [[ ! -f "$device_home/.config/meeting-upload.env" ]]; then
    install -o "$device_user" -g "$device_user" -m 0600 "$repo/radxa.env.example" "$device_home/.config/meeting-upload.env"
    echo "Edit $device_home/.config/meeting-upload.env before testing uploads."
fi

dtc -@ -I dts -O dtb -o /boot/dtbo/rk3528a-inmp441.dtbo "$repo/rk3528a-inmp441-fixed.dts"
dtc -@ -I dts -O dtb -o /boot/dtbo/rk3528-i2c0-m1.dtbo "$repo/rk3528-i2c0-m1.dts"
python3 "$repo/install-extlinux-overlays.py" /boot/extlinux/extlinux.conf \
    /boot/dtbo/rk3528-i2c0-m1.dtbo /boot/dtbo/rk3528a-inmp441.dtbo

loginctl enable-linger "$device_user"
systemctl daemon-reload
systemctl enable recorder-controller.service oled-dashboard.service wifi-watchdog.service bluetooth.service
systemctl restart bluetooth.service recorder-controller.service oled-dashboard.service wifi-watchdog.service
uid="$(id -u "$device_user")"
systemctl start "user@$uid.service"
runuser -u "$device_user" -- env XDG_RUNTIME_DIR="/run/user/$uid" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus" \
    systemctl --user daemon-reload
runuser -u "$device_user" -- env XDG_RUNTIME_DIR="/run/user/$uid" \
    DBUS_SESSION_BUS_ADDRESS="unix:path=/run/user/$uid/bus" \
    systemctl --user enable --now meeting-upload.timer

echo "Installation complete. Reboot is required to activate I2S and I2C overlays."
