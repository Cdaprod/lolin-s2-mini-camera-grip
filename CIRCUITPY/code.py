"""
CDAProd Camera Grip Controller
==============================

Target:
    LOLIN S2 Mini / ESP32-S2
    CircuitPython

USB interfaces:
    - USB HID mouse
    - USB CDC data channel using newline-delimited JSON

Hardware:
    IO1  <- joystick VRX
    IO2  <- joystick VRY
    IO3  <- joystick SW
    IO4  <- index button
    IO5  <- middle button
    IO6  <- ring button

    3V3  -> joystick VCC
    GND  -> joystick + button common ground


Architecture
------------

Physical Inputs
      |
      v
Normalization / Debounce
      |
      v
Gesture Engine
      |
      v
Semantic Event Router
      |
      +--------------------+
      |                    |
      v                    v
 USB HID Mouse        USB CDC JSON
      |                    ^
      |                    |
      +------ Host --------+


Profiles
--------

generic / mouse
    Joystick  -> mouse pointer
    JOY       -> left click
    INDEX     -> left click
    MIDDLE    -> right click
    RING      -> double left click

evf
    Joystick  -> axis events over CDC
    JOY       -> select
    INDEX     -> shutter
    MIDDLE    -> back
    RING      -> focus_assist

camera
    Joystick  -> axis events
    JOY       -> select
    INDEX     -> shutter
    MIDDLE    -> autofocus
    RING      -> record

editor
    Joystick  -> axis events
    JOY       -> select
    INDEX     -> capture
    MIDDLE    -> back/context
    RING      -> record


Host protocol
-------------

Device -> host examples:

    {"type":"ready", ...}

    {"type":"axis","x":0.1,"y":-0.5}

    {"type":"button","name":"index","event":"pressed"}

    {"type":"gesture","name":"index","gesture":"hold"}

    {"type":"action","action":"shutter","event":"pressed"}

    {"type":"profile","profile":"evf"}


Host -> device examples:

    {"cmd":"hello","client":"pi5-evf","profile":"evf"}

    {"cmd":"set_profile","profile":"editor"}

    {"cmd":"get_profile"}

    {"cmd":"get_status"}

    {"cmd":"calibrate"}

    {"cmd":"set_sensitivity","value":18}

    {"cmd":"set_deadzone","value":0.15}

    {"cmd":"ping"}


Manual override
---------------

Hold JOY + RING for MODE_CHORD_SECONDS.

This cycles:

    generic -> evf -> camera -> editor -> generic


Design principle
----------------

The grip always remains useful as a generic USB HID mouse.

A CDAProd-aware host can connect over CDC and request a richer profile.
If the host disappears, the grip can automatically return to generic HID.
"""

import json
import time

import analogio
import board
import digitalio
import usb_cdc
import usb_hid

from adafruit_hid.mouse import Mouse

import config


# ===========================================================================
# VERSION
# ===========================================================================

DEVICE_NAME = "cdaprod-camera-grip"
FIRMWARE_VERSION = "2.0.0"
PROTOCOL_VERSION = 2


# ===========================================================================
# SAFE CONFIG ACCESS
# ===========================================================================
#
# These helpers allow this code.py to remain compatible with the config.py
# you already have.
#
# New settings therefore do NOT have to exist in config.py immediately.
# ===========================================================================


def cfg(name, default):
    return getattr(config, name, default)


DEBUG_CONSOLE = cfg("DEBUG_CONSOLE", True)

DEBOUNCE_SECONDS = cfg("DEBOUNCE_SECONDS", 0.025)

CALIBRATION_SAMPLES = cfg("CALIBRATION_SAMPLES", 40)
CALIBRATION_SAMPLE_DELAY = cfg("CALIBRATION_SAMPLE_DELAY", 0.005)

JOYSTICK_DEADZONE = cfg("JOYSTICK_DEADZONE", 0.12)
JOYSTICK_CURVE = cfg("JOYSTICK_CURVE", 1.5)

INVERT_X = cfg("INVERT_X", False)
INVERT_Y = cfg("INVERT_Y", False)

MOUSE_MAX_SPEED = cfg("MOUSE_MAX_SPEED", 12)

DOUBLE_CLICK_DELAY = cfg("DOUBLE_CLICK_DELAY", 0.08)

EVF_AXIS_EPSILON = cfg("EVF_AXIS_EPSILON", 0.025)
EVF_AXIS_INTERVAL = cfg("EVF_AXIS_INTERVAL", 0.025)

MODE_CHORD_SECONDS = cfg("MODE_CHORD_SECONDS", 1.0)

POLL_INTERVAL = cfg("POLL_INTERVAL", 0.005)

BUTTON_HOLD_SECONDS = cfg("BUTTON_HOLD_SECONDS", 0.65)

BUTTON_DOUBLE_SECONDS = cfg("BUTTON_DOUBLE_SECONDS", 0.30)

HOST_TIMEOUT_SECONDS = cfg("HOST_TIMEOUT_SECONDS", 5.0)

AUTO_GENERIC_ON_HOST_TIMEOUT = cfg(
    "AUTO_GENERIC_ON_HOST_TIMEOUT",
    True,
)

DEFAULT_PROFILE = cfg(
    "DEFAULT_PROFILE",
    cfg("DEFAULT_MODE", "mouse"),
)


# ===========================================================================
# UTILITIES
# ===========================================================================


def clamp(value, lo, hi):
    if value < lo:
        return lo

    if value > hi:
        return hi

    return value


def console(*items):
    if DEBUG_CONSOLE:
        print(*items)


def now_ms():
    return int(time.monotonic() * 1000)


# ===========================================================================
# USB
# ===========================================================================

mouse = Mouse(usb_hid.devices)

serial_data = usb_cdc.data


# ===========================================================================
# CDC TRANSPORT
# ===========================================================================

rx_buffer = bytearray()


def emit(payload):
    """
    Send one newline-delimited JSON object to the host.
    """

    if serial_data is None:
        return False

    try:
        encoded = (
            json.dumps(
                payload,
                separators=(",", ":"),
            )
            + "\n"
        ).encode("utf-8")

        serial_data.write(encoded)

        return True

    except Exception as exc:
        console("CDC write failed:", exc)
        return False


def emit_error(message, cmd=None):
    payload = {
        "type": "error",
        "message": message,
    }

    if cmd is not None:
        payload["cmd"] = cmd

    emit(payload)


def emit_ack(cmd, **extra):
    payload = {
        "type": "ack",
        "cmd": cmd,
    }

    for key, value in extra.items():
        payload[key] = value

    emit(payload)


def read_serial_messages():
    """
    Non-blocking CDC reader.

    Returns a list of decoded JSON dictionaries.

    Protocol framing:
        one JSON object per newline
    """

    global rx_buffer

    messages = []

    if serial_data is None:
        return messages

    try:
        waiting = serial_data.in_waiting

        if waiting:
            chunk = serial_data.read(waiting)

            if chunk:
                rx_buffer.extend(chunk)

    except Exception as exc:
        console("CDC read failed:", exc)
        return messages

    while True:
        try:
            newline_index = rx_buffer.index(10)
        except ValueError:
            break

        raw = rx_buffer[:newline_index]

        del rx_buffer[: newline_index + 1]

        raw = raw.strip()

        if not raw:
            continue

        try:
            text = raw.decode("utf-8")

            message = json.loads(text)

            if isinstance(message, dict):
                messages.append(message)
            else:
                emit_error("JSON message must be an object")

        except Exception as exc:
            console("Bad CDC message:", raw, exc)

            emit_error("invalid_json")

    return messages


# ===========================================================================
# DIGITAL INPUT
# ===========================================================================


def make_input(pin):
    obj = digitalio.DigitalInOut(pin)

    obj.direction = digitalio.Direction.INPUT
    obj.pull = digitalio.Pull.UP

    return obj


# ===========================================================================
# BUTTON / GESTURE ENGINE
# ===========================================================================


class DebouncedButton:
    """
    Debounced active-low button with gesture state.

    Events:
        pressed
        released
        hold
        double
    """

    def __init__(self, pin, name):
        self.pin = pin
        self.name = name

        now = time.monotonic()

        current = self.read_raw()

        self.raw_state = current
        self.stable_state = current

        self.last_raw_change = now

        self.press_started = None

        self.hold_emitted = False

        self.last_release_time = None

        self.pending_single = False

    def read_raw(self):
        # Pull-up:
        # LOW == pressed
        return not self.pin.value

    @property
    def pressed(self):
        return self.stable_state

    def update(self, now):
        events = []

        current = self.read_raw()

        # ---------------------------------------------------------------
        # Raw transition
        # ---------------------------------------------------------------

        if current != self.raw_state:
            self.raw_state = current
            self.last_raw_change = now

        # ---------------------------------------------------------------
        # Debounced transition
        # ---------------------------------------------------------------

        if (
            current != self.stable_state
            and
            (now - self.last_raw_change) >= DEBOUNCE_SECONDS
        ):
            self.stable_state = current

            if current:
                self.press_started = now
                self.hold_emitted = False

                # Detect second press.
                if (
                    self.last_release_time is not None
                    and
                    (now - self.last_release_time)
                    <= BUTTON_DOUBLE_SECONDS
                ):
                    self.pending_single = False

                    events.append("double")

                events.append("pressed")

            else:
                events.append("released")

                self.last_release_time = now

                self.press_started = None
                self.hold_emitted = False

        # ---------------------------------------------------------------
        # Hold
        # ---------------------------------------------------------------

        if (
            self.stable_state
            and
            self.press_started is not None
            and
            not self.hold_emitted
            and
            (now - self.press_started) >= BUTTON_HOLD_SECONDS
        ):
            self.hold_emitted = True

            events.append("hold")

        return events


# ===========================================================================
# HARDWARE
# ===========================================================================

joy_x_adc = analogio.AnalogIn(board.IO1)
joy_y_adc = analogio.AnalogIn(board.IO2)


buttons = {
    "joy": DebouncedButton(
        make_input(board.IO3),
        "joy",
    ),

    "index": DebouncedButton(
        make_input(board.IO4),
        "index",
    ),

    "middle": DebouncedButton(
        make_input(board.IO5),
        "middle",
    ),

    "ring": DebouncedButton(
        make_input(board.IO6),
        "ring",
    ),
}


# ===========================================================================
# JOYSTICK CALIBRATION
# ===========================================================================


def calibrate_center(announce=True):
    if announce:
        console(
            "Calibrating joystick center; "
            "do not touch stick..."
        )

    sx = 0
    sy = 0

    for _ in range(CALIBRATION_SAMPLES):
        sx += joy_x_adc.value
        sy += joy_y_adc.value

        time.sleep(CALIBRATION_SAMPLE_DELAY)

    cx = sx / CALIBRATION_SAMPLES
    cy = sy / CALIBRATION_SAMPLES

    if announce:
        console(
            "Joystick center:",
            int(cx),
            int(cy),
        )

    return cx, cy


CENTER_X, CENTER_Y = calibrate_center()


# ===========================================================================
# JOYSTICK NORMALIZATION
# ===========================================================================

runtime_deadzone = JOYSTICK_DEADZONE
runtime_mouse_speed = MOUSE_MAX_SPEED


def normalize(raw, center):
    if raw >= center:

        denom = max(
            1.0,
            65535.0 - center,
        )

        value = (
            raw - center
        ) / denom

    else:

        denom = max(
            1.0,
            center,
        )

        value = (
            raw - center
        ) / denom

    return clamp(
        value,
        -1.0,
        1.0,
    )


def apply_deadzone(value):
    dz = runtime_deadzone

    magnitude = abs(value)

    if magnitude <= dz:
        return 0.0

    scaled = (
        magnitude - dz
    ) / (
        1.0 - dz
    )

    if value < 0:
        return -scaled

    return scaled


def apply_curve(value):
    if value == 0.0:
        return 0.0

    magnitude = (
        abs(value)
        ** JOYSTICK_CURVE
    )

    if value < 0:
        return -magnitude

    return magnitude


def read_joystick():
    x = normalize(
        joy_x_adc.value,
        CENTER_X,
    )

    y = normalize(
        joy_y_adc.value,
        CENTER_Y,
    )

    x = apply_deadzone(x)
    y = apply_deadzone(y)

    x = apply_curve(x)
    y = apply_curve(y)

    if INVERT_X:
        x = -x

    if INVERT_Y:
        y = -y

    return (
        clamp(x, -1.0, 1.0),
        clamp(y, -1.0, 1.0),
    )


# ===========================================================================
# PROFILE SYSTEM
# ===========================================================================

PROFILE_GENERIC = "generic"
PROFILE_EVF = "evf"
PROFILE_CAMERA = "camera"
PROFILE_EDITOR = "editor"


PROFILE_ORDER = (
    PROFILE_GENERIC,
    PROFILE_EVF,
    PROFILE_CAMERA,
    PROFILE_EDITOR,
)


PROFILE_ALIASES = {
    "mouse": PROFILE_GENERIC,
    "generic": PROFILE_GENERIC,

    "evf": PROFILE_EVF,

    "camera": PROFILE_CAMERA,

    "editor": PROFILE_EDITOR,
}


def normalize_profile(profile):
    if profile is None:
        return None

    profile = str(profile).lower()

    return PROFILE_ALIASES.get(profile)


initial_profile = normalize_profile(DEFAULT_PROFILE)

if initial_profile is None:
    initial_profile = PROFILE_GENERIC


profile = initial_profile


def set_profile(new_profile, source="local"):
    global profile

    normalized = normalize_profile(new_profile)

    if normalized is None:
        return False

    if normalized == profile:
        return True

    old_profile = profile

    profile = normalized

    console(
        "PROFILE:",
        old_profile,
        "->",
        profile,
        "(" + source + ")",
    )

    emit({
        "type": "profile",
        "profile": profile,
        "previous": old_profile,
        "source": source,
    })

    return True


def cycle_profile():
    try:
        index = PROFILE_ORDER.index(profile)
    except ValueError:
        index = 0

    index += 1

    if index >= len(PROFILE_ORDER):
        index = 0

    set_profile(
        PROFILE_ORDER[index],
        source="manual_chord",
    )


# ===========================================================================
# SEMANTIC ACTION MAPS
# ===========================================================================

ACTION_MAP = {

    PROFILE_EVF: {
        "joy": "select",
        "index": "shutter",
        "middle": "back",
        "ring": "focus_assist",
    },

    PROFILE_CAMERA: {
        "joy": "select",
        "index": "shutter",
        "middle": "autofocus",
        "ring": "record",
    },

    PROFILE_EDITOR: {
        "joy": "select",
        "index": "capture",
        "middle": "back",
        "ring": "record",
    },
}


def action_for_button(button_name):
    mapping = ACTION_MAP.get(profile)

    if mapping is None:
        return None

    return mapping.get(button_name)


# ===========================================================================
# HOST STATE
# ===========================================================================

host_connected = False
host_client = None
host_last_seen = None


def mark_host_seen(client=None):
    global host_connected
    global host_client
    global host_last_seen

    was_connected = host_connected

    host_connected = True
    host_last_seen = time.monotonic()

    if client is not None:
        host_client = str(client)

    if not was_connected:
        console(
            "Host connected:",
            host_client,
        )

        emit({
            "type": "host",
            "event": "connected",
            "client": host_client,
        })


def check_host_timeout(now):
    global host_connected
    global host_client
    global host_last_seen

    if not host_connected:
        return

    if host_last_seen is None:
        return

    if (
        now - host_last_seen
        <= HOST_TIMEOUT_SECONDS
    ):
        return

    old_client = host_client

    host_connected = False
    host_client = None
    host_last_seen = None

    console(
        "Host timeout:",
        old_client,
    )

    emit({
        "type": "host",
        "event": "timeout",
        "client": old_client,
    })

    if (
        AUTO_GENERIC_ON_HOST_TIMEOUT
        and
        profile != PROFILE_GENERIC
    ):
        set_profile(
            PROFILE_GENERIC,
            source="host_timeout",
        )


# ===========================================================================
# DEVICE STATUS
# ===========================================================================


def status_payload():
    return {
        "type": "status",

        "device": DEVICE_NAME,

        "firmware": FIRMWARE_VERSION,

        "protocol": PROTOCOL_VERSION,

        "profile": profile,

        "host_connected": host_connected,

        "host_client": host_client,

        "joystick": {
            "center_x": int(CENTER_X),
            "center_y": int(CENTER_Y),
            "deadzone": runtime_deadzone,
            "curve": JOYSTICK_CURVE,
            "mouse_speed": runtime_mouse_speed,
        },

        "buttons": {
            name: button.pressed
            for name, button in buttons.items()
        },

        "uptime_ms": now_ms(),
    }


# ===========================================================================
# HOST COMMAND PROCESSOR
# ===========================================================================


def process_host_command(message):
    global CENTER_X
    global CENTER_Y

    global runtime_deadzone
    global runtime_mouse_speed

    cmd = message.get("cmd")

    if cmd is None:
        emit_error(
            "missing_cmd",
        )
        return

    cmd = str(cmd).lower()

    client = message.get("client")

    mark_host_seen(client)

    # ------------------------------------------------------------------
    # HELLO / HANDSHAKE
    # ------------------------------------------------------------------

    if cmd == "hello":

        requested_profile = message.get(
            "profile"
        )

        if requested_profile is not None:

            if not set_profile(
                requested_profile,
                source="host",
            ):
                emit_error(
                    "invalid_profile",
                    cmd,
                )
                return

        emit({
            "type": "hello",

            "device": DEVICE_NAME,

            "firmware": FIRMWARE_VERSION,

            "protocol": PROTOCOL_VERSION,

            "profile": profile,

            "profiles": list(PROFILE_ORDER),

            "client": host_client,
        })

        return

    # ------------------------------------------------------------------
    # PING
    # ------------------------------------------------------------------

    if cmd == "ping":

        emit({
            "type": "pong",
            "uptime_ms": now_ms(),
            "profile": profile,
        })

        return

    # ------------------------------------------------------------------
    # GET STATUS
    # ------------------------------------------------------------------

    if cmd == "get_status":

        emit(status_payload())

        return

    # ------------------------------------------------------------------
    # GET PROFILE
    # ------------------------------------------------------------------

    if cmd == "get_profile":

        emit({
            "type": "profile",
            "profile": profile,
        })

        return

    # ------------------------------------------------------------------
    # SET PROFILE
    # ------------------------------------------------------------------

    if cmd == "set_profile":

        requested = message.get(
            "profile"
        )

        if requested is None:
            emit_error(
                "missing_profile",
                cmd,
            )
            return

        if not set_profile(
            requested,
            source="host",
        ):
            emit_error(
                "invalid_profile",
                cmd,
            )
            return

        emit_ack(
            cmd,
            profile=profile,
        )

        return

    # ------------------------------------------------------------------
    # CALIBRATE
    # ------------------------------------------------------------------

    if cmd == "calibrate":

        emit({
            "type": "calibration",
            "event": "started",
        })

        CENTER_X, CENTER_Y = calibrate_center()

        emit({
            "type": "calibration",
            "event": "complete",
            "center_x": int(CENTER_X),
            "center_y": int(CENTER_Y),
        })

        return

    # ------------------------------------------------------------------
    # SET DEADZONE
    # ------------------------------------------------------------------

    if cmd == "set_deadzone":

        value = message.get(
            "value"
        )

        try:
            value = float(value)
        except Exception:
            emit_error(
                "invalid_deadzone",
                cmd,
            )
            return

        value = clamp(
            value,
            0.0,
            0.95,
        )

        runtime_deadzone = value

        emit_ack(
            cmd,
            value=runtime_deadzone,
        )

        return

    # ------------------------------------------------------------------
    # SET MOUSE SENSITIVITY
    # ------------------------------------------------------------------

    if cmd == "set_sensitivity":

        value = message.get(
            "value"
        )

        try:
            value = int(value)
        except Exception:
            emit_error(
                "invalid_sensitivity",
                cmd,
            )
            return

        runtime_mouse_speed = int(
            clamp(
                value,
                1,
                127,
            )
        )

        emit_ack(
            cmd,
            value=runtime_mouse_speed,
        )

        return

    # ------------------------------------------------------------------
    # UNKNOWN COMMAND
    # ------------------------------------------------------------------

    emit_error(
        "unknown_command",
        cmd,
    )


# ===========================================================================
# GENERIC HID PROFILE
# ===========================================================================


def generic_mouse_axis(x, y):
    dx = int(
        x * runtime_mouse_speed
    )

    dy = int(
        y * runtime_mouse_speed
    )

    if dx != 0 or dy != 0:
        try:
            mouse.move(
                x=dx,
                y=dy,
            )
        except Exception as exc:
            console(
                "Mouse move failed:",
                exc,
            )


def generic_button_action(
    name,
    event,
):
    """
    Preserve the original grip behavior.

    Only the initial press performs HID action.
    """

    if event != "pressed":
        return

    try:

        if name in (
            "joy",
            "index",
        ):

            mouse.click(
                Mouse.LEFT_BUTTON
            )

        elif name == "middle":

            mouse.click(
                Mouse.RIGHT_BUTTON
            )

        elif name == "ring":

            mouse.click(
                Mouse.LEFT_BUTTON
            )

            time.sleep(
                DOUBLE_CLICK_DELAY
            )

            mouse.click(
                Mouse.LEFT_BUTTON
            )

    except Exception as exc:
        console(
            "Mouse button failed:",
            exc,
        )


# ===========================================================================
# AXIS EVENT ROUTER
# ===========================================================================

last_axis_x = None
last_axis_y = None
last_axis_emit = 0.0


def emit_axis_if_needed(
    x,
    y,
    now,
):
    global last_axis_x
    global last_axis_y
    global last_axis_emit

    changed = (
        last_axis_x is None
        or
        abs(x - last_axis_x)
        >= EVF_AXIS_EPSILON
        or
        abs(y - last_axis_y)
        >= EVF_AXIS_EPSILON
    )

    returned_to_center = (
        x == 0.0
        and
        y == 0.0
        and
        (
            last_axis_x
            not in (
                None,
                0.0,
            )
            or
            last_axis_y
            not in (
                None,
                0.0,
            )
        )
    )

    due = (
        now - last_axis_emit
    ) >= EVF_AXIS_INTERVAL

    if (
        due
        and
        (
            changed
            or
            returned_to_center
        )
    ):

        emit({
            "type": "axis",

            "profile": profile,

            "x": round(
                x,
                4,
            ),

            "y": round(
                y,
                4,
            ),
        })

        last_axis_x = x
        last_axis_y = y
        last_axis_emit = now


# ===========================================================================
# BUTTON EVENT ROUTER
# ===========================================================================


def emit_button_event(
    name,
    event,
):
    emit({
        "type": "button",
        "profile": profile,
        "name": name,
        "event": event,
    })


def emit_gesture(
    name,
    gesture,
):
    emit({
        "type": "gesture",
        "profile": profile,
        "name": name,
        "gesture": gesture,
    })


def emit_action(
    name,
    action,
    event,
):
    emit({
        "type": "action",
        "profile": profile,
        "input": name,
        "action": action,
        "event": event,
    })


def route_button_event(
    name,
    event,
):
    # ------------------------------------------------------------------
    # GENERIC HID
    # ------------------------------------------------------------------

    if profile == PROFILE_GENERIC:

        generic_button_action(
            name,
            event,
        )

        # Still expose physical events over CDC.
        emit_button_event(
            name,
            event,
        )

        if event in (
            "hold",
            "double",
        ):
            emit_gesture(
                name,
                event,
            )

        return

    # ------------------------------------------------------------------
    # CDA semantic profiles
    # ------------------------------------------------------------------

    emit_button_event(
        name,
        event,
    )

    if event in (
        "hold",
        "double",
    ):
        emit_gesture(
            name,
            event,
        )

    action = action_for_button(
        name
    )

    if action is not None:
        emit_action(
            name,
            action,
            event,
        )


# ===========================================================================
# MODE / PROFILE CHORD
# ===========================================================================

mode_chord_started = None
mode_chord_latched = False


def update_mode_chord(now):
    global mode_chord_started
    global mode_chord_latched

    active = (
        buttons["joy"].pressed
        and
        buttons["ring"].pressed
    )

    if active:

        if mode_chord_started is None:

            mode_chord_started = now
            mode_chord_latched = False

        elif (
            not mode_chord_latched
            and
            now - mode_chord_started
            >= MODE_CHORD_SECONDS
        ):

            mode_chord_latched = True

            cycle_profile()

    else:

        mode_chord_started = None
        mode_chord_latched = False


def mode_chord_active():
    return (
        buttons["joy"].pressed
        and
        buttons["ring"].pressed
    )


# ===========================================================================
# STARTUP
# ===========================================================================

console()
console(
    "========================================"
)
console(
    "CDAProd Camera Grip Controller"
)
console(
    "LOLIN S2 Mini / ESP32-S2"
)
console(
    "Firmware:",
    FIRMWARE_VERSION,
)
console(
    "Protocol:",
    PROTOCOL_VERSION,
)
console(
    "Profile:",
    profile.upper(),
)
console(
    "Hold JOY + RING to cycle profiles."
)
console(
    "========================================"
)
console()


emit({
    "type": "ready",

    "device": DEVICE_NAME,

    "firmware": FIRMWARE_VERSION,

    "protocol": PROTOCOL_VERSION,

    "profile": profile,

    "profiles": list(PROFILE_ORDER),

    "capabilities": [
        "hid_mouse",
        "cdc_json",
        "joystick",
        "buttons",
        "gestures",
        "semantic_actions",
        "host_profiles",
        "runtime_calibration",
        "runtime_deadzone",
        "runtime_sensitivity",
    ],
})


# ===========================================================================
# MAIN LOOP
# ===========================================================================

while True:

    now = time.monotonic()

    # ------------------------------------------------------------------
    # HOST -> GRIP
    # ------------------------------------------------------------------

    incoming_messages = (
        read_serial_messages()
    )

    for message in incoming_messages:

        try:
            process_host_command(
                message
            )

        except Exception as exc:

            console(
                "Host command error:",
                exc,
            )

            emit_error(
                "command_processing_failed"
            )

    # ------------------------------------------------------------------
    # HOST WATCHDOG
    # ------------------------------------------------------------------

    check_host_timeout(now)

    # ------------------------------------------------------------------
    # BUTTON SCAN
    # ------------------------------------------------------------------

    button_events = []

    for name, button in buttons.items():

        events = button.update(now)

        for event in events:

            button_events.append(
                (
                    name,
                    event,
                )
            )

    # ------------------------------------------------------------------
    # MANUAL PROFILE CHORD
    # ------------------------------------------------------------------

    update_mode_chord(now)

    chord_active = (
        mode_chord_active()
    )

    # ------------------------------------------------------------------
    # JOYSTICK
    # ------------------------------------------------------------------

    x, y = read_joystick()

    if profile == PROFILE_GENERIC:

        generic_mouse_axis(
            x,
            y,
        )

    else:

        emit_axis_if_needed(
            x,
            y,
            now,
        )

    # ------------------------------------------------------------------
    # BUTTON ROUTING
    # ------------------------------------------------------------------

    for name, event in button_events:

        # JOY + RING is reserved while the profile chord
        # is physically active.
        #
        # Prevent accidental click/action generation while
        # changing modes.

        if (
            chord_active
            and
            name in (
                "joy",
                "ring",
            )
        ):
            continue

        console(
            "BUTTON",
            name,
            event,
            "PROFILE",
            profile,
        )

        route_button_event(
            name,
            event,
        )

    # ------------------------------------------------------------------
    # LOOP THROTTLE
    # ------------------------------------------------------------------

    time.sleep(
        POLL_INTERVAL
    )