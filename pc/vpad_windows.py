"""Virtual controllers on Windows, through the ViGEmBus driver (vgamepad).

Two kinds: a wired Xbox 360 controller ("xbox") or a wired DualShock 4
("playstation"), which games and Steam see as a real PlayStation 4 controller.
"""

VIGEMBUS_URL = "https://github.com/nefarius/ViGEmBus/releases/latest"

try:
    import vgamepad as vg  # connects to ViGEmBus on import
    _import_error = None
except Exception as e:  # noqa: BLE001
    vg = None
    _import_error = e


class BackendError(Exception):
    """A problem the user has to fix; the message says how."""

    help_url = VIGEMBUS_URL


def check():
    if vg is None:
        raise BackendError(
            "Padthru needs the free ViGEmBus driver to create a virtual controller, "
            "and it isn't installed (or isn't running).\n\n"
            "Install it, restart Windows if asked, then start Padthru again. (%r)" % (_import_error,))


def describe():
    return "ViGEmBus"


def _byte(v):
    """-1..1 -> 0..255 (128 = centre), the DualShock 4's stick range."""
    return int(round((v + 1.0) * 127.5))


class VirtualPad:
    """One virtual controller: kind "xbox" (Xbox 360) or "playstation" (DualShock 4)."""

    def __init__(self, on_rumble, kind="xbox"):
        self.kind = kind
        if kind == "playstation":
            b, s = vg.DS4_BUTTONS, vg.DS4_SPECIAL_BUTTONS
            d = vg.DS4_DPAD_DIRECTIONS
            self.buttons = {
                "A": b.DS4_BUTTON_CROSS, "B": b.DS4_BUTTON_CIRCLE,
                "X": b.DS4_BUTTON_SQUARE, "Y": b.DS4_BUTTON_TRIANGLE,
                "LB": b.DS4_BUTTON_SHOULDER_LEFT, "RB": b.DS4_BUTTON_SHOULDER_RIGHT,
                "BACK": b.DS4_BUTTON_SHARE, "START": b.DS4_BUTTON_OPTIONS,
                "LS": b.DS4_BUTTON_THUMB_LEFT, "RS": b.DS4_BUTTON_THUMB_RIGHT,
            }
            self.special = {"GUIDE": s.DS4_SPECIAL_BUTTON_PS, "TOUCHPAD": s.DS4_SPECIAL_BUTTON_TOUCHPAD}
            self.trigger_bits = (b.DS4_BUTTON_TRIGGER_LEFT, b.DS4_BUTTON_TRIGGER_RIGHT)
            # (up, down, left, right) -> hat direction
            self.hat = {
                (1, 0, 0, 0): d.DS4_BUTTON_DPAD_NORTH, (0, 1, 0, 0): d.DS4_BUTTON_DPAD_SOUTH,
                (0, 0, 1, 0): d.DS4_BUTTON_DPAD_WEST, (0, 0, 0, 1): d.DS4_BUTTON_DPAD_EAST,
                (1, 0, 0, 1): d.DS4_BUTTON_DPAD_NORTHEAST, (1, 0, 1, 0): d.DS4_BUTTON_DPAD_NORTHWEST,
                (0, 1, 0, 1): d.DS4_BUTTON_DPAD_SOUTHEAST, (0, 1, 1, 0): d.DS4_BUTTON_DPAD_SOUTHWEST,
            }
            self.pad = vg.VDS4Gamepad()

            def notification(client, target, large_motor, small_motor, lightbar_color, user_data):
                on_rumble(large_motor, small_motor)
        else:
            b = vg.XUSB_BUTTON
            self.buttons = {
                "A": b.XUSB_GAMEPAD_A, "B": b.XUSB_GAMEPAD_B, "X": b.XUSB_GAMEPAD_X, "Y": b.XUSB_GAMEPAD_Y,
                "LB": b.XUSB_GAMEPAD_LEFT_SHOULDER, "RB": b.XUSB_GAMEPAD_RIGHT_SHOULDER,
                "BACK": b.XUSB_GAMEPAD_BACK, "START": b.XUSB_GAMEPAD_START, "GUIDE": b.XUSB_GAMEPAD_GUIDE,
                "LS": b.XUSB_GAMEPAD_LEFT_THUMB, "RS": b.XUSB_GAMEPAD_RIGHT_THUMB,
                "UP": b.XUSB_GAMEPAD_DPAD_UP, "DOWN": b.XUSB_GAMEPAD_DPAD_DOWN,
                "LEFT": b.XUSB_GAMEPAD_DPAD_LEFT, "RIGHT": b.XUSB_GAMEPAD_DPAD_RIGHT,
            }
            self.pad = vg.VX360Gamepad()

            def notification(client, target, large_motor, small_motor, led_number, user_data):
                on_rumble(large_motor, small_motor)

        self.pad.register_notification(callback_function=notification)

    def name(self):
        return "ViGEm DS4" if self.kind == "playstation" else "ViGEm X360"

    def set_state(self, buttons, lt, rt, lx, ly, rx, ry):
        """buttons: set of names; triggers 0..1; sticks -1..1 with +y pointing down
        (browser Gamepad API convention, same as the DualShock 4; XInput's +y points up)."""
        pad = self.pad
        pad.reset()
        if self.kind == "playstation":
            for name in buttons:
                if name in self.buttons:
                    pad.press_button(button=self.buttons[name])
                elif name in self.special:
                    pad.press_special_button(special_button=self.special[name])
            hat = self.hat.get(tuple(int(n in buttons) for n in ("UP", "DOWN", "LEFT", "RIGHT")))
            if hat is not None:
                pad.directional_pad(direction=hat)
            # a real DualShock 4 also sets the digital L2/R2 bits as soon as a trigger moves
            for value, bit in zip((lt, rt), self.trigger_bits):
                if value > 0:
                    pad.press_button(button=bit)
            pad.left_trigger_float(value_float=lt)
            pad.right_trigger_float(value_float=rt)
            pad.left_joystick(x_value=_byte(lx), y_value=_byte(ly))
            pad.right_joystick(x_value=_byte(rx), y_value=_byte(ry))
        else:
            for name in buttons:
                pad.press_button(button=self.buttons["BACK" if name == "TOUCHPAD" else name])
            pad.left_trigger_float(value_float=lt)
            pad.right_trigger_float(value_float=rt)
            pad.left_joystick_float(x_value_float=lx, y_value_float=-ly)
            pad.right_joystick_float(x_value_float=rx, y_value_float=-ry)
        pad.update()

    def close(self):
        """Release everything and unplug the virtual controller."""
        try:
            self.pad.reset()
            self.pad.update()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.pad.unregister_notification()
        except Exception:  # noqa: BLE001
            pass
        try:
            del self.pad  # vgamepad unplugs the target when the object goes away
        except Exception:  # noqa: BLE001
            pass
