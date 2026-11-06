"""Padthru PC app.

Receives controller state from the Padthru TV app over the local network and
feeds it to a virtual Xbox 360 controller, so games on this PC see the
controller that is paired to your Samsung TV.

  Windows: ViGEmBus driver, tray app.     Linux: uinput, background service.

    Padthru.exe / padthru            start (Windows: tray icon; Linux: runs in the foreground)
    --console                        Windows: no tray, log to the console
    --debug                          also log every button and stick change
"""
import argparse
import asyncio
import json
import logging
import logging.handlers
import os
import signal
import socket
import sys
import threading
import time
import webbrowser

APP = "Padthru"
VERSION = "0.2.1"
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

# W3C "standard" gamepad button index -> Xbox button
BUTTONS = {
    0: "A", 1: "B", 2: "X", 3: "Y", 4: "LB", 5: "RB", 8: "BACK", 9: "START",
    10: "LS", 11: "RS", 12: "UP", 13: "DOWN", 14: "LEFT", 15: "RIGHT", 16: "GUIDE", 17: "BACK",
}


class Pad:
    """One TV controller, mirrored by one virtual Xbox 360 controller."""

    def __init__(self, backend, idx, bridge):
        self.idx = idx
        self.bridge = bridge
        self.ws = None
        self.last = 0.0
        self.active = False
        self.debug_state = None
        self.device = backend.VirtualPad(self._rumble)
        log.info("virtual controller %d created (%s)", idx, self.device.name())

    def _rumble(self, large_motor, small_motor):
        """Called by the backend, from any thread, when a game changes the rumble (0-255 each)."""
        ws, loop = self.ws, self.bridge.loop
        if ws is None or loop is None:
            return
        msg = json.dumps({"r": [self.idx, int(large_motor), int(small_motor)]})
        try:
            asyncio.run_coroutine_threadsafe(ws.send(msg), loop)
        except Exception:  # noqa: BLE001
            pass

    def apply(self, b, a):
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
        )
        self.last = time.monotonic()
        self.active = True

    def release(self):
        if self.active:
            self.device.set_state(set(), 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)
            self.active = False


# ---------------------------------------------------------------- network bridge

class Bridge:
    def __init__(self, backend, port, debug):
        self.backend = backend
        self.port = port
        self.debug = debug
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
        live = sum(1 for p in self.pads.values() if p.ws is not None)
        tv = next(iter(self.tvs.values()))
        return f"TV connected ({tv}), {live} controller{'s' if live != 1 else ''}"

    def get_pad(self, idx):
        if idx not in self.pads:
            now = time.monotonic()
            if now - self.pad_error_at < 5:
                return None
            try:
                self.pads[idx] = Pad(self.backend, idx, self)
            except Exception as e:  # noqa: BLE001
                self.pad_error_at = now
                log.error("cannot create virtual controller %d: %s", idx, e)
                return None
        return self.pads[idx]

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
                    if isinstance(hello, dict) and hello.get("app") == "padthru-tv" and not verified:
                        verified = True
                        self.tvs[ws] = peer
                        log.info("TV connected from %s (TV app %s)", peer, hello.get("v"))
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
                if pad is None:
                    continue
                if pad.ws is not ws:
                    pad.ws = ws
                    self.on_change()
                if msg.get("gone"):
                    pad.release()
                    pad.ws = None
                    self.on_change()
                    continue
                if msg.get("id") and (idx, msg["id"]) not in self.seen_ids:
                    self.seen_ids.add((idx, msg["id"]))
                    log.info("controller %d: %s (mapping %s)", idx, msg["id"], msg.get("m"))
                b, a = msg.get("b") or [], msg.get("a") or []
                if not isinstance(b, list) or not isinstance(a, list):
                    continue
                if self.debug:
                    state = (tuple(i for i, v in enumerate(b) if clamp(v, 0, 1) > 0.5),
                             tuple(round(clamp(v), 1) for v in a[:4]))
                    if state != pad.debug_state:
                        pad.debug_state = state
                        log.info("controller %d buttons=%s sticks=%s", idx, state[0], state[1])
                pad.apply(b, a)
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
        log.info("%s %s listening on port %d (%s), addresses: %s", APP, VERSION, self.port,
                 self.backend.describe(), ", ".join(local_addresses()) or "unknown")
        started["event"].set()
        await self.watchdog()
        server.close()
        await server.wait_closed()
        for p in self.pads.values():
            try:
                p.device.close()
            except Exception:  # noqa: BLE001
                pass
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

    menu = pystray.Menu(
        pystray.MenuItem(lambda item: bridge.status_text(), None, enabled=False),
        pystray.MenuItem(lambda item: "This PC: " + (", ".join(local_addresses()[:2]) or "unknown"), None, enabled=False),
        pystray.Menu.SEPARATOR,
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
    ap.add_argument("--port", type=int, default=PORT, help=f"TCP port (default {PORT}; the TV app expects {PORT})")
    ap.add_argument("--version", action="version", version=f"{APP} {VERSION}")
    args = ap.parse_args()
    console = args.console or not IS_WINDOWS  # no tray on Linux: it runs as a service or in a terminal

    setup_logging(console=console)

    try:
        backend = load_backend()
        backend.check()
    except Exception as e:  # noqa: BLE001
        log.error("%s", e)
        if IS_WINDOWS:
            if message_box(f"{e}\n\nOpen the download page now?", yes_no=True, error=True):
                webbrowser.open(getattr(e, "help_url", REPO_URL))
        return 1

    bridge = Bridge(backend, args.port, args.debug)
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
