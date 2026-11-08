"""Tests for stepping aside while a controller is plugged into the PC itself.

    python3 tests/step_aside.py            (Windows or Linux; needs the websockets package,
                                            which a Linux install of Padthru carries in its pyz)

1. The bridge, with a fake backend: a TV sends input, a "real" controller appears,
   the virtual controller is unplugged and the TV's input ignored; it goes away and
   the TV's next input creates the virtual controller again. Runs everywhere.
2. Linux: real_controllers() on a fake /sys tree (virtual, keyboard, touchpad,
   motion sensor and udev-less devices).
3. Windows: real_controllers() on this PC, with a ViGEm DualShock 4 plugged in,
   which must not count as a real controller (skipped without ViGEmBus).
"""
import asyncio
import json
import os
import sys
import tempfile
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
PC = os.path.join(HERE, "..", "pc")
sys.path[:0] = [PC, os.path.join(PC, "vendor")]
try:
    import websockets  # noqa: F401
except ImportError:  # use the copy inside a built or installed Padthru pyz
    for pyz in (os.environ.get("PADTHRU_PYZ"), os.path.join(HERE, "..", "dist", "padthru-linux.pyz"),
                os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"),
                             "padthru", "padthru.pyz")):
        if pyz and os.path.exists(pyz):
            sys.path.append(pyz)
            break
    import websockets  # noqa: F401
import padthru  # noqa: E402

failures = []


def check(cond, what):
    print(("ok   " if cond else "FAIL ") + what, flush=True)
    if not cond:
        failures.append(what)


# ---------------------------------------------------------------- 1. the bridge

class FakeDevice:
    made = []

    def __init__(self, on_rumble, kind="xbox", on_light=None):
        self.kind, self.closed, self.states = kind, False, 0
        FakeDevice.made.append(self)

    def name(self):
        return "fake " + self.kind

    def set_state(self, *args):
        self.states += 1

    def close(self):
        self.closed = True


class FakeBackend:
    VirtualPad = FakeDevice
    plugged = []

    @staticmethod
    def describe():
        return "fake"

    @classmethod
    def real_controllers(cls):
        return list(cls.plugged)


def test_bridge():
    print("== bridge", flush=True)
    padthru.DESK_CHECK_EVERY = 0.1
    import socket
    with socket.socket() as s:  # a free port (a fixed one may be forwarded to WSL, say)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    bridge = padthru.Bridge(FakeBackend, port, False, "auto", step_aside=True)
    started = {"event": threading.Event(), "error": None}
    thread = threading.Thread(target=lambda: asyncio.run(bridge.main(started)), daemon=True)
    thread.start()
    started["event"].wait(5)
    check(started["event"].is_set() and started["error"] is None, "bridge listens")

    async def tv():
        import websockets
        async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
            await ws.send(json.dumps({"hello": {"app": "padthru-tv", "v": "test"}}))
            await ws.recv()

            async def press(n=5):
                for _ in range(n):
                    await ws.send(json.dumps({"i": 0, "id": "Wireless Controller (STANDARD GAMEPAD Vendor: 054c "
                                              "Product: 09cc)", "b": [1] + [0] * 16, "a": [0, 0, 0, 0]}))
                    await asyncio.sleep(0.02)

            await press()
            await asyncio.sleep(0.2)
            check(len(FakeDevice.made) == 1 and not FakeDevice.made[0].closed and FakeDevice.made[0].states > 0,
                  "TV input creates a virtual controller")
            first = FakeDevice.made[0]

            FakeBackend.plugged = ["Wireless Controller"]
            await asyncio.sleep(0.4)
            check(first.closed, "a controller plugged into the PC unplugs the virtual one")
            check(bridge.status_text() == "Paused: Wireless Controller is plugged into this PC",
                  "tray status says why: %r" % bridge.status_text())
            await press()
            await asyncio.sleep(0.2)
            check(len(FakeDevice.made) == 1, "TV input is ignored while it's plugged in")

            FakeBackend.plugged = []
            await asyncio.sleep(0.4)
            check(not bridge.status_text().startswith("Paused"), "status back to normal once it's gone")
            check(len(FakeDevice.made) == 1, "no virtual controller before the TV sends something")
            await press()
            await asyncio.sleep(0.2)
            check(len(FakeDevice.made) == 2 and not FakeDevice.made[1].closed, "the TV's next input brings it back")

            bridge.set_step_aside(False)
            FakeBackend.plugged = ["Wireless Controller"]
            await asyncio.sleep(0.4)
            check(not FakeDevice.made[1].closed and not bridge.desk_pads, "switched off: nothing happens")
            bridge.set_step_aside(True)
            await asyncio.sleep(0.4)
            check(FakeDevice.made[1].closed, "switched on again: it steps aside")

    asyncio.run(tv())
    bridge.stop()
    thread.join(3)


# ---------------------------------------------------------------- 2. Linux detection

def test_linux():
    print("== Linux real_controllers() on a fake /sys", flush=True)
    import vpad_linux as V
    root = tempfile.mkdtemp(prefix="padthru-sys-")
    sysd, udev = os.path.join(root, "sys"), os.path.join(root, "udev")
    os.makedirs(os.path.join(sysd, "class", "input"))
    os.makedirs(udev)
    bits = 64

    def keymap(*codes):
        words = [0] * 12
        for c in codes:
            words[c // bits] |= 1 << (c % bits)
        return " ".join("%x" % w for w in reversed(words))

    def device(n, parent, name, keys="0", udev_props=None):
        path = os.path.join(sysd, "devices", parent, "input", f"input{n}", f"event{n}")
        os.makedirs(os.path.join(path, "device", "capabilities"))
        with open(os.path.join(path, "dev"), "w") as f:
            f.write(f"13:{64 + n}\n")
        with open(os.path.join(path, "device", "name"), "w") as f:
            f.write(name + "\n")
        with open(os.path.join(path, "device", "capabilities", "key"), "w") as f:
            f.write(keys + "\n")
        os.symlink(os.path.relpath(path, os.path.join(sysd, "class", "input")),
                   os.path.join(sysd, "class", "input", f"event{n}"))
        if udev_props is not None:
            with open(os.path.join(udev, f"c13:{64 + n}"), "w") as f:
                f.write("".join(f"E:{p}=1\n" for p in udev_props))

    usb = "pci0000:00/0000:00:14.0/usb1/1-2/1-2:1.3/0003:054C:09CC.0005"
    device(0, usb, "Sony Interactive Entertainment Wireless Controller", keymap(V.BTN_GAMEPAD),
           ["ID_INPUT", "ID_INPUT_JOYSTICK"])
    device(1, usb, "Sony Interactive Entertainment Wireless Controller Motion Sensors", "0",
           ["ID_INPUT", "ID_INPUT_ACCELEROMETER"])
    device(2, usb, "Sony Interactive Entertainment Wireless Controller Touchpad", keymap(0x110, 0x14a),
           ["ID_INPUT", "ID_INPUT_TOUCHPAD"])
    device(3, "virtual", "Sony Interactive Entertainment Wireless Controller", keymap(V.BTN_GAMEPAD),
           ["ID_INPUT", "ID_INPUT_JOYSTICK"])  # ours, through uinput
    device(4, "pci0000:00/0000:00:14.0/usb1/1-3", "Gaming Keyboard", keymap(30, 57), ["ID_INPUT", "ID_INPUT_KEYBOARD"])
    device(5, "pci0000:00/0000:00:14.0/usb1/1-4", "Logitech Extreme 3D", keymap(V.BTN_JOYSTICK))  # no udev data
    found = V.real_controllers(sysd, udev)
    check(found == ["Logitech Extreme 3D", "Sony Interactive Entertainment Wireless Controller"],
          "finds the real controllers only: %r" % found)
    check(V.real_controllers(os.path.join(root, "nowhere"), udev) == [], "no /sys: nothing, no error")
    # "10000 0": the upper 64-bit word has bit 16 set, which is key 64 + 16 = 0x50
    check(V._has_key("10000 0", 0x50, 64) and not V._has_key("10000 0", 0x40, 64)
          and V._has_key("1", 0, 64) and not V._has_key("", 5, 64), "capability bitmap parsing")


# ---------------------------------------------------------------- 3. Windows detection

def test_windows():
    print("== Windows real_controllers() on this PC", flush=True)
    import vpad_windows as V
    before = V.real_controllers()
    print("     plugged into this PC now: %s" % (before or "none"))
    if V.vg is None:
        print("skip ViGEmBus isn't available, so no virtual controller to tell apart")
        return
    pad = V.VirtualPad(lambda *a: None, "playstation")
    try:
        time.sleep(1.5)  # let Windows enumerate it
        during = V.real_controllers()
    finally:
        pad.close()
    check(during == before, "a ViGEm DualShock 4 doesn't count as a real controller (%r)" % during)
    t = time.perf_counter()
    for _ in range(20):
        V.real_controllers()
    ms = (time.perf_counter() - t) * 50
    check(ms < 50, "fast enough to run every 2 s: %.1f ms per look" % ms)


test_bridge()
if sys.platform.startswith("linux"):
    test_linux()
elif sys.platform == "win32":
    test_windows()
print("\n%s" % ("all passed" if not failures else "%d FAILED" % len(failures)))
sys.exit(1 if failures else 0)
