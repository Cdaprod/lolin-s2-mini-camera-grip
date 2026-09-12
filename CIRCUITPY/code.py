\
import json
import time

import analogio
import board
import digitalio
import usb_cdc
import usb_hid

from adafruit_hid.mouse import Mouse
import config


def clamp(v, lo, hi):
    return lo if v < lo else hi if v > hi else v


def make_input(pin):
    obj = digitalio.DigitalInOut(pin)
    obj.direction = digitalio.Direction.INPUT
    obj.pull = digitalio.Pull.UP
    return obj


class DebouncedButton:
    def __init__(self, pin, name):
        self.pin = pin
        self.name = name
        now = time.monotonic()
        current = self.read_raw()
        self.raw_state = current
        self.stable_state = current
        self.last_raw_change = now

    def read_raw(self):
        # Pull-up: LOW means pressed.
        return not self.pin.value

    def update(self, now):
        current = self.read_raw()

        if current != self.raw_state:
            self.raw_state = current
            self.last_raw_change = now

        if (
            current != self.stable_state
            and (now - self.last_raw_change) >= config.DEBOUNCE_SECONDS
        ):
            self.stable_state = current
            return "pressed" if current else "released"

        return None

    @property
    def pressed(self):
        return self.stable_state


mouse = Mouse(usb_hid.devices)
serial_data = usb_cdc.data


def console(*items):
    if config.DEBUG_CONSOLE:
        print(*items)


def emit(payload):
    if serial_data is None:
        return
    try:
        serial_data.write(
            (json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8")
        )
    except Exception as exc:
        console("CDC write failed:", exc)


# ---------------------------------------------------------------------------
# Hardware
# ---------------------------------------------------------------------------
# IO1  <- joystick VRX
# IO2  <- joystick VRY
# IO3  <- joystick SW
# IO4  <- index button
# IO5  <- middle button
# IO6  <- ring button
# 3V3  -> joystick VCC
# GND  -> joystick + button common ground

joy_x_adc = analogio.AnalogIn(board.IO1)
joy_y_adc = analogio.AnalogIn(board.IO2)

buttons = {
    "joy": DebouncedButton(make_input(board.IO3), "joy"),
    "index": DebouncedButton(make_input(board.IO4), "index"),
    "middle": DebouncedButton(make_input(board.IO5), "middle"),
    "ring": DebouncedButton(make_input(board.IO6), "ring"),
}


def calibrate_center():
    console("Calibrating joystick center; do not touch stick...")
    sx = 0
    sy = 0
    for _ in range(config.CALIBRATION_SAMPLES):
        sx += joy_x_adc.value
        sy += joy_y_adc.value
        time.sleep(config.CALIBRATION_SAMPLE_DELAY)

    cx = sx / config.CALIBRATION_SAMPLES
    cy = sy / config.CALIBRATION_SAMPLES
    console("Joystick center:", int(cx), int(cy))
    return cx, cy


CENTER_X, CENTER_Y = calibrate_center()


def normalize(raw, center):
    if raw >= center:
        denom = max(1.0, 65535.0 - center)
        value = (raw - center) / denom
    else:
        denom = max(1.0, center)
        value = (raw - center) / denom
    return clamp(value, -1.0, 1.0)


def deadzone(value):
    dz = config.JOYSTICK_DEADZONE
    mag = abs(value)
    if mag <= dz:
        return 0.0

    scaled = (mag - dz) / (1.0 - dz)
    return -scaled if value < 0 else scaled


def curve(value):
    if value == 0.0:
        return 0.0
    mag = abs(value) ** config.JOYSTICK_CURVE
    return -mag if value < 0 else mag


def read_joystick():
    x = curve(deadzone(normalize(joy_x_adc.value, CENTER_X)))
    y = curve(deadzone(normalize(joy_y_adc.value, CENTER_Y)))

    if config.INVERT_X:
        x = -x
    if config.INVERT_Y:
        y = -y

    return clamp(x, -1.0, 1.0), clamp(y, -1.0, 1.0)


mode = config.DEFAULT_MODE.lower()
if mode not in ("mouse", "evf"):
    mode = "mouse"


def set_mode(new_mode):
    global mode
    if new_mode not in ("mouse", "evf") or new_mode == mode:
        return
    mode = new_mode
    console("MODE:", mode.upper())
    emit({"type": "mode", "mode": mode})


def toggle_mode():
    set_mode("evf" if mode == "mouse" else "mouse")


def mouse_button_action(name, event):
    if event != "pressed":
        return

    if name in ("joy", "index"):
        mouse.click(Mouse.LEFT_BUTTON)
    elif name == "middle":
        mouse.click(Mouse.RIGHT_BUTTON)
    elif name == "ring":
        mouse.click(Mouse.LEFT_BUTTON)
        time.sleep(config.DOUBLE_CLICK_DELAY)
        mouse.click(Mouse.LEFT_BUTTON)


last_axis_x = None
last_axis_y = None
last_axis_emit = 0.0


def evf_axis_event(x, y, now):
    global last_axis_x, last_axis_y, last_axis_emit

    changed = (
        last_axis_x is None
        or abs(x - last_axis_x) >= config.EVF_AXIS_EPSILON
        or abs(y - last_axis_y) >= config.EVF_AXIS_EPSILON
    )
    returned = (
        x == 0.0
        and y == 0.0
        and (last_axis_x not in (None, 0.0) or last_axis_y not in (None, 0.0))
    )
    due = (now - last_axis_emit) >= config.EVF_AXIS_INTERVAL

    if due and (changed or returned):
        emit({"type": "axis", "x": round(x, 4), "y": round(y, 4)})
        last_axis_x = x
        last_axis_y = y
        last_axis_emit = now


mode_chord_started = None
mode_chord_latched = False


def update_mode_chord(now):
    global mode_chord_started, mode_chord_latched

    active = buttons["joy"].pressed and buttons["ring"].pressed

    if active:
        if mode_chord_started is None:
            mode_chord_started = now
            mode_chord_latched = False
        elif (
            not mode_chord_latched
            and now - mode_chord_started >= config.MODE_CHORD_SECONDS
        ):
            mode_chord_latched = True
            toggle_mode()
    else:
        mode_chord_started = None
        mode_chord_latched = False


console()
console("CDAProd Camera Grip Controller")
console("LOLIN S2 Mini / ESP32-S2")
console("Mode:", mode.upper())
console("Hold JOY + RING for 1 second to toggle MOUSE/EVF.")
emit({"type": "ready", "device": "cdaprod-camera-grip", "mode": mode})


while True:
    now = time.monotonic()
    events = []

    for name, button in buttons.items():
        event = button.update(now)
        if event is not None:
            events.append((name, event))

    update_mode_chord(now)

    x, y = read_joystick()

    if mode == "mouse":
        dx = int(x * config.MOUSE_MAX_SPEED)
        dy = int(y * config.MOUSE_MAX_SPEED)
        if dx != 0 or dy != 0:
            mouse.move(x=dx, y=dy)
    else:
        evf_axis_event(x, y, now)

    chord_active = buttons["joy"].pressed and buttons["ring"].pressed

    for name, event in events:
        if chord_active and name in ("joy", "ring"):
            continue

        console("BUTTON", name, event)

        if mode == "mouse":
            mouse_button_action(name, event)
        else:
            emit({"type": "button", "name": name, "event": event})

    time.sleep(config.POLL_INTERVAL)
