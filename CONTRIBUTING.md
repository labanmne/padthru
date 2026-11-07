# Contributing

Thanks for taking a look! Padthru is a small hobby project, and help is welcome.

## Reports that help most

- **"It works on my TV."** Open an issue with your TV model, year and Tizen version (*Settings → Support → About This TV*), plus the controller you used. A list of tested models helps everyone.
- **"It works on my Linux distro."** Linux support is new, so reports from SteamOS, Bazzite, Fedora, Arch and the rest are especially useful.
- **Bugs.** Use the bug report template and attach the relevant part of the log (`%LOCALAPPDATA%\Padthru\padthru.log` on Windows, `journalctl --user -u padthru` on Linux). Running with `--debug` makes the log much more useful.

## Code

- **TV app** (`tv/`): plain ES5-style JavaScript, so it runs on older Tizen browsers. Test changes on a real TV; the TV Window API doesn't exist in desktop browsers.
- **PC app** (`pc/`): `padthru.py` is shared, `vpad_windows.py` and `vpad_linux.py` create the virtual controllers. Keep `padthru.py` and `vpad_linux.py` compatible with Python 3.8. Run from source with `python pc/padthru.py --console --debug`.
- **Without a TV:** `tools/fake_tv.py` connects to a running Padthru like the TV app does.
- Build everything with `tools\build.ps1` (Windows), or the Linux app alone with `python3 tools/build_pyz.py`.

Please keep pull requests focused, and describe how you tested them (TV model, game).

## Ideas that would be great to have

- Support for other TV platforms (LG webOS, Android/Google TV), if they allow showing an HDMI input inside an app.
- Packages for Linux distributions, such as an AUR package. (Flatpak is a poor fit, because Padthru needs `/dev/uinput`.)
