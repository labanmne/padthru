"""Virtual Xbox 360 controller on Linux, through the kernel's uinput module.

Pure Python, no compiled modules: it talks to /dev/uinput with ioctl() and
creates a device that looks exactly like a wired Xbox 360 controller does under
the kernel's own xpad driver (same IDs, buttons, axes and ranges). Steam, SDL
(most native games), Proton/Wine and browsers therefore treat it as one.

Rumble works the standard Linux way: games upload FF_RUMBLE effects to the
virtual device, and this module passes the motor strengths back to Padthru.
"""
import ctypes
import errno
import fcntl
import os
import select
import threading
import time

DEVICE = "/dev/uinput"

# What the xpad driver reports for a wired Xbox 360 controller. Games, SDL and
# Steam recognise the controller by these IDs (and some older games by name).
NAME = b"Microsoft X-Box 360 pad"
BUS_USB, VENDOR, PRODUCT, VERSION = 0x03, 0x045E, 0x028E, 0x0114

EV_SYN, EV_KEY, EV_ABS, EV_FF, EV_UINPUT = 0x00, 0x01, 0x03, 0x15, 0x0101
SYN_REPORT = 0
UI_FF_UPLOAD, UI_FF_ERASE = 1, 2
FF_RUMBLE, FF_GAIN = 0x50, 0x60
FF_EFFECTS_MAX = 16

KEYS = {
    "A": 0x130, "B": 0x131, "X": 0x133, "Y": 0x134,  # BTN_SOUTH, BTN_EAST, BTN_NORTH, BTN_WEST
    "LB": 0x136, "RB": 0x137, "BACK": 0x13A, "START": 0x13B, "GUIDE": 0x13C,
    "LS": 0x13D, "RS": 0x13E,
}
ABS_X, ABS_Y, ABS_Z, ABS_RX, ABS_RY, ABS_RZ, ABS_HAT0X, ABS_HAT0Y = 0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x10, 0x11
AXES = [  # code, minimum, maximum, fuzz, flat (xpad's values)
    (ABS_X, -32768, 32767, 16, 128), (ABS_Y, -32768, 32767, 16, 128),
    (ABS_RX, -32768, 32767, 16, 128), (ABS_RY, -32768, 32767, 16, 128),
    (ABS_Z, 0, 255, 0, 0), (ABS_RZ, 0, 255, 0, 0),
    (ABS_HAT0X, -1, 1, 0, 0), (ABS_HAT0Y, -1, 1, 0, 0),
]


# ------------------------------------------------------------- kernel structs

class input_id(ctypes.Structure):
    _fields_ = [("bustype", ctypes.c_uint16), ("vendor", ctypes.c_uint16),
                ("product", ctypes.c_uint16), ("version", ctypes.c_uint16)]


class uinput_setup(ctypes.Structure):
    _fields_ = [("id", input_id), ("name", ctypes.c_char * 80), ("ff_effects_max", ctypes.c_uint32)]


class input_absinfo(ctypes.Structure):
    _fields_ = [(n, ctypes.c_int32) for n in ("value", "minimum", "maximum", "fuzz", "flat", "resolution")]


class uinput_abs_setup(ctypes.Structure):
    _fields_ = [("code", ctypes.c_uint16), ("absinfo", input_absinfo)]


class timeval(ctypes.Structure):
    _fields_ = [("sec", ctypes.c_long), ("usec", ctypes.c_long)]


class input_event(ctypes.Structure):
    _fields_ = [("time", timeval), ("type", ctypes.c_uint16), ("code", ctypes.c_uint16), ("value", ctypes.c_int32)]


class ff_trigger(ctypes.Structure):
    _fields_ = [("button", ctypes.c_uint16), ("interval", ctypes.c_uint16)]


class ff_replay(ctypes.Structure):
    _fields_ = [("length", ctypes.c_uint16), ("delay", ctypes.c_uint16)]


class ff_envelope(ctypes.Structure):
    _fields_ = [(n, ctypes.c_uint16) for n in ("attack_length", "attack_level", "fade_length", "fade_level")]


class ff_rumble_effect(ctypes.Structure):
    _fields_ = [("strong_magnitude", ctypes.c_uint16), ("weak_magnitude", ctypes.c_uint16)]


class ff_periodic_effect(ctypes.Structure):  # only here so the union has the kernel's size
    _fields_ = [("waveform", ctypes.c_uint16), ("period", ctypes.c_uint16), ("magnitude", ctypes.c_int16),
                ("offset", ctypes.c_int16), ("phase", ctypes.c_uint16), ("envelope", ff_envelope),
                ("custom_len", ctypes.c_uint32), ("custom_data", ctypes.c_void_p)]


class ff_condition_effect(ctypes.Structure):
    _fields_ = [("right_saturation", ctypes.c_uint16), ("left_saturation", ctypes.c_uint16),
                ("right_coeff", ctypes.c_int16), ("left_coeff", ctypes.c_int16),
                ("deadband", ctypes.c_uint16), ("center", ctypes.c_int16)]


class ff_effect_union(ctypes.Union):
    _fields_ = [("rumble", ff_rumble_effect), ("periodic", ff_periodic_effect),
                ("condition", ff_condition_effect * 2)]


class ff_effect(ctypes.Structure):
    _fields_ = [("type", ctypes.c_uint16), ("id", ctypes.c_int16), ("direction", ctypes.c_uint16),
                ("trigger", ff_trigger), ("replay", ff_replay), ("u", ff_effect_union)]


class uinput_ff_upload(ctypes.Structure):
    _fields_ = [("request_id", ctypes.c_uint32), ("retval", ctypes.c_int32),
                ("effect", ff_effect), ("old", ff_effect)]


class uinput_ff_erase(ctypes.Structure):
    _fields_ = [("request_id", ctypes.c_uint32), ("retval", ctypes.c_int32), ("effect_id", ctypes.c_uint32)]


# ioctl numbers from <linux/uinput.h> (generic encoding: x86, ARM, RISC-V)
def _ioc(direction, nr, size):
    return (direction << 30) | (size << 16) | (ord("U") << 8) | nr


_W, _R, _RW = 1, 2, 3
UI_DEV_CREATE = _ioc(0, 1, 0)
UI_DEV_DESTROY = _ioc(0, 2, 0)
UI_DEV_SETUP = _ioc(_W, 3, ctypes.sizeof(uinput_setup))
UI_ABS_SETUP = _ioc(_W, 4, ctypes.sizeof(uinput_abs_setup))
UI_SET_EVBIT = _ioc(_W, 100, ctypes.sizeof(ctypes.c_int))
UI_SET_KEYBIT = _ioc(_W, 101, ctypes.sizeof(ctypes.c_int))
UI_SET_ABSBIT = _ioc(_W, 103, ctypes.sizeof(ctypes.c_int))
UI_SET_FFBIT = _ioc(_W, 107, ctypes.sizeof(ctypes.c_int))
UI_GET_SYSNAME_64 = _ioc(_R, 44, 64)
UI_BEGIN_FF_UPLOAD = _ioc(_RW, 200, ctypes.sizeof(uinput_ff_upload))
UI_END_FF_UPLOAD = _ioc(_W, 201, ctypes.sizeof(uinput_ff_upload))
UI_BEGIN_FF_ERASE = _ioc(_RW, 202, ctypes.sizeof(uinput_ff_erase))
UI_END_FF_ERASE = _ioc(_W, 203, ctypes.sizeof(uinput_ff_erase))

EVENT_SIZE = ctypes.sizeof(input_event)


# ------------------------------------------------------------- backend API

class BackendError(Exception):
    """A problem the user has to fix; the message says how."""


HELP_URL = "https://github.com/labanmne/padthru/blob/main/docs/linux.md"


def check():
    """Fail early, with a useful message, if virtual controllers can't be created."""
    try:
        os.close(os.open(DEVICE, os.O_RDWR | os.O_CLOEXEC))
    except FileNotFoundError:
        raise BackendError(
            "/dev/uinput is missing, so Padthru can't create a virtual controller. "
            "Load the kernel module with: sudo modprobe uinput  (see %s)" % HELP_URL) from None
    except PermissionError:
        raise BackendError(
            "Padthru isn't allowed to use /dev/uinput, so it can't create a virtual controller. "
            "Run the installer (it adds a udev rule, like Steam's), or see %s" % HELP_URL) from None


def describe():
    return "uinput (%s)" % DEVICE


class VirtualPad:
    """One virtual Xbox 360 controller."""

    def __init__(self, on_rumble):
        self.on_rumble = on_rumble
        self.fd = os.open(DEVICE, os.O_RDWR | os.O_NONBLOCK | os.O_CLOEXEC)
        try:
            self._create()
        except Exception:
            os.close(self.fd)
            raise
        self.state = {}
        self.closed = False
        self.effects = {}   # effect id -> (strong, weak, length in ms)
        self.playing = {}   # effect id -> time it stops (None = until stopped)
        self.gain = 0xFFFF
        self.sent = (0, 0)
        self.thread = threading.Thread(target=self._ff_loop, name="uinput-ff", daemon=True)
        self.thread.start()

    def _create(self):
        fd = self.fd
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
        for code in KEYS.values():
            fcntl.ioctl(fd, UI_SET_KEYBIT, code)
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_ABS)
        for code, lo, hi, fuzz, flat in AXES:
            fcntl.ioctl(fd, UI_SET_ABSBIT, code)
            fcntl.ioctl(fd, UI_ABS_SETUP, bytes(uinput_abs_setup(code, input_absinfo(0, lo, hi, fuzz, flat, 0))))
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_FF)
        fcntl.ioctl(fd, UI_SET_FFBIT, FF_RUMBLE)
        fcntl.ioctl(fd, UI_SET_FFBIT, FF_GAIN)
        setup = uinput_setup(input_id(BUS_USB, VENDOR, PRODUCT, VERSION), NAME, FF_EFFECTS_MAX)
        fcntl.ioctl(fd, UI_DEV_SETUP, bytes(setup))
        fcntl.ioctl(fd, UI_DEV_CREATE)

    def name(self):
        """Kernel name of the device, e.g. 'input23', for the log."""
        try:
            buf = bytearray(64)
            fcntl.ioctl(self.fd, UI_GET_SYSNAME_64, buf, True)
            return buf.split(b"\0", 1)[0].decode() or "?"
        except OSError:
            return "?"

    # --- input

    def set_state(self, buttons, lt, rt, lx, ly, rx, ry):
        """buttons: set of names (A, B, ..., UP/DOWN/LEFT/RIGHT); triggers 0..1;
        sticks -1..1 with +y pointing down (the browser Gamepad API and evdev agree)."""
        want = {}
        for name, code in KEYS.items():
            want[(EV_KEY, code)] = 1 if name in buttons else 0
        want[(EV_ABS, ABS_X)] = _stick(lx)
        want[(EV_ABS, ABS_Y)] = _stick(ly)
        want[(EV_ABS, ABS_RX)] = _stick(rx)
        want[(EV_ABS, ABS_RY)] = _stick(ry)
        want[(EV_ABS, ABS_Z)] = int(round(lt * 255))
        want[(EV_ABS, ABS_RZ)] = int(round(rt * 255))
        want[(EV_ABS, ABS_HAT0X)] = ("RIGHT" in buttons) - ("LEFT" in buttons)
        want[(EV_ABS, ABS_HAT0Y)] = ("DOWN" in buttons) - ("UP" in buttons)
        changes = [(k, v) for k, v in want.items() if self.state.get(k, 0) != v]
        if not changes:
            return
        data = b"".join(bytes(input_event(timeval(0, 0), t, c, v)) for (t, c), v in changes)
        data += bytes(input_event(timeval(0, 0), EV_SYN, SYN_REPORT, 0))
        os.write(self.fd, data)
        self.state.update(changes)

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.thread.join(1)
        try:
            fcntl.ioctl(self.fd, UI_DEV_DESTROY)
        except OSError:
            pass
        os.close(self.fd)

    # --- rumble

    def _ff_loop(self):
        while not self.closed:
            try:
                ready, _, _ = select.select([self.fd], [], [], 0.05)
                data = os.read(self.fd, EVENT_SIZE * 16) if ready else b""
            except BlockingIOError:
                data = b""
            except (OSError, ValueError):
                return
            for off in range(0, len(data) - EVENT_SIZE + 1, EVENT_SIZE):
                ev = input_event.from_buffer_copy(data, off)
                try:
                    self._ff_event(ev.type, ev.code, ev.value)
                except OSError:
                    pass
            if self.playing:
                self._update_rumble()

    def _ff_event(self, etype, code, value):
        if etype == EV_UINPUT and code == UI_FF_UPLOAD:
            up = uinput_ff_upload(request_id=value)
            buf = bytearray(up)
            fcntl.ioctl(self.fd, UI_BEGIN_FF_UPLOAD, buf, True)
            up = uinput_ff_upload.from_buffer_copy(buf)
            if up.effect.type == FF_RUMBLE:
                r = up.effect.u.rumble
                self.effects[up.effect.id] = (r.strong_magnitude, r.weak_magnitude, up.effect.replay.length)
                up.retval = 0
            else:
                up.retval = -errno.EINVAL
            fcntl.ioctl(self.fd, UI_END_FF_UPLOAD, bytes(up))
        elif etype == EV_UINPUT and code == UI_FF_ERASE:
            er = uinput_ff_erase(request_id=value)
            buf = bytearray(er)
            fcntl.ioctl(self.fd, UI_BEGIN_FF_ERASE, buf, True)
            er = uinput_ff_erase.from_buffer_copy(buf)
            self.effects.pop(er.effect_id, None)
            self.playing.pop(er.effect_id, None)
            er.retval = 0
            fcntl.ioctl(self.fd, UI_END_FF_ERASE, bytes(er))
            self._update_rumble()
        elif etype == EV_FF and code == FF_GAIN:
            self.gain = max(0, min(0xFFFF, value))
            self._update_rumble()
        elif etype == EV_FF and code in self.effects:
            if value > 0:
                length = self.effects[code][2]
                self.playing[code] = time.monotonic() + length * value / 1000.0 if length else None
            else:
                self.playing.pop(code, None)
            self._update_rumble()

    def _update_rumble(self):
        now = time.monotonic()
        for eid, until in list(self.playing.items()):
            if until is not None and now >= until:
                del self.playing[eid]
        strong = max((self.effects[e][0] for e in self.playing if e in self.effects), default=0)
        weak = max((self.effects[e][1] for e in self.playing if e in self.effects), default=0)
        level = (strong * self.gain // 0xFFFF >> 8, weak * self.gain // 0xFFFF >> 8)
        if level != self.sent:
            self.sent = level
            self.on_rumble(*level)


def _stick(v):
    return int(round(v * 32767))
