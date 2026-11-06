# How Padthru works

Padthru has two halves that talk over your home network.

## TV app (`tv/`)

A small Tizen web app.

1. **Picture.** On start it asks the TV for its HDMI inputs (`tizen.systeminfo`, property `VIDEOSOURCE`) and shows the chosen one full screen with the TV Window API (`tizen.tvwindow.setSource` + `show`, public `tv.window` privilege). The page itself is transparent, so the HDMI picture is drawn behind it. Because the TV is still showing an HDMI source, Game Mode, HDR and the full resolution and refresh rate stay in effect.
2. **Controllers.** It reads the controllers paired to the TV with the standard [Gamepad API](https://developer.mozilla.org/docs/Web/API/Gamepad_API) every 4 ms. It sends the state when something changes, and at least every 100 ms as a keep-alive.
3. **Finding the PC.** It first tries the last PC it connected to. Failing that, it opens a WebSocket to every address on the TV's own `/24` subnet, 32 at a time, on port **47811**, and keeps the first one that answers the Padthru handshake. You can also type the address under **Green → Settings**.

The TV's PS / Home button is handled by the TV itself and never reaches apps.

## PC app (`pc/padthru.py`)

A Python tray app, shipped as a single `Padthru.exe` built with PyInstaller.

- Listens on TCP **47811** (WebSocket) on all interfaces.
- Creates a virtual **Xbox 360** controller per TV controller with [ViGEmBus](https://github.com/nefarius/ViGEmBus) through [vgamepad](https://github.com/yannbouteiller/vgamepad).
- Maps the W3C *standard* gamepad layout onto Xbox buttons. **Share + Options** pressed together become the **Guide** button, since the TV keeps the PS button.
- Releases every input if the TV goes quiet for 600 ms (app closed, Wi-Fi drop), so nothing stays held down.
- Forwards rumble requests from games back to the TV.

## Protocol

JSON text messages over a WebSocket.

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
| `pc/padthru.py` | The PC app |
| `pc/vendor/vgamepad` | Vendored vgamepad 0.2.0 (see below) |
| `tools/build.ps1` | Builds `Padthru.wgt` and `Padthru.exe` |
| `tools/make_icon.py` | Draws the icons |
| `tools/lagtest.html` | Millisecond timer for latency tests |

**Why vendor vgamepad?** Installing it with pip launches the ViGEmBus installer wizard every time. The PC app only needs its Python code and `ViGEmClient.dll`, and users install ViGEmBus once themselves.
