First public release of **Padthru**: play PC games on your Samsung TV with the controller that's already paired to the TV. The picture stays on your HDMI connection, and only the controller travels over your home network.

### Downloads

| File | What it is |
|---|---|
| **Padthru.exe** | The Windows tray app. Needs the free [ViGEmBus](https://github.com/nefarius/ViGEmBus/releases/latest) driver. |
| **Padthru.wgt** | The Samsung TV app. Install it with [Apps2Samsung](https://github.com/Apps2Samsung/Apps2Samsung) or Tizen Studio, which sign it for your TV. |
| SHA256SUMS.txt | Checksums |

These files are built by GitHub Actions straight from this tag's source.

### Setup

Follow the [installation guide](https://github.com/labanmne/padthru#installation). In short: install ViGEmBus, run Padthru.exe, turn on Developer Mode on the TV, install Padthru.wgt, open it.

### What's in 0.1.0
- **Picture:** the HDMI input is shown full screen inside the TV app, keeping Game Mode, 4K, HDR and high refresh rates.
- **Controllers:** up to four controllers paired to the TV appear on the PC as Xbox 360 controllers.
- **Automatic PC discovery,** plus manual address entry (**Green** on the remote).
- **Guide button:** Share + Options act as Guide, because the TV keeps the PS button.
- **Rumble forwarding** (experimental).

Tested on a Samsung Q70A (2021, Tizen 6.0) with a DualShock 4. Please report your TV model with the "Works on my TV" issue template.
