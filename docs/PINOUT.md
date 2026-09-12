# Camera Grip Pinout

```text
LOLIN S2 MINI
=============

IO1   joystick VRX
IO2   joystick VRY
IO3   joystick SW

IO4   index button
IO5   middle button
IO6   ring button

3V3   joystick power
GND   common ground
```

## Finger boards

```text
IO4 ----[ INDEX ]-----+
IO5 ----[ MIDDLE ]----+---- GND
IO6 ----[ RING ]------+ 
```

The three-button harness can therefore use four conductors:

```text
INDEX
MIDDLE
RING
GND
```

## Joystick

```text
JOYSTICK     S2 MINI
--------     -------
GND       -> GND
VCC/+5V   -> 3V3
VRX       -> IO1
VRY       -> IO2
SW        -> IO3
```
