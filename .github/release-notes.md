**Any TV, the whole DualShock, no fiddling.** An Android TV or Google TV box can now be Padthru's controller side instead of the Samsung TV app. It works with any TV, passes the DualShock's **gyro, touchpad, light bar and battery** through to the PC, and in **background mode** it follows the TV's input by itself: switch the TV to the PC and the controller goes to the PC, switch back and it works on the box, which keeps playing whatever it plays.

### Downloads

| File | What it is |
|---|---|
| **Padthru.exe** | The Windows tray app. Needs the free [ViGEmBus](https://github.com/nefarius/ViGEmBus/releases/latest) driver. |
| **Padthru-android.apk** | **New:** the Android TV / Google TV box app (Android 12+). Install it with `adb install`, see [docs/android.md](https://github.com/labanmne/padthru/blob/main/docs/android.md). |
| **Padthru.wgt** | The Samsung TV app. Install it with [Apps2Samsung](https://github.com/Apps2Samsung/Apps2Samsung) (Windows, macOS, Linux) or Tizen Studio, which sign it for your TV. |
| **install-linux.sh** | The Linux installer. It downloads `padthru-linux.pyz`, checks it, and sets it up as a background service. |
| **padthru-linux.pyz** | The Linux app itself, one file, for Python 3.8 or newer. |
| SHA256SUMS.txt | Checksums of the files built by GitHub Actions |

Everything except the box app is built by GitHub Actions straight from this tag's source. `Padthru-android.apk` is built from the same source with `tools/build_android.sh` and signed with the project's key, so updates install over it; its SHA-256 is `e0d67983c57f942ec34d81b7a4364d801fe9e5e1f22d42a8493f8d7fdaeae876`.

### Setup

- **PC (Windows):** install ViGEmBus, run `Padthru.exe`. **Linux:** `curl -fsSLO https://github.com/labanmne/padthru/releases/latest/download/install-linux.sh && bash install-linux.sh`
- **Controller side, either:**
  - **Samsung TV:** turn on Developer Mode, install `Padthru.wgt`, open it.
  - **Android TV box:** turn on USB debugging, `adb install Padthru-android.apk`, open Padthru once and allow debugging from `padthru@box`. From then on, just switch the TV's input.

The [installation guide](https://github.com/labanmne/padthru#installation) has the details.

### New in 0.4.1
- **Controller auto-off, like a console.** In the box app's background mode, wireless controllers are switched off when the TV goes to standby (HDMI-CEC) or after 15 minutes without use; press PS to turn them back on. Set `auto_off_minutes` in `/data/local/tmp/padthru/settings.properties` on the box (0 = never). Android 13+.
- Box app fixes for Android 14 (Bluetooth access, controller lookup, rumble on boxes that don't allow writing to input devices).

### New in 0.4.0
- **Android TV box app** with the DualShock's motion sensors, touchpad (fingers and click), light bar and battery on the PC's virtual DualShock 4 (Windows). Measured on a Skyworth HY44G: a DualShock 4 on the box's USB port at its full 250 updates a second, about 44 over Bluetooth.
- **Background mode:** no app on screen; the controller follows the TV's input over HDMI-CEC, and the helper starts again by itself after reboots and app updates (tested through a reboot that also updated the box from Android 12 to 14).
- Tested on one box with a DualShock 4 so far. Please report your box and controller.

### Earlier: 0.3.0
PlayStation mode: a PlayStation controller shows up on the PC as a DualShock 4 (tray menu or `--pad` to change), and a controller on the Samsung TV's USB port reaches the PC at about 190 updates a second.
