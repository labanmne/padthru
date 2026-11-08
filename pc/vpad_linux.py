"""Virtual controllers on Linux, through the kernel's uinput module.

Pure Python, no compiled modules: it talks to /dev/uinput with ioctl() and
creates a device that looks exactly like a wired controller does under the
kernel's own drivers (same IDs, buttons, axes and ranges):

  "xbox"         an Xbox 360 controller, as the xpad driver shows one
  "playstation"  a DualShock 4, as the hid-playstation / hid-sony drivers show one

SDL (Steam, Proton and most native games) and browsers therefore treat it as one.

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

BUS_USB = 0x03

EV_SYN, EV_KEY, EV_ABS, EV_FF, EV_UINPUT = 0x00, 0x01, 0x03, 0x15, 0x0101
SYN_REPORT = 0
UI_FF_UPLOAD, UI_FF_ERASE = 1, 2
FF_RUMBLE, FF_GAIN = 0x50, 0x60
FF_EFFECTS_MAX = 16

BTN_SOUTH, BTN_EAST, BTN_NORTH, BTN_WEST = 0x130, 0x131, 0x133, 0x134
BTN_TL, BTN_TR, BTN_TL2, BTN_TR2 = 0x136, 0x137, 0x138, 0x139
BTN_SELECT, BTN_START, BTN_MODE, BTN_THUMBL, BTN_THUMBR = 0x13A, 0x13B, 0x13C, 0x13D, 0x13E
ABS_X, ABS_Y, ABS_Z, ABS_RX, ABS_RY, ABS_RZ, ABS_HAT0X, ABS_HAT0Y = 0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x10, 0x11
STICK_AXES = (ABS_X, ABS_Y, ABS_RX, ABS_RY)


class Kind:
    """How one kind of controller looks to the system: identity, buttons and axes."""

    def __init__(self, name, vendor, product, version, keys, axes, stick, trigger_keys=None):
        self.name, self.vendor, self.product, self.version = name, vendor, product, version
        self.keys = keys            # Padthru button name -> key code
        self.axes = axes            # (code, minimum, maximum, fuzz, flat)
        self.stick = stick          # -1..1 -> stick axis value
        self.trigger_keys = trigger_keys or {}  # digital L2/R2 keys, pressed while the trigger is > 0


KINDS = {
    # What the xpad driver reports for a wired Xbox 360 controller. Games, SDL and
    # Steam recognise the controller by these IDs (and some older games by name).
    # xpad's X and Y use the BTN_X/BTN_Y codes, which are BTN_NORTH/BTN_WEST.
    "xbox": Kind(
        b"Microsoft X-Box 360 pad", 0x045E, 0x028E, 0x0114,
        {"A": BTN_SOUTH, "B": BTN_EAST, "X": BTN_NORTH, "Y": BTN_WEST, "LB": BTN_TL, "RB": BTN_TR,
         "BACK": BTN_SELECT, "TOUCHPAD": BTN_SELECT, "START": BTN_START, "GUIDE": BTN_MODE,
         "LS": BTN_THUMBL, "RS": BTN_THUMBR},
        [(ABS_X, -32768, 32767, 16, 128), (ABS_Y, -32768, 32767, 16, 128),
         (ABS_RX, -32768, 32767, 16, 128), (ABS_RY, -32768, 32767, 16, 128),
         (ABS_Z, 0, 255, 0, 0), (ABS_RZ, 0, 255, 0, 0),
         (ABS_HAT0X, -1, 1, 0, 0), (ABS_HAT0Y, -1, 1, 0, 0)],
        lambda v: int(round(v * 32767))),
    # What hid-playstation (and hid-sony before it) reports for a USB DualShock 4 v2.
    # Version 0x8111: those drivers set bit 15 to mark this button layout, and SDL's
    # controller database matches on it. Cross/Circle/Square/Triangle are
    # SOUTH/EAST/WEST/NORTH, L2/R2 are also buttons, sticks run 0-255 like the real pad.
    "playstation": Kind(
        b"Sony Interactive Entertainment Wireless Controller", 0x054C, 0x09CC, 0x8111,
        {"A": BTN_SOUTH, "B": BTN_EAST, "X": BTN_WEST, "Y": BTN_NORTH, "LB": BTN_TL, "RB": BTN_TR,
         "BACK": BTN_SELECT, "START": BTN_START, "GUIDE": BTN_MODE, "LS": BTN_THUMBL, "RS": BTN_THUMBR},
        [(ABS_X, 0, 255, 0, 0), (ABS_Y, 0, 255, 0, 0), (ABS_RX, 0, 255, 0, 0), (ABS_RY, 0, 255, 0, 0),
         (ABS_Z, 0, 255, 0, 0), (ABS_RZ, 0, 255, 0, 0),
         (ABS_HAT0X, -1, 1, 0, 0), (ABS_HAT0Y, -1, 1, 0, 0)],
        lambda v: int(round((v + 1.0) * 127.5)),
        {"L2": BTN_TL2, "R2": BTN_TR2}),
}
# the Xbox layout under its old names, for scripts that import it
NAME, KEYS, AXES = KINDS["xbox"].name, KINDS["xbox"].keys, KINDS["xbox"].axes


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


BTN_JOYSTICK, BTN_GAMEPAD = 0x120, 0x130


def _has_key(bitmap, code, word_bits=None):
    """True if a sysfs capability bitmap ("capabilities/key": hex words, most
    significant first, one per C long) has the bit for this key code."""
    words = bitmap.split()
    bits = word_bits or ctypes.sizeof(ctypes.c_long) * 8
    idx = len(words) - 1 - code // bits
    if idx < 0:
        return False
    try:
        return bool(int(words[idx], 16) >> (code % bits) & 1)
    except ValueError:
        return False


def _read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read().strip()
    except OSError:
        return ""


def real_controllers(sys_root="/sys", udev_root="/run/udev/data"):
    """Names of game controllers plugged into this PC itself (USB or Bluetooth).

    Virtual input devices (ours, Steam's and other uinput devices) live under
    /sys/devices/virtual and don't count. A device is a controller when udev says
    so (ID_INPUT_JOYSTICK), or, without udev, when it has gamepad or joystick buttons."""
    found = []
    base = os.path.join(sys_root, "class", "input")
    try:
        entries = sorted(os.listdir(base))
    except OSError:
        return found
    for entry in entries:
        if not entry.startswith("event"):
            continue
        node = os.path.join(base, entry)
        if "/devices/virtual/" in os.path.realpath(node).replace(os.sep, "/"):
            continue
        udev = os.path.join(udev_root, "c" + _read(os.path.join(node, "dev")))
        if os.path.exists(udev):
            joystick = "E:ID_INPUT_JOYSTICK=1" in _read(udev).splitlines()
        else:
            keys = _read(os.path.join(node, "device", "capabilities", "key"))
            joystick = _has_key(keys, BTN_GAMEPAD) or _has_key(keys, BTN_JOYSTICK)
        if joystick:
            found.append(_read(os.path.join(node, "device", "name")) or "a controller")
    return sorted(set(found))


class VirtualPad:
    """One virtual controller: kind "xbox" (Xbox 360) or "playstation" (DualShock 4)."""

    def __init__(self, on_rumble, kind="xbox", on_light=None):
        self.on_rumble = on_rumble
        self.kind = KINDS[kind]
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
        kind = self.kind
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_KEY)
        for code in sorted(set(kind.keys.values()) | set(kind.trigger_keys.values())):
            fcntl.ioctl(fd, UI_SET_KEYBIT, code)
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_ABS)
        for code, lo, hi, fuzz, flat in kind.axes:
            rest = kind.stick(0.0) if code in STICK_AXES else 0
            fcntl.ioctl(fd, UI_SET_ABSBIT, code)
            fcntl.ioctl(fd, UI_ABS_SETUP, bytes(uinput_abs_setup(code, input_absinfo(rest, lo, hi, fuzz, flat, 0))))
        fcntl.ioctl(fd, UI_SET_EVBIT, EV_FF)
        fcntl.ioctl(fd, UI_SET_FFBIT, FF_RUMBLE)
        fcntl.ioctl(fd, UI_SET_FFBIT, FF_GAIN)
        setup = uinput_setup(input_id(BUS_USB, kind.vendor, kind.product, kind.version), kind.name, FF_EFFECTS_MAX)
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

    def set_state(self, buttons, lt, rt, lx, ly, rx, ry, extra=None):
        """buttons: set of names (A, B, ..., UP/DOWN/LEFT/RIGHT); triggers 0..1;
        sticks -1..1 with +y pointing down (the browser Gamepad API and evdev agree).
        extra (motion, touchpad, battery) isn't passed on by the Linux backend yet."""
        kind = self.kind
        want = {}
        for name, code in kind.keys.items():
            want[(EV_KEY, code)] = max(want.get((EV_KEY, code), 0), 1 if name in buttons else 0)
        if kind.trigger_keys:
            want[(EV_KEY, kind.trigger_keys["L2"])] = 1 if lt > 0 else 0
            want[(EV_KEY, kind.trigger_keys["R2"])] = 1 if rt > 0 else 0
        want[(EV_ABS, ABS_X)] = kind.stick(lx)
        want[(EV_ABS, ABS_Y)] = kind.stick(ly)
        want[(EV_ABS, ABS_RX)] = kind.stick(rx)
        want[(EV_ABS, ABS_RY)] = kind.stick(ry)
        want[(EV_ABS, ABS_Z)] = int(round(lt * 255))
        want[(EV_ABS, ABS_RZ)] = int(round(rt * 255))
        want[(EV_ABS, ABS_HAT0X)] = ("RIGHT" in buttons) - ("LEFT" in buttons)
        want[(EV_ABS, ABS_HAT0Y)] = ("DOWN" in buttons) - ("UP" in buttons)
        changes = [(k, v) for k, v in want.items() if self.state.get(k) != v]
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
