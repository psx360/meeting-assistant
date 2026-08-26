#!/bin/bash
set -u

interface="${WIFI_INTERFACE:-wlan0}"
retry_seconds=10

echo "WIFI_WATCHDOG_READY interface=$interface retry=${retry_seconds}s"

while true; do
    if ! nmcli -t -f DEVICE,STATE device status | grep -q "^${interface}:connected$"; then
        echo "WIFI_RECONNECT_BEGIN interface=$interface"
        rfkill unblock wifi 2>/dev/null || true
        nmcli radio wifi on
        connection="$(nmcli -t -f NAME,TYPE,AUTOCONNECT connection show | awk -F: '$2 ~ /wireless|wifi/ && $3 == "yes" {print $1; exit}')"
        if [[ -n "$connection" ]] && nmcli --wait 20 connection up id "$connection" ifname "$interface"; then
            echo "WIFI_RECONNECTED interface=$interface"
        else
            echo "WIFI_RECONNECT_FAILED interface=$interface"
        fi
    fi
    sleep "$retry_seconds"
done
