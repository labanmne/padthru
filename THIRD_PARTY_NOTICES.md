# Third-party notices

Padthru includes or depends on the following software.

| Component | Used for | License |
|---|---|---|
| [vgamepad](https://github.com/yannbouteiller/vgamepad) 0.2.0 (vendored in `pc/vendor/vgamepad`) | Python bindings for ViGEm | MIT, © 2021 Yann Bouteiller (see `pc/vendor/vgamepad/LICENSE`) |
| [ViGEmClient](https://github.com/nefarius/ViGEmClient) (`ViGEmClient.dll`, shipped with vgamepad) | Talks to the ViGEmBus driver | MIT, © Benjamin Höglinger-Stelzer |
| [ViGEmBus](https://github.com/nefarius/ViGEmBus) (installed separately by the user) | Virtual controller driver | BSD-3-Clause, © Benjamin Höglinger-Stelzer |
| [websockets](https://github.com/python-websockets/websockets) (bundled in `Padthru.exe` and, unmodified, in `padthru-linux.pyz`) | WebSocket server | BSD-3-Clause, © Aymeric Augustin and contributors (license text included in the `.pyz` under `licenses/`) |
| [pystray](https://github.com/moses-palmer/pystray) | Tray icon | LGPL-3.0, © Moses Palmér |
| [Pillow](https://github.com/python-pillow/Pillow) | Tray icon image | MIT-CMU (HPND), © Jeffrey A. Clark and contributors |
| [PyInstaller](https://github.com/pyinstaller/pyinstaller) | Builds `Padthru.exe` | GPL-2.0-or-later with the bootloader exception, which allows distributing the built program under any license |

## About pystray (LGPL-3.0)

`Padthru.exe` bundles an unmodified copy of pystray. Under the LGPL you may replace it with your own build: all of Padthru's source and the build script (`tools/build.ps1`) are in this repository, so you can rebuild `Padthru.exe` against any version of pystray. pystray's source is available at <https://github.com/moses-palmer/pystray>.
