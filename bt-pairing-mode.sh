#!/bin/bash
set -u

cleanup() {
    bluetoothctl discoverable off >/dev/null 2>&1 || true
    bluetoothctl pairable off >/dev/null 2>&1 || true
    rm -f /run/bt-pairing-mode
    echo "BT_PAIRING_STOPPED"
}
trap cleanup EXIT INT TERM

bluetoothctl power on
bluetoothctl system-alias "Meeting Assistant"
bluetoothctl discoverable-timeout 900
bluetoothctl pairable on
bluetoothctl discoverable on
date +%s > /run/bt-pairing-mode
echo "BT_PAIRING_STARTED timeout=900s alias=Meeting Assistant"
timeout --signal=TERM 900 /usr/local/sbin/bt-wifi-gatt.py || true
