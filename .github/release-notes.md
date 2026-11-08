**Plug a controller into the PC and Padthru steps aside.** Playing at the desk with a controller on the PC's USB cable or Bluetooth used to leave games with two controllers: the real one and Padthru's idle virtual one, and some games listened to the wrong one. Now the PC app notices a controller connected to the PC itself, unplugs its virtual controller and ignores the TV until that controller is gone. Unplug it and the TV's controller works again from its next button press. Nothing to set up.

### Downloads

| File | What it is |
|---|---|
| **Padthru.exe** | The Windows tray app. Needs the free [ViGEmBus](https://github.com/nefarius/ViGEmBus/releases/latest) driver. |
| **Padthru-android.apk** | The Android TV / Google TV box app (Android 12+). Install it with `adb install`, see [docs/android.md](https://github.com/labanmne/padthru/blob/main/docs/android.md). |
| **Padthru.wgt** | The Samsung TV app. Install it with [Apps2Samsung](https://github.com/Apps2Samsung/Apps2Samsung) (Windows, macOS, Linux) or Tizen Studio, which sign it for your TV. |
| **install-linux.sh** | The Linux installer. It downloads `padthru-linux.pyz`, checks it, and sets it up as a background service. |
| **padthru-linux.pyz** | The Linux app itself, one file, for Python 3.8 or newer. |
| SHA256SUMS.txt | Checksums of the files built by GitHub Actions |

Everything except the box app is built by GitHub Actions straight from this tag's source. `Padthru-android.apk` is built from the same source with `tools/build_android.sh` and signed with the project's key, so updates install over it; its SHA-256 is `54555d50b754155c1d2b69d80d0be6961c6c0b5eb0469cc8f7df9012da50e5fd`.

Only the PC app changed in this release; the TV and box apps just carry the new version number, so updating them is optional.

### Setup

- **PC (Windows):** install ViGEmBus, run `Padthru.exe`. **Linux:** `curl -fsSLO https://github.com/labanmne/padthru/releases/latest/download/install-linux.sh && bash install-linux.sh`
- **Controller side, either:**
  - **Samsung TV:** turn on Developer Mode, install `Padthru.wgt`, open it.
  - **Android TV box:** turn on USB debugging, `adb install Padthru-android.apk`, open Padthru once and allow debugging from `padthru@box`. From then on, just switch the TV's input.

The [installation guide](https://github.com/labanmne/padthru#installation) has the details.

### New in 0.5.0
- **Step aside for a controller plugged into the PC** (Windows and Linux). The PC app looks every 2 seconds (about 1.5 ms each time) for game controllers connected to the PC itself, over USB or Bluetooth. While one is there, its virtual controllers are unplugged and input from the TV or box is ignored; the tray shows *Paused: … is plugged into this PC*. Virtual controllers from other programs (Steam Input, DS4Windows, ViGEm) don't count. On by default; switch it off in the tray (*Step aside when a controller is plugged into this PC*) or with `--step-aside off`.
- `tests/step_aside.py` checks the whole flow with a fake TV, the Linux detection on a simulated `/sys`, and the Windows detection next to a ViGEm DualShock 4.

### Earlier: 0.4.x
- **0.4.1:** controller auto-off in the box app's background mode (TV standby or 15 idle minutes), and fixes for Android 14 boxes.
- **0.4.0:** the Android TV box app, with the DualShock's motion sensors, touchpad, light bar and battery on the PC's virtual DualShock 4 (Windows), and background mode that follows the TV's input over HDMI-CEC.

### Earlier: 0.3.0
PlayStation mode: a PlayStation controller shows up on the PC as a DualShock 4 (tray menu or `--pad` to change), and a controller on the Samsung TV's USB port reaches the PC at about 190 updates a second.
