"""End-to-end test of the Linux PC app: act as the TV, watch the virtual controller it creates.

Start Padthru first, then run this as root (it reads /dev/input/event*):

    python3 dist/padthru-linux.pyz &
    sudo python3 tests/linux_e2e.py dist/padthru-linux.pyz [port]
"""
import asyncio
import ctypes
import fcntl
import json
import os
import re
import sys
import time
import urllib.request

PYZ = sys.argv[1]
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 47811
sys.path.insert(0, PYZ)
import websockets  # noqa: E402  (from the pyz)
import vpad_linux as V  # noqa: E402  (structs)

EV_NAMES = {(1, v): k for k, v in V.KEYS.items()}
EV_NAMES.update({(3, 0): "LX", (3, 1): "LY", (3, 2): "LT", (3, 3): "RX", (3, 4): "RY", (3, 5): "RT",
                 (3, 0x10): "HATX", (3, 0x11): "HATY"})
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


def ioc(d, t, nr, size):
    return (d << 30) | (size << 16) | (ord(t) << 8) | nr


EVIOCGID = ioc(2, "E", 0x02, ctypes.sizeof(V.input_id))
EVIOCGNAME = ioc(2, "E", 0x06, 256)
EVIOCSFF = ioc(1, "E", 0x80, ctypes.sizeof(V.ff_effect))


def find_event_nodes():
    text = open("/proc/bus/input/devices").read()
    nodes = []
    for block in text.strip().split("\n\n"):
        if 'Name="Microsoft X-Box 360 pad"' in block:
            m = re.search(r"H: Handlers=.*?(event\d+)", block)
            if m:
                nodes.append("/dev/input/" + m.group(1))
    return nodes


def msg(b=None, a=None, i=0):
    bb = [0.0] * 17
    for k, v in (b or {}).items():
        bb[k] = v
    return json.dumps({"i": i, "b": bb, "a": a or [0, 0, 0, 0], "id": "Wireless Controller (STANDARD GAMEPAD)", "m": "standard"})


def read_events(fd):
    out = {}
    while True:
        try:
            data = os.read(fd, V.EVENT_SIZE * 64)
        except BlockingIOError:
            break
        for off in range(0, len(data), V.EVENT_SIZE):
            ev = V.input_event.from_buffer_copy(data, off)
            if ev.type in (1, 3):
                out[EV_NAMES.get((ev.type, ev.code), (ev.type, ev.code))] = ev.value
    return out


async def run():
    body = urllib.request.urlopen(f"http://127.0.0.1:{PORT}/padthru", timeout=3).read()
    d = json.loads(body)
    check("HTTP discovery GET /padthru", d.get("padthru") == 1, body.decode())

    async with websockets.connect(f"ws://127.0.0.1:{PORT}/") as ws:
        await ws.send(json.dumps({"hello": {"app": "padthru-tv", "v": "e2e"}}))
        reply = json.loads(await ws.recv())
        check("WebSocket hello", reply.get("padthru") == 1, json.dumps(reply))

        before = set(find_event_nodes())
        await ws.send(msg())
        node = None
        for _ in range(30):
            await asyncio.sleep(0.1)
            new = set(find_event_nodes()) - before
            if new:
                node = sorted(new)[0]
                break
        check("virtual controller appears", node is not None, node or "no new device")
        if not node:
            return
        for _ in range(30):
            if os.path.exists(node):
                break
            await asyncio.sleep(0.1)
        fd = os.open(node, os.O_RDWR | os.O_NONBLOCK)
        ident = V.input_id()
        buf = bytearray(ident)
        fcntl.ioctl(fd, EVIOCGID, buf, True)
        ident = V.input_id.from_buffer_copy(buf)
        nb = bytearray(256)
        fcntl.ioctl(fd, EVIOCGNAME, nb, True)
        name = nb.split(b"\0", 1)[0].decode()
        check("identifies as a wired Xbox 360 controller",
              (ident.bustype, ident.vendor, ident.product) == (3, 0x045E, 0x028E) and name == "Microsoft X-Box 360 pad",
              f"bus {ident.bustype:#x} {ident.vendor:04x}:{ident.product:04x} v{ident.version:04x} '{name}'")
        read_events(fd)

        async def step(label, expect, **kw):
            await ws.send(msg(**kw))
            await asyncio.sleep(0.08)
            got = read_events(fd)
            ok = all(got.get(k) == v for k, v in expect.items())
            check(label, ok, f"got {got}")

        await step("A button", {"A": 1}, b={0: 1})
        await step("A released", {"A": 0})
        await step("B X Y LB RB", {"B": 1, "X": 1, "Y": 1, "LB": 1, "RB": 1}, b={1: 1, 2: 1, 3: 1, 4: 1, 5: 1})
        await step("stick clicks", {"LS": 1, "RS": 1, "B": 0, "X": 0, "Y": 0, "LB": 0, "RB": 0}, b={10: 1, 11: 1})
        await step("left stick right+up", {"LX": 32767, "LY": -32767, "LS": 0, "RS": 0}, a=[1, -1, 0, 0])
        await step("right stick left+down", {"RX": -32767, "RY": 32767, "LX": 0, "LY": 0}, a=[0, 0, -1, 1])
        await step("triggers (full / half)", {"LT": 255, "RT": 128, "RX": 0, "RY": 0}, b={6: 1.0, 7: 0.5})
        await step("D-pad up+left", {"HATX": -1, "HATY": -1, "LT": 0, "RT": 0}, b={12: 1, 14: 1})
        await step("D-pad down+right", {"HATX": 1, "HATY": 1}, b={13: 1, 15: 1})
        await step("Share = Back, Options = Start", {"BACK": 1, "HATX": 0, "HATY": 0}, b={8: 1})
        await step("Share + Options = Guide", {"GUIDE": 1, "BACK": 0}, b={8: 1, 9: 1})
        await ws.send(msg(b={0: 1}, a=[0.5, 0, 0, 0]))
        await asyncio.sleep(0.08)
        read_events(fd)
        await asyncio.sleep(1.0)  # TV goes quiet -> watchdog releases everything
        got = read_events(fd)
        check("inputs released when the TV goes quiet", got.get("A") == 0 and got.get("LX") == 0, f"got {got}")

        # Rumble, the way a game does it: upload an FF_RUMBLE effect to the event device and play it.
        eff = V.ff_effect()
        eff.type, eff.id = V.FF_RUMBLE, -1
        eff.replay.length = 400
        eff.u.rumble.strong_magnitude, eff.u.rumble.weak_magnitude = 0xFFFF, 0x8000
        buf = bytearray(eff)
        fcntl.ioctl(fd, EVIOCSFF, buf, True)
        eid = V.ff_effect.from_buffer_copy(buf).id
        check("game can upload a rumble effect", eid >= 0, f"effect id {eid}")
        os.write(fd, bytes(V.input_event(V.timeval(0, 0), V.EV_FF, eid, 1)))
        t0 = time.monotonic()
        msgs = []
        try:
            while time.monotonic() - t0 < 1.5 and len(msgs) < 2:
                m = json.loads(await asyncio.wait_for(ws.recv(), 1.5))
                if "r" in m:
                    msgs.append((round(time.monotonic() - t0, 2), m["r"]))
        except asyncio.TimeoutError:
            pass
        check("rumble reaches the TV", bool(msgs) and msgs[0][1] == [0, 255, 128], str(msgs))
        check("rumble stops after the effect's length",
              len(msgs) > 1 and msgs[1][1] == [0, 0, 0] and 0.3 <= msgs[1][0] <= 0.7, str(msgs))
        os.close(fd)

    await asyncio.sleep(0.5)


asyncio.run(run())
print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
