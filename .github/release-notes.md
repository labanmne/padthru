**PlayStation mode.** Play PC games on your Samsung TV with the controller that's already paired to (or plugged into) the TV. A PlayStation controller now shows up on the PC as a **DualShock 4**, with PlayStation button prompts in games and Steam. The picture stays on your HDMI connection, and only the controller travels over your home network.

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

### New in 0.3.0
- **PlayStation mode.** By default, a PlayStation controller becomes a virtual DualShock 4 and anything else an Xbox 360 controller. Change it in the tray menu (*PC sees the controller as*) or with `--pad xbox` / `--pad playstation`. Rumble works in both.
- **Plug the controller into the TV for lower lag.** On a Q70A, a DualShock 4 on the TV's USB port reaches the PC at about 190 updates a second, against about 42 over the TV's Bluetooth. No setting needed.
- The TV shows which controller the PC sees, and logs what your TV offers apps to the PC log (press **Yellow** to also test USB access). Motion sensors, touchpad and light bar aren't available: the TV doesn't give apps that data.

Update both sides: the PC app for PlayStation mode, the TV app for the new info. Older TV apps keep working with the new PC app.
