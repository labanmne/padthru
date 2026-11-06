"""Pretend to be the Padthru TV app, to test the PC side without a Samsung TV.

    python3 tools/fake_tv.py [PC address]        (default: 127.0.0.1)

It connects the way the TV does, then presses every button, moves both sticks
and pulls the triggers of one controller, slowly enough to follow in a
controller test screen (Steam: Settings > Controller; Windows: joy.cpl;
Linux: evtest or jstest-gtk). Rumble sent back by a game is printed.

Needs the `websockets` package. On Linux it uses the copy inside an installed
Padthru, so nothing extra is needed there.
"""
import asyncio
import json
import math
import os
import sys

try:
    import websockets
except ImportError:
    pyz = os.path.join(os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share"), "padthru", "padthru.pyz")
    if not os.path.exists(pyz):
        sys.exit("Please install websockets first: pip install websockets")
    sys.path.insert(0, pyz)
    import websockets

PORT = 47811
BUTTONS = ["A", "B", "X", "Y", "LB", "RB", "LT", "RT", "Back", "Start", "LS", "RS", "Up", "Down", "Left", "Right"]


async def main(host):
    state = {"b": [0.0] * 17, "a": [0.0] * 4}
    async with websockets.connect(f"ws://{host}:{PORT}/", open_timeout=5) as ws:
        await ws.send(json.dumps({"hello": {"app": "padthru-tv", "v": "fake-tv"}}))
        reply = json.loads(await ws.recv())
        print(f"Connected to {reply.get('name')} (Padthru {reply.get('v')})")

        async def send_loop():
            first = True
            while True:
                msg = {"i": 0, "b": state["b"], "a": state["a"]}
                if first:
                    msg.update(id="Padthru fake TV", m="standard")
                    first = False
                await ws.send(json.dumps(msg))
                await asyncio.sleep(1 / 30)

        async def recv_loop():
            async for raw in ws:
                msg = json.loads(raw)
                if "r" in msg:
                    print(f"  rumble from a game: strong {msg['r'][1]}, weak {msg['r'][2]}")

        tasks = [asyncio.create_task(send_loop()), asyncio.create_task(recv_loop())]
        await asyncio.sleep(1)
        for i, name in enumerate(BUTTONS):
            print("press", name)
            state["b"][i] = 1.0
            await asyncio.sleep(0.5)
            state["b"][i] = 0.0
            await asyncio.sleep(0.25)
        print("press Share + Options (Guide)")
        state["b"][8] = state["b"][9] = 1.0
        await asyncio.sleep(0.5)
        state["b"][8] = state["b"][9] = 0.0
        for stick in (0, 2):
            print("circle", "left stick" if stick == 0 else "right stick")
            for step in range(60):
                t = step / 60 * 2 * math.pi
                state["a"][stick], state["a"][stick + 1] = round(math.cos(t), 3), round(math.sin(t), 3)
                await asyncio.sleep(1 / 30)
            state["a"][stick] = state["a"][stick + 1] = 0.0
        print("Done. Leaving the controller connected for 10 s so you can test rumble in a game; Ctrl+C to quit.")
        await asyncio.sleep(10)
        for t in tasks:
            t.cancel()


if __name__ == "__main__":
    sys.stdout.reconfigure(line_buffering=True)
    try:
        asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else "127.0.0.1"))
    except KeyboardInterrupt:
        pass
    except OSError as e:
        sys.exit(f"Could not reach Padthru: {e}")
