# Cdaprod/lolin-s2-mini-camera-grip

CircuitPython firmware for a USB-C camera grip controller built around the
LOLIN S2 Mini / ESP32-S2.

## Hardware

- 3 finger buttons on perfboard
- analog thumb joystick with push switch
- LOLIN S2 Mini
- USB-C for both power and USB data

## Wiring

```text
IO1  <- joystick VRX
IO2  <- joystick VRY
IO3  <- joystick SW

IO4  <- index finger button
IO5  <- middle finger button
IO6  <- ring finger button

3V3  -> joystick VCC
GND  -> joystick GND
GND  -> common ground for finger buttons
```

Even if the joystick board is marked `+5V`, use **3.3V** here so its analog
outputs stay within the ESP32-S2 ADC range.

## Default mouse mode

```text
Joystick       -> mouse movement
Joy press      -> left click
Index          -> left click
Middle         -> right click
Ring           -> double click
```

## EVF mode

Hold:

```text
JOY PRESS + RING
```

for ~1 second to toggle:

```text
MOUSE <-> EVF
```

In EVF mode the controller stops moving the mouse and sends JSON-line events
over the CircuitPython USB CDC data port:

```json
{"type":"axis","x":0.1421,"y":-0.032}
{"type":"button","name":"index","event":"pressed"}
{"type":"button","name":"index","event":"released"}
```

That gives the Pi EVF software direct analog values for navigation,
adjustments, and future focus pulling.

## Repo layout

```text
.
├── README.md
├── CIRCUITPY/
│   ├── boot.py
│   ├── code.py
│   ├── config.py
│   └── lib/
│       └── README.txt
├── scripts/
│   ├── install.sh
│   ├── monitor.py
│   └── test_linux.sh
├── docs/
│   ├── PINOUT.md
│   └── PROTOCOL.md
└── LICENSE
```

## Install to the LOLIN S2 Mini

CircuitPython must already be flashed and the board should mount as:

```text
/Volumes/CIRCUITPY
```

Then:

```bash
chmod +x scripts/install.sh
./scripts/install.sh
```

The installer will:

1. locate the CIRCUITPY drive,
2. install CircUp if needed,
3. install/update `adafruit_hid`,
4. copy `boot.py`,
5. copy `code.py`,
6. copy `config.py`,
7. sync the drive.

Explicit mount:

```bash
./scripts/install.sh /Volumes/CIRCUITPY
```

After `boot.py` changes, unplug/replug the board once.

## Tune behavior

Edit:

```text
CIRCUITPY/config.py
```

Key values:

```python
JOYSTICK_DEADZONE = 0.12
JOYSTICK_CURVE = 2.0
MOUSE_MAX_SPEED = 14
INVERT_X = False
INVERT_Y = True
```

On startup the stick center is auto-calibrated. Leave the joystick untouched
for about one second after boot/reset.

## Monitor EVF events

Install pyserial:

```bash
python3 -m pip install pyserial
```

Then:

```bash
python3 scripts/monitor.py
```

Or select a port:

```bash
python3 scripts/monitor.py /dev/cu.usbmodemXXXX
```

## Test on Raspberry Pi

```bash
chmod +x scripts/test_linux.sh
./scripts/test_linux.sh
```

For raw input events:

```bash
sudo evtest
```
