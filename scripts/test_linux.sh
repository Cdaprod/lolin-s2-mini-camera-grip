#!/usr/bin/env bash
set -euo pipefail

echo "=== USB ==="
lsusb || true

echo
echo "=== Relevant input-device records ==="
grep -i -A8 -B2 -E 'CircuitPython|Espressif|LOLIN|camera grip' \
    /proc/bus/input/devices || true

echo
echo "=== /dev/input ==="
ls -l /dev/input 2>/dev/null || true

echo
echo "For live events:"
echo "  sudo evtest"
