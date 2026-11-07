# How Padthru works

Padthru has two halves that talk over your home network.

## TV app (`tv/`)

A small Tizen web app.

1. **Picture.** On start it asks the TV for its HDMI inputs (`tizen.systeminfo`, property `VIDEOSOURCE`) and shows the chosen one full screen with the TV Window API (`tizen.tvwindow.setSource` + `show`, public `tv.window` privilege). The page itself is transparent, so the HDMI picture is drawn behind it. Because the TV is still showing an HDMI source, Game Mode, HDR and the full resolution and refresh rate stay in effect.
2. **Controllers.** It reads the controllers paired to the TV with the standard [Gamepad API](https://developer.mozilla.org/docs/Web/API/Gamepad_API) every 4 ms. It sends the state when something changes, and at least every 100 ms as a keep-alive.
3. **Finding the PC.** It first tries the last PC it connected to. Failing that, it asks the TV for its own address and sends a small HTTP request (`GET /padthru`) to every address on that `/24` subnet, 32 at a time, on port **47811**. Only the PC that answers gets the WebSocket connection. A full search takes about 10 seconds. You can also type the address under **Green → Settings**.

   *Why HTTP and not WebSockets for the search?* The TV's browser deliberately slows down new WebSocket attempts after many failed ones (a defense against port scanning). A WebSocket-based search therefore often walked right past the PC.

The TV's PS / Home button is handled by the TV itself and never reaches apps.

## PC app (`pc/`)

One Python program with a small backend per operating system:

| | Windows | Linux |
|---|---|---|
| Shipped as | `Padthru.exe` (PyInstaller, tray app) | `padthru-linux.pyz` (zipapp, systemd user service) |
| Virtual controller | [ViGEmBus](https://github.com/nefarius/ViGEmBus) through [vgamepad](https://github.com/yannbouteiller/vgamepad) (`vpad_windows.py`): an Xbox 360 or DualShock 4 target | the kernel's `uinput`, pure Python `ioctl` calls (`vpad_linux.py`): looks like the `xpad` or `hid-playstation` driver's device |
| Rumble from games | ViGEm notification callback | `FF_RUMBLE` effects uploaded to the virtual device |

What both do:

- Listen on TCP **47811** (WebSocket) on all interfaces.
- Create a virtual controller per TV controller. In the default *auto* mode, a Sony controller (vendor `054c` in the TV's controller ID) gets a **DualShock 4** and anything else an **Xbox 360** controller. `--pad` or the tray menu forces one; the choice is saved in `settings.json` next to the log. If the kind changes, the virtual controller is replaced.
- Map the W3C *standard* gamepad layout onto it. **Share + Options** pressed together become the **Guide** / **PS** button, since the TV keeps the PS button. Button 17 (the DualShock touchpad click, when the TV reports it) becomes the touchpad click, or Back on an Xbox controller.
- Release every input if the TV goes quiet for 600 ms (app closed, Wi-Fi drop), so nothing stays held down.
- Forward rumble requests from games back to the TV.

## Protocol

JSON text messages over a WebSocket.

Discovery: `GET http://<pc>:47811/padthru` returns `{"padthru": 1, "v": "0.3.0", "name": "MY-PC"}`.

| Direction | Message | Meaning |
|---|---|---|
| TV → PC | `{"hello": {"app": "padthru-tv", "v": "0.1.0"}}` | Handshake. Nothing else is accepted before it. |
| PC → TV | `{"padthru": 1, "v": "0.1.0", "name": "MY-PC"}` | Handshake reply; `name` is shown on the TV. |
| TV → PC | `{"i": 0, "b": [..], "a": [..]}` | Controller `i` (0–3): button values `b` (0–1) and axes `a` (−1–1) in standard-mapping order. The first message also carries `id` and `m` (mapping). |
| TV → PC | `{"i": 0, "gone": 1}` | Controller disconnected; release its inputs. |
| PC → TV | `{"r": [0, large, small]}` | Rumble for controller 0, motor strengths 0–255. |
| TV → PC | `{"log": "..."}` | Diagnostic line, written to the PC log. |
| TV → PC / PC → TV | `{"ping": t}` / `{"pong": t}` | Round-trip check. |

## Files

| Path | What it is |
|---|---|
| `tv/config.xml` | Tizen manifest (package `PadthruApp`, privileges) |
| `tv/main.js` | Everything the TV app does |
| `pc/padthru.py` | The PC app: network, controller mapping, tray (Windows) |
| `pc/vpad_windows.py`, `pc/vpad_linux.py` | Virtual controller backends |
| `pc/vendor/vgamepad` | Vendored vgamepad 0.2.0 (see below) |
| `linux/install.sh` | Linux installer, published as `install-linux.sh` |
| `tools/build.ps1` | Builds all release files |
| `tools/build_pyz.py` | Builds `padthru-linux.pyz` (any OS) |
| `tools/fake_tv.py` | Acts like the TV app, for testing without a TV |
| `tools/make_icon.py` | Draws the icons |
| `tools/lagtest.html` | Millisecond timer for latency tests |

**Why vendor vgamepad?** Installing it with pip launches the ViGEmBus installer wizard every time. The PC app only needs its Python code and `ViGEmClient.dll`, and users install ViGEmBus once themselves.
