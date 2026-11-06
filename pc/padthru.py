"""Padthru PC app.

Receives controller state from the Padthru TV app over the local network and
feeds it to a virtual Xbox 360 controller (ViGEmBus), so games on this PC see
the controller that is paired to your Samsung TV.

    Padthru.exe              tray app (default)
    Padthru.exe --console    no tray, log to the console
    Padthru.exe --debug      also log every button and stick change
"""
import argparse
import asyncio
import json
import logging
import logging.handlers
import os
import socket
import sys
import threading
import time
import webbrowser

APP = "Padthru"
VERSION = "0.1.0"
PORT = 47811
REPO_URL = "https://github.com/labanmne/padthru"
VIGEMBUS_URL = "https://github.com/nefarius/ViGEmBus/releases/latest"
STALE_AFTER = 0.6  # seconds without a packet from the TV -> release all inputs

HERE = os.path.dirname(os.path.abspath(__file__))
BUNDLE = getattr(sys, "_MEIPASS", HERE)
if not getattr(sys, "frozen", False):
    sys.path.insert(0, os.path.join(HERE, "vendor"))

DATA_DIR = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"), APP)
LOG_PATH = os.path.join(DATA_DIR, "padthru.log")
FIRST_RUN_FLAG = os.path.join(DATA_DIR, "first-run-done")

log = logging.getLogger("padthru")


# ---------------------------------------------------------------- utilities

def setup_logging(console):
    os.makedirs(DATA_DIR, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(message)s")
    log.setLevel(logging.INFO)
    fh = logging.handlers.RotatingFileHandler(LOG_PATH, maxBytes=1_000_000, backupCount=2, encoding="utf-8")
    fh.setFormatter(fmt)
    log.addHandler(fh)
    if console:
        sh = logging.StreamHandler(sys.stdout)
        sh.setFormatter(fmt)
        log.addHandler(sh)


def message_box(text, yes_no=False, error=False):
    """Native Windows message box. Returns True for Yes/OK."""
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


# ---------------------------------------------------------------- autostart

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


# ---------------------------------------------------------------- virtual controllers

class Pad:
    """One virtual Xbox 360 controller, fed with W3C 'standard' gamepad state."""

    def __init__(self, vg, idx, bridge):
        b = vg.XUSB_BUTTON
        self.buttons = {
            0: b.XUSB_GAMEPAD_A, 1: b.XUSB_GAMEPAD_B, 2: b.XUSB_GAMEPAD_X, 3: b.XUSB_GAMEPAD_Y,
            4: b.XUSB_GAMEPAD_LEFT_SHOULDER, 5: b.XUSB_GAMEPAD_RIGHT_SHOULDER,
            8: b.XUSB_GAMEPAD_BACK, 9: b.XUSB_GAMEPAD_START,
            10: b.XUSB_GAMEPAD_LEFT_THUMB, 11: b.XUSB_GAMEPAD_RIGHT_THUMB,
            12: b.XUSB_GAMEPAD_DPAD_UP, 13: b.XUSB_GAMEPAD_DPAD_DOWN,
            14: b.XUSB_GAMEPAD_DPAD_LEFT, 15: b.XUSB_GAMEPAD_DPAD_RIGHT,
            16: b.XUSB_GAMEPAD_GUIDE, 17: b.XUSB_GAMEPAD_BACK,
        }
        self.idx = idx
        self.bridge = bridge
        self.pad = vg.VX360Gamepad()
        self.pad.register_notification(callback_function=self._rumble)
        self.ws = None
        self.last = 0.0
        self.active = False
        self.debug_state = None
        log.info("virtual controller %d created", idx)

    def _rumble(self, client, target, large_motor, small_motor, led_number, user_data):
        ws, loop = self.ws, self.bridge.loop
        if ws is None or loop is None:
            return
        msg = json.dumps({"r": [self.idx, large_motor, small_motor]})
        try:
            asyncio.run_coroutine_threadsafe(ws.send(msg), loop)
        except Exception:  # noqa: BLE001
            pass

    def apply(self, b, a):
        down = {i for i in self.buttons if i < len(b) and clamp(b[i], 0, 1) > 0.5}
        # Samsung TVs keep the PS/Home button for their own menu, so Share + Options = Guide.
        if 8 in down and 9 in down:
            down -= {8, 9}
            down.add(16)
        pad = self.pad
        pad.reset()
        for i in down:
            pad.press_button(button=self.buttons[i])
        pad.left_trigger_float(value_float=clamp(b[6], 0, 1) if len(b) > 6 else 0.0)
        pad.right_trigger_float(value_float=clamp(b[7], 0, 1) if len(b) > 7 else 0.0)
        ax = list(a) + [0, 0, 0, 0]
        pad.left_joystick_float(x_value_float=clamp(ax[0]), y_value_float=-clamp(ax[1]))
        pad.right_joystick_float(x_value_float=clamp(ax[2]), y_value_float=-clamp(ax[3]))
        pad.update()
        self.last = time.monotonic()
        self.active = True

    def release(self):
        if self.active:
            self.pad.reset()
            self.pad.update()
            self.active = False


# ---------------------------------------------------------------- network bridge

class Bridge:
    def __init__(self, vg, port, debug):
        self.vg = vg
        self.port = port
        self.debug = debug
        self.pads = {}
        self.tvs = {}  # websocket -> peer address
        self.loop = None
        self.stop_event = None
        self.on_change = lambda: None
        self.seen_ids = set()

    # status for the tray
    def status_text(self):
        if not self.tvs:
            return "Waiting for the TV app"
        live = sum(1 for p in self.pads.values() if p.ws is not None)
        tv = next(iter(self.tvs.values()))
        return f"TV connected ({tv}), {live} controller{'s' if live != 1 else ''}"

    def get_pad(self, idx):
        if idx not in self.pads:
            self.pads[idx] = Pad(self.vg, idx, self)
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
                if "hello" in msg:
                    await ws.send(json.dumps({"padthru": 1, "v": VERSION, "name": socket.gethostname()}))
                    hello = msg.get("hello") or {}
                    if hello.get("app") == "padthru-tv" and not verified:
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
            await asyncio.sleep(0.2)
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
        log.info("%s %s listening on port %d, addresses: %s", APP, VERSION, self.port,
                 ", ".join(local_addresses()) or "unknown")
        started["event"].set()
        await self.watchdog()
        server.close()
        await server.wait_closed()

    def stop(self):
        if self.loop and self.stop_event:
            self.loop.call_soon_threadsafe(self.stop_event.set)


# ---------------------------------------------------------------- tray

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
    ap = argparse.ArgumentParser(prog="Padthru", description="Padthru PC app")
    ap.add_argument("--console", action="store_true", help="no tray icon, log to the console")
    ap.add_argument("--debug", action="store_true", help="log every button and stick change")
    ap.add_argument("--port", type=int, default=PORT, help=f"TCP port (default {PORT}; the TV app expects {PORT})")
    args = ap.parse_args()

    setup_logging(console=args.console)

    try:
        import vgamepad as vg  # connects to ViGEmBus on import
    except Exception as e:  # noqa: BLE001
        log.error("ViGEmBus not available: %r", e)
        if message_box("Padthru needs the free ViGEmBus driver to create a virtual Xbox controller, "
                       "and it isn't installed (or isn't running).\n\n"
                       "Install it, restart Windows if asked, then start Padthru again.\n\n"
                       "Open the download page now?", yes_no=True, error=True):
            webbrowser.open(VIGEMBUS_URL)
        return 1

    bridge = Bridge(vg, args.port, args.debug)
    started = {"event": threading.Event(), "error": None}
    thread = threading.Thread(target=lambda: asyncio.run(bridge.main(started)), name="bridge", daemon=True)
    thread.start()
    started["event"].wait(10)
    if started["error"] is not None:
        log.error("cannot listen on port %d: %s", args.port, started["error"])
        message_box(f"Padthru could not start: port {args.port} is already in use.\n\n"
                    "Padthru is probably already running. Look for its icon next to the clock.", error=True)
        return 1

    if args.console:
        print(f"{APP} {VERSION} is running. This PC: {', '.join(local_addresses())}. Press Ctrl+C to quit.")
        try:
            while thread.is_alive():
                thread.join(0.5)
        except KeyboardInterrupt:
            bridge.stop()
        return 0

    run_tray(bridge)
    thread.join(3)
    return 0


if __name__ == "__main__":
    sys.exit(main())
