# Changelog

All notable changes are listed here. The project follows [Semantic Versioning](https://semver.org/).

## [0.2.0] - 2026-11-06

### Added
- **Linux PC app.** It creates a virtual Xbox 360 controller through the kernel's `uinput`, in pure Python with no dependencies, shipped as a single `padthru-linux.pyz` that needs Python 3.8 or newer. Steam, Proton/Wine and SDL games see it as a wired Xbox 360 controller. Rumble from games is forwarded to the TV.
- **Linux installer** (`install-linux.sh`). It checks the download against the release checksums, sets up a systemd user service, adds Steam's udev rule for `/dev/uinput` only when needed, and opens the port in `ufw` or `firewalld` if one is active. `--uninstall` removes all of it.
- **`tools/fake_tv.py`** acts like the TV app, so the PC side can be tested without a Samsung TV.
- `--version` option.

### Changed
- The PC app is now one shared program with a virtual-controller backend per operating system (`vpad_windows.py`, `vpad_linux.py`). The Windows app behaves as before.

## [0.1.0] - 2026-11-06

First public release.

### Added
- **TV app.** It shows an HDMI input full screen inside the app, keeping Game Mode, 4K, HDR and high refresh rates.
- **Controller forwarding.** It forwards up to four controllers paired to the TV to the PC as Xbox 360 controllers.
- **Automatic PC discovery** on the TV's subnet, plus manual address entry under **Green → Settings**.
- **Remote shortcuts:** **1–4** picks the HDMI input, **Red** shows or hides the info panel.
- **PC tray app** with "Start with Windows", a log file, and clear messages when ViGEmBus is missing or the port is in use.
- **Share + Options as Guide.** Together they act as the Xbox Guide button, because the TV keeps the PS button for itself.
- **Rumble forwarding** from games to the TV (experimental).

[0.2.0]: https://github.com/labanmne/padthru/releases/tag/v0.2.0
[0.1.0]: https://github.com/labanmne/padthru/releases/tag/v0.1.0
