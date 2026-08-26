#!/bin/sh
set -eu
echo SYMBOLS
ls /proc/device-tree/__symbols__ | grep -Ei '^i2c0|i2c0.*m1|i2c.*xfer' | sort || true
echo PATHS
for name in i2c0 i2c0m1_xfer; do
    printf '%s=' "$name"
    tr -d '\000' < "/proc/device-tree/__symbols__/$name" 2>/dev/null || true
    echo
done
echo CURRENT_I2C0
path="$(tr -d '\000' < /proc/device-tree/__symbols__/i2c0)"
find "/proc/device-tree$path" -maxdepth 1 -type f -printf '%f\n' | sort
