**Padthru now runs on Linux too.** Play PC games on your Samsung TV with the controller that's already paired to the TV. The picture stays on your HDMI connection, and only the controller travels over your home network.

### Downloads

| File | What it is |
|---|---|
| **Padthru.exe** | The Windows tray app. Needs the free [ViGEmBus](https://github.com/nefarius/ViGEmBus/releases/latest) driver. |
| **install-linux.sh** | The Linux installer. It downloads `padthru-linux.pyz`, checks it, and sets it up as a background service. |
| **padthru-linux.pyz** | The Linux app itself, one file, for Python 3.8 or newer. Run it with `python3 padthru-linux.pyz`. |
| **Padthru.wgt** | The Samsung TV app. Install it with [Apps2Samsung](https://github.com/Apps2Samsung/Apps2Samsung) (Windows, macOS, Linux) or Tizen Studio, which sign it for your TV. |
| SHA256SUMS.txt | Checksums |

These files are built by GitHub Actions straight from this tag's source.

### Setup

- **Windows:** install ViGEmBus, run `Padthru.exe`.
- **Linux:**
  ```bash
  curl -fsSLO https://github.com/labanmne/padthru/releases/latest/download/install-linux.sh
  bash install-linux.sh
  ```
- **TV:** turn on Developer Mode, install `Padthru.wgt`, open it.

The [installation guide](https://github.com/labanmne/padthru#installation) has the details, and [docs/linux.md](https://github.com/labanmne/padthru/blob/main/docs/linux.md) covers Linux.

### What's new in 0.2.0
- **Linux PC app.** It creates a virtual Xbox 360 controller through the kernel's `uinput`, so Steam, Proton and SDL games see a wired Xbox 360 controller. Rumble from games reaches the TV. It's pure Python with no dependencies.
- **Linux installer** with a systemd user service, Steam's udev rule (only if needed), firewall handling, and `--uninstall`.
- **`tools/fake_tv.py`** for testing the PC side without a TV.

The Linux app was tested end to end on Ubuntu 24.04 with a simulated TV, including through SDL 2.30, the library most games use. It hasn't run on a real Linux gaming setup yet, so please report your distro with the "Works on my TV" template, even if everything just works. The TV app is unchanged apart from its version number, so 0.1.0 TV installs keep working with both PC apps.
