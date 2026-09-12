#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
MOUNT="${1:-/Volumes/CIRCUITPY}"
PYTHON="${PYTHON:-python3}"

echo "=== CDAProd Camera Grip Installer ==="
echo "Target: $MOUNT"

if [[ ! -d "$MOUNT" ]]; then
  echo "ERROR: CIRCUITPY drive not found: $MOUNT" >&2
  exit 1
fi

if ! command -v circup >/dev/null 2>&1; then
  echo "Installing CircUp..."
  "$PYTHON" -m pip install --user -U circup
  USER_BASE="$("$PYTHON" -m site --user-base)"
  export PATH="$USER_BASE/bin:$PATH"
fi

if ! command -v circup >/dev/null 2>&1; then
  echo "ERROR: circup is not available on PATH." >&2
  exit 1
fi

echo
echo "Installing CircuitPython HID library..."
circup --path "$MOUNT" install adafruit_hid

echo
echo "Copying firmware..."
cp "$ROOT/CIRCUITPY/boot.py" "$MOUNT/boot.py"
cp "$ROOT/CIRCUITPY/code.py" "$MOUNT/code.py"
cp "$ROOT/CIRCUITPY/config.py" "$MOUNT/config.py"

sync

echo
echo "Installed successfully."
echo "Unplug/replug the board once so boot.py USB settings take effect."
