"""Virtual Xbox 360 controller on Windows, through the ViGEmBus driver (vgamepad)."""

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
            "Padthru needs the free ViGEmBus driver to create a virtual Xbox controller, "
            "and it isn't installed (or isn't running).\n\n"
            "Install it, restart Windows if asked, then start Padthru again. (%r)" % (_import_error,))


def describe():
    return "ViGEmBus"


class VirtualPad:
    """One virtual Xbox 360 controller."""

    def __init__(self, on_rumble):
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
        return "ViGEm X360"

    def set_state(self, buttons, lt, rt, lx, ly, rx, ry):
        """buttons: set of names; triggers 0..1; sticks -1..1 with +y pointing down
        (browser Gamepad API convention; XInput's +y points up)."""
        pad = self.pad
        pad.reset()
        for name in buttons:
            pad.press_button(button=self.buttons[name])
        pad.left_trigger_float(value_float=lt)
        pad.right_trigger_float(value_float=rt)
        pad.left_joystick_float(x_value_float=lx, y_value_float=-ly)
        pad.right_joystick_float(x_value_float=rx, y_value_float=-ry)
        pad.update()

    def close(self):
        try:
            self.pad.reset()
            self.pad.update()
        except Exception:  # noqa: BLE001
            pass
