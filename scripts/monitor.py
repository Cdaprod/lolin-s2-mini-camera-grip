#!/usr/bin/env python3
import glob
import json
import sys

try:
    import serial
except ImportError:
    raise SystemExit("Install pyserial: python3 -m pip install pyserial")


def ports():
    found = []
    for pattern in ("/dev/cu.usbmodem*", "/dev/ttyACM*", "/dev/ttyUSB*"):
        found.extend(glob.glob(pattern))
    return sorted(set(found))


def choose():
    if len(sys.argv) > 1:
        return sys.argv[1]

    p = ports()
    if not p:
        raise SystemExit("No likely USB serial ports found.")
    if len(p) == 1:
        return p[0]

    print("Possible ports:")
    for item in p:
        print(" ", item)
    raise SystemExit("Pass the CircuitPython CDC data port explicitly.")


port = choose()
print("Opening:", port)

with serial.Serial(port, baudrate=115200, timeout=1) as ser:
    try:
        while True:
            line = ser.readline()
            if not line:
                continue

            text = line.decode("utf-8", errors="replace").strip()

            try:
                print(json.dumps(json.loads(text), indent=2))
            except json.JSONDecodeError:
                print(text)
    except KeyboardInterrupt:
        print()
