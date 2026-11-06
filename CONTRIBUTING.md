# Contributing

Thanks for taking a look! Padthru is a small hobby project, and help is welcome.

## Reports that help most

- **"It works on my TV."** Open an issue with your TV model, year and Tizen version (*Settings → Support → About This TV*), plus the controller you used. A list of tested models helps everyone.
- **Bugs.** Use the bug report template and attach the relevant part of `%LOCALAPPDATA%\Padthru\padthru.log`. Running `Padthru.exe --debug` makes the log much more useful.

## Code

- **TV app** (`tv/`): plain ES5-style JavaScript, so it runs on older Tizen browsers. Test changes on a real TV; the TV Window API doesn't exist in desktop browsers.
- **PC app** (`pc/padthru.py`): Python 3.10+. Run it from source with `python pc\padthru.py --console --debug`.
- Build everything with `tools\build.ps1`.

Please keep pull requests focused, and describe how you tested them (TV model, game).

## Ideas that would be great to have

- Support for other TV platforms (LG webOS, Android/Google TV), if they allow showing an HDMI input inside an app.
- A DualShock 4 virtual controller mode (PlayStation prompts) as an option.
- A Linux PC app (uinput).
