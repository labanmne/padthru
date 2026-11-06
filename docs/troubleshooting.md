# Troubleshooting

Start with the info panel on the TV: press **Red** to show it. It tells you what Padthru sees: the PC connection, the HDMI input and its signal, and the controller.

On the PC, **Open log** in the tray menu shows what happened; the file is `%LOCALAPPDATA%\Padthru\padthru.log`. For more detail, quit Padthru and run `Padthru.exe --debug`, which also logs every button press.

This page covers Windows. On Linux, see the [Linux troubleshooting table](linux.md#troubleshooting); the TV-side sections below apply there too.

## The TV can't find the PC

- **Is Padthru running?** Look for its icon next to the clock (it may be under the **^** arrow).
- **Firewall.** The first time Padthru runs, Windows asks whether to allow it. If that was dismissed or declined, open *Windows Security → Firewall & network protection → Allow an app through firewall*, find **Padthru** and tick **Private**. If there's an old entry with a red "blocked" icon, remove it.
- **Network profile.** Your home network should be set to **Private** in Windows (*Settings → Network & internet → your connection → Network profile type*).
- **Different subnets.** Automatic search only covers the TV's own `x.x.x.1–254` range. If the PC is on another subnet (for example a separate mesh or guest network), press **Green** on the TV and type the last part of the PC's address. The full address is in the Padthru tray menu.
- **VPNs.** A VPN on the PC can hide it from the local network. Allow local network access in the VPN app.

## Black screen or the wrong picture

- Press **1–4** on the remote to choose the HDMI input your PC is connected to. Padthru remembers it.
- The info panel shows the incoming signal, for example `HDMI 4 · 3840×2160 @ 60 Hz`. If it shows nothing, the TV isn't receiving a signal on that input.

## No 4K, no HDR, or only 30 Hz

- Turn on **Input Signal Plus** for that HDMI port: *Settings → General → External Device Manager → Input Signal Plus*.
- Use a cable or extender rated for **18 Gbps / 4K60 HDR**.
- In Windows, set the TV display's resolution and refresh rate under *Settings → System → Display*, and turn on **Use HDR** if you want HDR.

## Input lag feels high

- Turn on **Game Mode** for the PC's HDMI port: *Settings → General → External Device Manager → Game Mode Settings*. It stays active inside Padthru.
- Turn off motion smoothing (*Picture Clarity*) if Game Mode is unavailable on your model.

## The controller works on the TV but not in games

- The info panel should show your controller's name and **Connected to <your PC>**.
- Some games only look for controllers when they start. Connect first, then launch the game.
- Steam: make sure *Settings → Controller → Xbox Extended Feature Support* doesn't fight with it. Padthru appears as a regular Xbox 360 controller.

## "Padthru needs the ViGEmBus driver"

Install ViGEmBus from <https://github.com/nefarius/ViGEmBus/releases/latest> and restart Padthru. If it's installed but still not found, check that **Nefarius Virtual Gamepad Emulation Bus** appears under *System devices* in Device Manager.

## Installing or updating the TV app fails

- **Developer Mode** must be on, with **Host PC IP** set to the PC you install from. Restart the TV after changing it.
- **"Author certificate not match"** when updating means the installed copy was signed with a different certificate. Uninstall Padthru from the TV first, then install again.
- Keep installing from the same PC and tool, so the same certificate is reused.
