"""Padthru PC app.

Receives controller state from the Padthru TV app over the local network and
feeds it to a virtual controller, so games on this PC see the controller that
is paired to your Samsung TV. A PlayStation controller becomes a virtual
DualShock 4, anything else a virtual Xbox 360 controller (changeable).

  Windows: ViGEmBus driver, tray app.     Linux: uinput, background service.

    Padthru.exe / padthru            start (Windows: tray icon; Linux: runs in the foreground)
    --pad auto|xbox|playstation      which controller the PC sees (default: auto, saved from the tray)
    --console                        Windows: no tray, log to the console
    --debug                          also log every button and stick change
"""
import argparse
import asyncio
import json
import logging
import logging.handlers
import os
import re
import signal
import socket
import sys
import threading
import time
import webbrowser

APP = "Padthru"
VERSION = "0.3.0"
PORT = 47811
REPO_URL = "https://github.com/labanmne/padthru"
STALE_AFTER = 0.6  # seconds without a packet from the TV -> release all inputs

IS_WINDOWS = sys.platform == "win32"
HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLE = getattr(sys, "_MEIPASS", HERE)
if not getattr(sys, "frozen", False):
    # explicit, because `python -I` leaves the script's own folder off sys.path
    sys.path[:0] = [p for p in (HERE, os.path.join(HERE, "vendor")) if p not in sys.path]

if IS_WINDOWS:
    DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), APP)
else:
    DATA_DIR = os.path.join(os.environ.get("XDG_STATE_HOME") or os.path.expanduser("~/.local/state"), "padthru")
LOG_PATH = os.path.join(DATA_DIR, "padthru.log")
FIRST_RUN_FLAG = os.path.join(DATA_DIR, "first-run-done")
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")

# Which virtual controller the PC sees. "auto": a DualShock 4 for PlayStation
# controllers, an Xbox 360 controller for everything else.
PAD_MODES = {
    "auto": "Automatic (matches the controller)",
    "xbox": "Xbox 360 controller",
    "playstation": "PlayStation 4 controller (DualShock 4)",
}
KIND_NAMES = {"xbox": "Xbox 360", "playstation": "PlayStation 4"}
# apps that send controllers: the Samsung TV app and the Android TV box app
CONTROLLER_APPS = {"padthru-tv": "TV", "padthru-android": "Android TV box"}

log = logging.getLogger("padthru")


# ---------------------------------------------------------------- utilities

def setup_logging(console):
    log.setLevel(logging.INFO)
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
        fh.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
        log.addHandler(fh)
    except OSError as e:
        print(f"{APP}: cannot write the log file {LOG_PATH}: {e}", file=sys.stderr)
    if console:
        # systemd's journal adds its own timestamps
        fmt = "%(message)s" if os.environ.get("INVOCATION_ID") else "%(asctime)s %(message)s"
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(logging.Formatter(fmt))
        log.addHandler(sh)


def message_box(text, yes_no=False, error=False):
    """Native Windows message box (Yes/OK -> True). Elsewhere: print it."""
    if not IS_WINDOWS:
        print(text, file=sys.stderr)
        return False
    try:
        import ctypes
        flags = (0x4 if yes_no else 0x0) | (0x10 if error else 0x40) | 0x10000  # MB_SETFOREGROUND
        return ctypes.windll.user32.MessageBoxW(None, text, APP, flags) in (1, 6)
    except Exception:  # noqa: BLE001
        print(text)
        return False


def local_addresses():
    """IPv4 addresses the TV can reach, best guess first."""
    found = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("10.254.254.254", 1))  # no packet is sent; this just picks the default interface
        found.append(s.getsockname()[0])
        s.close()
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in found and not ip.startswith("127."):
                found.append(ip)
    except OSError:
        pass
    return found


def clamp(v, lo=-1.0, hi=1.0):
    try:
        v = float(v)
    except (TypeError, ValueError):
        return 0.0
    return lo if v < lo else hi if v > hi else v


def load_settings():
    try:
        with open(SETTINGS_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_settings(**changes):
    data = load_settings()
    data.update(changes)
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = SETTINGS_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp, SETTINGS_PATH)
    except OSError as e:
        log.error("cannot save settings to %s: %s", SETTINGS_PATH, e)


def is_playstation(tv_id):
    """True for Sony controllers. The TV describes a controller like Chrome does:
    'Wireless Controller (STANDARD GAMEPAD Vendor: 054c Product: 09cc)'."""
    tv_id = tv_id or ""
    return bool(re.search(r"vendor:?\s*0*54c\b", tv_id, re.I)
                or re.search(r"\b054c-[0-9a-f]{4}\b", tv_id, re.I)
                or re.search(r"sony|dualshock|dualsense|playstation", tv_id, re.I))


def pad_kind(mode, tv_id):
    """The kind of virtual controller ("xbox" or "playstation") for a TV controller."""
    if mode in ("xbox", "playstation"):
        return mode
    return "playstation" if is_playstation(tv_id) else "xbox"


def load_backend():
    """The module that creates virtual controllers on this OS."""
    if IS_WINDOWS:
        import vpad_windows as backend
    elif sys.platform.startswith("linux"):
        import vpad_linux as backend
    else:
        raise RuntimeError(f"{APP} runs on Windows and Linux, not on {sys.platform}.")
    return backend


# ---------------------------------------------------------------- autostart (Windows)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def autostart_command():
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}"'
    pyw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
    return f'"{pyw if os.path.exists(pyw) else sys.executable}" "{os.path.abspath(__file__)}"'


def autostart_enabled():
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            return bool(winreg.QueryValueEx(k, APP)[0])
    except OSError:
        return False


def set_autostart(on):
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            winreg.SetValueEx(k, APP, 0, winreg.REG_SZ, autostart_command())
        else:
            try:
                winreg.DeleteValue(k, APP)
            except OSError:
                pass
    log.info("start with Windows: %s", "on" if on else "off")


# ---------------------------------------------------------------- controllers

# W3C "standard" gamepad button index -> Padthru button name (Xbox names; the
# PlayStation backends map A/B/X/Y to Cross/Circle/Square/Triangle, BACK/START to
# Share/Options, GUIDE to PS). 17 is the DualShock touchpad click, when the TV has it.
BUTTONS = {
    0: "A", 1: "B", 2: "X", 3: "Y", 4: "LB", 5: "RB", 8: "BACK", 9: "START",
    10: "LS", 11: "RS", 12: "UP", 13: "DOWN", 14: "LEFT", 15: "RIGHT", 16: "GUIDE", 17: "TOUCHPAD",
}


def parse_extra(msg):
    """Motion, touchpad and battery, sent by controller apps that can read them (the Android
    box app): "g" gyro [deg/s x3], "ac" accel [g x3], "t" up to two fingers [x, y] 0..1 or
    null, "bat" battery percent, "usb" cable connected."""
    extra = {}
    for key, name, limit in (("g", "gyro", 2000.0), ("ac", "accel", 4.0)):
        v = msg.get(key)
        if isinstance(v, list) and len(v) == 3:
            extra[name] = [clamp(x, -limit, limit) for x in v]
    t = msg.get("t")
    if isinstance(t, list):
        fingers = [(clamp(f[0], 0, 1), clamp(f[1], 0, 1)) if isinstance(f, list) and len(f) >= 2 else None
                   for f in t[:2]]
        extra["touch"] = fingers + [None] * (2 - len(fingers))
    if isinstance(msg.get("bat"), (int, float)) and not isinstance(msg.get("bat"), bool):
        extra["battery"] = int(clamp(msg["bat"], 0, 100))
    if isinstance(msg.get("usb"), bool):
        extra["usb"] = msg["usb"]
    return extra or None


class Pad:
    """One TV controller, mirrored by one virtual controller (Xbox 360 or DualShock 4)."""

    def __init__(self, idx, bridge):
        self.idx = idx
        self.bridge = bridge
        self.ws = None
        self.last = 0.0
        self.active = False
        self.debug_state = None
        self.tv_id = ""
        self.device = None
        self.kind = None
        self.light = None  # last light bar color a game set (DualShock 4)

    def ensure_device(self):
        """Create the virtual controller, or replace it when the wanted kind changed.
        Raises if the backend can't create one."""
        kind = pad_kind(self.bridge.pad_mode, self.tv_id)
        if self.device is not None and kind == self.kind:
            return
        if self.device is not None:
            self.close()
            log.info("virtual controller %d: switching to %s", self.idx, KIND_NAMES[kind])
        self.light = None
        self.device = self.bridge.backend.VirtualPad(self._rumble, kind, on_light=self._light)
        self.kind = kind
        self.active = False
        log.info("virtual controller %d created: %s (%s)", self.idx, KIND_NAMES[kind], self.device.name())
        self.tell_tv()

    def _send(self, obj):
        """Send to the TV/box app that owns this controller (any thread)."""
        ws, loop = self.ws, self.bridge.loop
        if ws is None or loop is None:
            return
        try:
            asyncio.run_coroutine_threadsafe(ws.send(json.dumps(obj)), loop)
        except Exception:  # noqa: BLE001
            pass

    def tell_tv(self):
        """Let the TV show which controller the PC sees (and restore a game's light bar color)."""
        if self.kind:
            self._send({"k": [self.idx, KIND_NAMES[self.kind]]})
        if self.light is not None:
            self._send({"l": [self.idx] + list(self.light)})

    def _rumble(self, large_motor, small_motor):
        """Called by the backend, from any thread, when a game changes the rumble (0-255 each)."""
        self._send({"r": [self.idx, int(large_motor), int(small_motor)]})

    def _light(self, red, green, blue):
        """Called by the backend when a game sets the DualShock 4 light bar color."""
        light = (int(red) & 0xFF, int(green) & 0xFF, int(blue) & 0xFF)
        if light != self.light:
            self.light = light
            self._send({"l": [self.idx] + list(light)})

    def close(self):
        device, self.device, self.kind, self.active = self.device, None, None, False
        if device is not None:
            try:
                device.close()
            except Exception:  # noqa: BLE001
                pass

    def apply(self, b, a, extra=None):
        down = {i for i in BUTTONS if i < len(b) and clamp(b[i], 0, 1) > 0.5}
        # Samsung TVs keep the PS/Home button for their own menu, so Share + Options = Guide.
        if 8 in down and 9 in down:
            down -= {8, 9}
            down.add(16)
        ax = list(a[:4]) + [0, 0, 0, 0]
        self.device.set_state(
            {BUTTONS[i] for i in down},
            clamp(b[6], 0, 1) if len(b) > 6 else 0.0,
            clamp(b[7], 0, 1) if len(b) > 7 else 0.0,
            clamp(ax[0]), clamp(ax[1]), clamp(ax[2]), clamp(ax[3]),
            extra,
        )
        self.last = time.monotonic()
        self.active = True

    def release(self):
        if self.active and self.device is not None:
            self.device.set_state(set(), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
        self.active = False


# ---------------------------------------------------------------- network bridge

class Bridge:
    def __init__(self, backend, port, debug, pad_mode="auto"):
        self.backend = backend
        self.port = port
        self.debug = debug
        self.pad_mode = pad_mode
        self.pads = {}
        self.tvs = {}  # websocket -> peer address
        self.loop = None
        self.stop_event = None
        self.on_change = lambda: None
        self.seen_ids = set()
        self.pad_error_at = 0.0

    # status for the tray
    def status_text(self):
        if not self.tvs:
            return "Waiting for the TV app"
        live = [p for p in self.pads.values() if p.ws is not None]
        tv = next(iter(self.tvs.values()))
        kinds = sorted({KIND_NAMES[p.kind] for p in live if p.kind})
        return (f"TV connected ({tv}), {len(live)} controller{'s' if len(live) != 1 else ''}"
                + (f" ({', '.join(kinds)})" if kinds else ""))

    def get_pad(self, idx):
        if idx not in self.pads:
            self.pads[idx] = Pad(idx, self)
        return self.pads[idx]

    def ready(self, pad):
        """Make sure the pad has the right virtual controller; False if it can't be created now."""
        try:
            pad.ensure_device()
            return True
        except Exception as e:  # noqa: BLE001
            now = time.monotonic()
            if now - self.pad_error_at > 5:
                self.pad_error_at = now
                log.error("cannot create virtual controller %d: %s", pad.idx, e)
            return False

    def set_pad_mode(self, mode):
        """Change which controller the PC sees (any thread). Existing controllers are replaced."""
        if mode not in PAD_MODES:
            return
        self.pad_mode = mode
        log.info("controller type: %s", PAD_MODES[mode])

        def apply():
            for p in self.pads.values():
                if p.device is not None:
                    self.ready(p)
            self.on_change()

        if self.loop is not None:
            self.loop.call_soon_threadsafe(apply)

    async def handler(self, ws):
        import websockets
        peer = ws.remote_address[0] if getattr(ws, "remote_address", None) else "?"
        verified = False
        try:
            async for raw in ws:
                try:
                    msg = json.loads(raw)
                except ValueError:
                    continue
                if not isinstance(msg, dict):
                    continue
                if "hello" in msg:
                    await ws.send(json.dumps({"padthru": 1, "v": VERSION, "name": socket.gethostname()}))
                    hello = msg.get("hello") or {}
                    if isinstance(hello, dict) and hello.get("app") in CONTROLLER_APPS and not verified:
                        verified = True
                        self.tvs[ws] = peer
                        log.info("%s connected from %s (%s %s%s)", CONTROLLER_APPS[hello["app"]], peer,
                                 hello["app"], hello.get("v"), f", {hello['ua']}" if hello.get("ua") and
                                 hello["app"] != "padthru-tv" else "")
                        self.on_change()
                    continue
                if not verified:
                    continue
                if "ping" in msg:
                    await ws.send(json.dumps({"pong": msg["ping"]}))
                    continue
                if "log" in msg:
                    log.info("tv: %s", str(msg["log"])[:500])
                    continue
                idx = msg.get("i")
                if not isinstance(idx, int) or not 0 <= idx <= 3:
                    continue
                pad = self.get_pad(idx)
                if msg.get("gone"):
                    pad.release()
                    if pad.ws is ws:
                        pad.ws = None
                    self.on_change()
                    continue
                if isinstance(msg.get("id"), str) and msg["id"]:
                    pad.tv_id = msg["id"][:200]
                    if (idx, pad.tv_id) not in self.seen_ids:
                        self.seen_ids.add((idx, pad.tv_id))
                        log.info("controller %d: %s (mapping %s)", idx, pad.tv_id, msg.get("m"))
                if not self.ready(pad):
                    continue
                if pad.ws is not ws:
                    pad.ws = ws
                    pad.tell_tv()
                    self.on_change()
                b, a = msg.get("b") or [], msg.get("a") or []
                if not isinstance(b, list) or not isinstance(a, list):
                    continue
                if self.debug:
                    state = (tuple(i for i, v in enumerate(b) if clamp(v, 0, 1) > 0.5),
                             tuple(round(clamp(v), 1) for v in a[:4]))
                    if state != pad.debug_state:
                        pad.debug_state = state
                        log.info("controller %d buttons=%s sticks=%s", idx, state[0], state[1])
                pad.apply(b, a, parse_extra(msg))
        except websockets.ConnectionClosed:
            pass
        finally:
            if self.tvs.pop(ws, None) is not None:
                log.info("TV disconnected (%s)", peer)
            for p in self.pads.values():
                if p.ws is ws:
                    p.ws = None
                    p.release()
            self.on_change()

    async def watchdog(self):
        while not self.stop_event.is_set():
            try:
                await asyncio.wait_for(self.stop_event.wait(), 0.2)
            except asyncio.TimeoutError:
                pass
            now = time.monotonic()
            for p in self.pads.values():
                if p.active and now - p.last > STALE_AFTER:
                    p.release()

    async def discovery(self, path, request_headers):
        """Plain HTTP GET /padthru answers the TV's network search.

        The TV searches with ordinary HTTP requests because its browser deliberately
        slows down WebSocket attempts after many failures (port-scan protection).
        """
        if path.split("?")[0] == "/padthru" and "upgrade" not in request_headers.get("Connection", "").lower():
            body = json.dumps({"padthru": 1, "v": VERSION, "name": socket.gethostname()}).encode()
            return (200, [("Content-Type", "application/json"), ("Access-Control-Allow-Origin", "*"),
                          ("Cache-Control", "no-store")], body)
        return None

    async def main(self, started):
        import websockets
        self.loop = asyncio.get_running_loop()
        self.stop_event = asyncio.Event()
        try:
            server = await websockets.serve(self.handler, "0.0.0.0", self.port, ping_interval=5,
                                            ping_timeout=5, max_size=65536, compression=None,
                                            process_request=self.discovery)
        except OSError as e:
            started["error"] = e
            started["event"].set()
            return
        log.info("%s %s listening on port %d (%s, controller type: %s), addresses: %s", APP, VERSION,
                 self.port, self.backend.describe(), self.pad_mode, ", ".join(local_addresses()) or "unknown")
        started["event"].set()
        await self.watchdog()
        server.close()
        await server.wait_closed()
        for p in self.pads.values():
            p.close()
        log.info("%s stopped", APP)

    def stop(self):
        if self.loop and self.stop_event:
            self.loop.call_soon_threadsafe(self.stop_event.set)


# ---------------------------------------------------------------- tray (Windows)

def run_tray(bridge):
    import pystray
    from PIL import Image

    image = Image.open(os.path.join(BUNDLE, "assets", "padthru.png"))

    def toggle_autostart(icon, item):
        try:
            set_autostart(not autostart_enabled())
        except OSError as e:
            message_box(f"Could not change the startup setting:\n{e}", error=True)

    def open_log(icon, item):
        try:
            os.startfile(LOG_PATH)
        except OSError:
            pass

    def quit_app(icon, item):
        bridge.stop()
        icon.stop()

    def mode_item(mode):
        def choose(icon, item):
            save_settings(pad=mode)
            bridge.set_pad_mode(mode)
        return pystray.MenuItem(PAD_MODES[mode], choose, checked=lambda item: bridge.pad_mode == mode, radio=True)

    menu = pystray.Menu(
        pystray.MenuItem(lambda item: bridge.status_text(), None, enabled=False),
        pystray.MenuItem(lambda item: "This PC: " + (", ".join(local_addresses()[:2]) or "unknown"), None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("PC sees the controller as", pystray.Menu(*(mode_item(m) for m in PAD_MODES))),
        pystray.MenuItem("Start with Windows", toggle_autostart, checked=lambda item: autostart_enabled()),
        pystray.MenuItem("Open log", open_log),
        pystray.MenuItem("Help and updates", lambda icon, item: webbrowser.open(REPO_URL)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Quit Padthru", quit_app),
    )
    icon = pystray.Icon(APP, image, f"{APP}: waiting for the TV app", menu)

    def changed():
        try:
            icon.title = f"{APP}: {bridge.status_text()}"[:127]
            icon.update_menu()
        except Exception:  # noqa: BLE001
            pass

    bridge.on_change = changed

    def first_run(icon):
        icon.visible = True
        if not os.path.exists(FIRST_RUN_FLAG):
            try:
                open(FIRST_RUN_FLAG, "w").close()
            except OSError:
                pass
            if message_box("Padthru is running in the system tray, next to the clock.\n\n"
                           "Start it automatically when you sign in to Windows?", yes_no=True):
                try:
                    set_autostart(True)
                except OSError:
                    pass
            changed()

    icon.run(setup=first_run)


# ---------------------------------------------------------------- entry point

def main():
    ap = argparse.ArgumentParser(prog="padthru", description=f"{APP} {VERSION} PC app ({REPO_URL})")
    ap.add_argument("--console", action="store_true", help="Windows: no tray icon, log to the console")
    ap.add_argument("--debug", action="store_true", help="log every button and stick change")
    ap.add_argument("--pad", choices=list(PAD_MODES),
                    help="which controller the PC sees: auto (default: PlayStation controllers become a "
                         "DualShock 4, others an Xbox 360 controller), xbox or playstation")
    ap.add_argument("--port", type=int, default=PORT, help=f"TCP port (default {PORT}; the TV app expects {PORT})")
    ap.add_argument("--version", action="version", version=f"{APP} {VERSION}")
    args = ap.parse_args()
    console = args.console or not IS_WINDOWS  # no tray on Linux: it runs as a service or in a terminal

    setup_logging(console=console)
    pad_mode = args.pad or load_settings().get("pad")
    if pad_mode not in PAD_MODES:
        pad_mode = "auto"

    try:
        backend = load_backend()
        backend.check()
    except Exception as e:  # noqa: BLE001
        log.error("%s", e)
        if IS_WINDOWS:
            if message_box(f"{e}\n\nOpen the download page now?", yes_no=True, error=True):
                webbrowser.open(getattr(e, "help_url", REPO_URL))
        return 1

    bridge = Bridge(backend, args.port, args.debug, pad_mode)
    started = {"event": threading.Event(), "error": None}
    thread = threading.Thread(target=lambda: asyncio.run(bridge.main(started)), name="bridge", daemon=True)
    thread.start()
    started["event"].wait(10)
    if started["error"] is not None:
        log.error("cannot listen on port %d: %s", args.port, started["error"])
        message_box(f"Padthru could not start: port {args.port} is already in use.\n\n"
                    "Padthru is probably already running."
                    + (" Look for its icon next to the clock." if IS_WINDOWS else ""), error=True)
        return 1

    if console:
        if not IS_WINDOWS:
            signal.signal(signal.SIGTERM, lambda *_: bridge.stop())
        if sys.stdout is not None and sys.stdout.isatty():  # None in the windowed Padthru.exe
            print(f"{APP} {VERSION} is running. This PC: {', '.join(local_addresses()) or 'unknown'}. "
                  "Open Padthru on the TV. Press Ctrl+C to quit.")
        try:
            while thread.is_alive():
                thread.join(0.5)
        except KeyboardInterrupt:
            bridge.stop()
            thread.join(3)
        return 0

    run_tray(bridge)
    thread.join(3)
    return 0


if __name__ == "__main__":
    sys.exit(main())
