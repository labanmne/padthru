# Changelog

All notable changes are listed here. The project follows [Semantic Versioning](https://semver.org/).

## [0.3.0] - 2026-11-07

### Added
- **PlayStation mode.** A PlayStation controller now shows up on the PC as a virtual **DualShock 4** (Windows: ViGEmBus DS4 target; Linux: a `uinput` device like `hid-playstation`'s), so games and Steam show PlayStation buttons. Other controllers stay Xbox 360 controllers. The tray menu (*PC sees the controller as*) or `--pad auto|xbox|playstation` changes it; the choice is saved. L2/R2 are analog plus digital, like on the real pad, and rumble works.
- The TV shows which controller the PC sees ("→ PC: PlayStation 4 controller").
- The TV app logs what the TV gives apps (controller buttons and axes, rumble, motion, touch, WebHID, WebUSB) to the PC log, and **Yellow** on the remote tests USB access, to help with other TV models.
- Tested: a DualShock 4 on the TV's **USB port** reaches the PC at about 190 updates a second, against about 42 over the TV's Bluetooth.

### Changed
- The DualShock touchpad click (when the TV reports it) is now the touchpad click in PlayStation mode, and still Back on an Xbox controller.
- The Linux tests check both controller kinds.

## [0.2.1] - 2026-11-06

### Fixed
- **Linux installer:** the download check failed, because the release's `SHA256SUMS.txt` was written with Windows line endings. The release build now writes Unix line endings, and the installer accepts both.

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

[0.2.1]: https://github.com/labanmne/padthru/releases/tag/v0.2.1
[0.2.0]: https://github.com/labanmne/padthru/releases/tag/v0.2.0
[0.1.0]: https://github.com/labanmne/padthru/releases/tag/v0.1.0
