"""Check the Linux virtual controller the way games see it: through SDL2, which Steam, Proton
and most native games use. Acts as the TV over the network and reads the controller with
SDL's GameController API. Needs libSDL2 (package libsdl2-2.0-0 or SDL2).

Start Padthru first (default controller type: auto), then run this as root. "playstation"
pretends the TV has a Sony controller, so the PC should get a DualShock 4:

    sudo python3 tests/linux_sdl.py dist/padthru-linux.pyz [xbox|playstation]
"""
import asyncio
import ctypes
import json
import sys
import time

if len(sys.argv) <= 2:  # no kind given: test both, each in its own run
    import os
    import subprocess
    rc = 0
    for kind in ("xbox", "playstation"):
        print(f"=== virtual {kind} controller", flush=True)
        rc |= subprocess.call([sys.executable, os.path.abspath(__file__), sys.argv[1], kind])
    sys.exit(rc)
sys.path.insert(0, sys.argv[1])
KIND = sys.argv[2]
TV_ID = {"xbox": "Wireless Gamepad (STANDARD GAMEPAD Vendor: 0079 Product: 18d4)",
         "playstation": "Sony Interactive Entertainment Wireless Controller (STANDARD GAMEPAD Vendor: 054c Product: 09cc)"}[KIND]
EXPECT_TYPE = {"xbox": "Xbox 360", "playstation": "PS4"}[KIND]
# A DualShock 4's sticks run 0-255 with 128 as centre, which SDL reads as +128 at rest.
REST = {"xbox": 1, "playstation": 200}[KIND]
import websockets  # noqa: E402

sdl = ctypes.CDLL("libSDL2-2.0.so.0")
sdl.SDL_JoystickNameForIndex.restype = ctypes.c_char_p
sdl.SDL_GameControllerOpen.restype = ctypes.c_void_p
sdl.SDL_GameControllerName.restype = ctypes.c_char_p
sdl.SDL_GameControllerName.argtypes = [ctypes.c_void_p]
sdl.SDL_GameControllerMapping.restype = ctypes.c_char_p
sdl.SDL_GameControllerMapping.argtypes = [ctypes.c_void_p]
sdl.SDL_GameControllerGetType.argtypes = [ctypes.c_void_p]
sdl.SDL_GameControllerGetButton.argtypes = [ctypes.c_void_p, ctypes.c_int]
sdl.SDL_GameControllerGetAxis.argtypes = [ctypes.c_void_p, ctypes.c_int]
sdl.SDL_GameControllerGetAxis.restype = ctypes.c_int16
sdl.SDL_GameControllerRumble.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint16, ctypes.c_uint32]
sdl.SDL_GameControllerHasRumble.argtypes = [ctypes.c_void_p]
sdl.SDL_GetError.restype = ctypes.c_char_p


class SDL_version(ctypes.Structure):
    _fields_ = [("major", ctypes.c_uint8), ("minor", ctypes.c_uint8), ("patch", ctypes.c_uint8)]


BTN = {"A": 0, "B": 1, "X": 2, "Y": 3, "BACK": 4, "GUIDE": 5, "START": 6, "LS": 7, "RS": 8, "LB": 9, "RB": 10,
       "UP": 11, "DOWN": 12, "LEFT": 13, "RIGHT": 14}
AX = {"LX": 0, "LY": 1, "RX": 2, "RY": 3, "LT": 4, "RT": 5}
TYPES = {0: "unknown", 1: "Xbox 360", 2: "Xbox One", 3: "PS3", 4: "PS4", 5: "Switch Pro", 6: "virtual", 7: "PS5"}
results = []


def check(name, ok, detail=""):
    results.append(ok)
    print(("PASS " if ok else "FAIL ") + name + (f"  [{detail}]" if detail else ""))


state = {"b": [0.0] * 17, "a": [0, 0, 0, 0]}


async def sender(ws):
    while True:
        await ws.send(json.dumps({"i": 0, "b": state["b"], "a": state["a"], "id": TV_ID, "m": "standard"}))
        await asyncio.sleep(0.03)


def set_state(b=None, a=None):
    bb = [0.0] * 17
    for k, v in (b or {}).items():
        bb[k] = v
    state["b"], state["a"] = bb, a or [0, 0, 0, 0]


async def settle(ctrl=None, t=0.2):
    end = time.monotonic() + t
    while time.monotonic() < end:
        sdl.SDL_PumpEvents()
        if ctrl:
            sdl.SDL_GameControllerUpdate()
        await asyncio.sleep(0.01)


async def run():
    v = SDL_version()
    sdl.SDL_GetVersion(ctypes.byref(v))
    print(f"SDL {v.major}.{v.minor}.{v.patch}")
    async with websockets.connect("ws://127.0.0.1:47811/") as ws:
        await ws.send(json.dumps({"hello": {"app": "padthru-tv", "v": "sdl-test"}}))
        await ws.recv()
        task = asyncio.create_task(sender(ws))
        await asyncio.sleep(0.5)  # virtual controller gets created
        assert sdl.SDL_Init(0x200 | 0x2000 | 0x4000) == 0, sdl.SDL_GetError()
        await settle(t=0.5)
        idx = None
        for i in range(sdl.SDL_NumJoysticks()):
            if sdl.SDL_JoystickNameForIndex(i):
                idx = i
        check("SDL sees a joystick", idx is not None, f"{sdl.SDL_NumJoysticks()} found")
        if idx is None:
            return
        check("SDL treats it as a game controller (has a mapping)", sdl.SDL_IsGameController(idx) == 1)
        ctrl = sdl.SDL_GameControllerOpen(idx)
        name = sdl.SDL_GameControllerName(ctrl).decode()
        ctype = TYPES.get(sdl.SDL_GameControllerGetType(ctrl), "?")
        check(f"SDL identifies it as a{'n' if EXPECT_TYPE[0] in 'AEIOUX' else ''} {EXPECT_TYPE} controller",
              ctype == EXPECT_TYPE, f"name '{name}', type {ctype}")
        print("     mapping:", sdl.SDL_GameControllerMapping(ctrl).decode()[:160], "...")

        async def expect(label, b=None, a=None, buttons=(), axes=None):
            set_state(b, a)
            await settle(ctrl, 0.25)
            got_b = {k: sdl.SDL_GameControllerGetButton(ctrl, n) for k, n in BTN.items()}
            got_a = {k: sdl.SDL_GameControllerGetAxis(ctrl, n) for k, n in AX.items()}
            ok = all(got_b[k] == (1 if k in buttons else 0) for k in BTN)
            ok = ok and all(abs(got_a[k] - v) <= (REST if v == 0 and k[1] in "XY" else 1)
                            for k, v in (axes or {}).items())
            pressed = [k for k, v in got_b.items() if v]
            check(label, ok, f"buttons {pressed}, axes {got_a}")

        await expect("A / B / X / Y", b={0: 1, 1: 1, 2: 1, 3: 1}, buttons=("A", "B", "X", "Y"))
        await expect("bumpers and stick clicks", b={4: 1, 5: 1, 10: 1, 11: 1}, buttons=("LB", "RB", "LS", "RS"))
        await expect("Back and Start", b={8: 1}, buttons=("BACK",))
        await expect("Start", b={9: 1}, buttons=("START",))
        await expect("Share + Options = Guide", b={8: 1, 9: 1}, buttons=("GUIDE",))
        await expect("D-pad up+left", b={12: 1, 14: 1}, buttons=("UP", "LEFT"))
        await expect("D-pad down+right", b={13: 1, 15: 1}, buttons=("DOWN", "RIGHT"))
        await expect("left stick right+up, right stick left+down", a=[1, -1, -1, 1],
                     axes={"LX": 32767, "LY": -32767, "RX": -32767, "RY": 32767})
        await expect("triggers full", b={6: 1, 7: 1}, axes={"LT": 32767, "RT": 32767})
        await expect("everything released", axes={"LX": 0, "LY": 0, "RX": 0, "RY": 0, "LT": 0, "RT": 0})

        check("SDL reports rumble support", sdl.SDL_GameControllerHasRumble(ctrl) == 1)
        task.cancel()
        rc = sdl.SDL_GameControllerRumble(ctrl, 0xFFFF, 0x8000, 300)
        check("SDL_GameControllerRumble succeeds", rc == 0, (sdl.SDL_GetError() or b"").decode())
        t0, msgs = time.monotonic(), []
        while time.monotonic() - t0 < 1.2:
            sdl.SDL_GameControllerUpdate()  # SDL ends the rumble from here
            try:
                m = json.loads(await asyncio.wait_for(ws.recv(), 0.02))
                if "r" in m:
                    msgs.append((round(time.monotonic() - t0, 2), m["r"]))
            except asyncio.TimeoutError:
                pass
        check("game rumble reaches the TV", bool(msgs) and msgs[0][1] == [0, 255, 128], str(msgs))
        check("rumble stops when the game's duration ends", bool(msgs) and msgs[-1][1] == [0, 0, 0], str(msgs))
        sdl.SDL_GameControllerClose(ctypes.c_void_p(ctrl))
    sdl.SDL_Quit()


asyncio.run(run())
print(f"\n{sum(results)}/{len(results)} checks passed")
sys.exit(0 if all(results) else 1)
