# Padthru on Linux

The Linux PC app does the same job as `Padthru.exe`: it receives the controller from the TV app and creates a virtual controller. It uses the kernel's `uinput` module, the same way Steam Input creates its virtual controllers. A PlayStation controller shows up exactly like a USB **DualShock 4** does under the kernel's `hid-playstation` driver, anything else like a wired **Xbox 360 controller** under `xpad`. SDL-based games, Proton/Wine and browsers treat it as one, rumble included. To force one kind, add `--pad xbox` or `--pad playstation` (as a service: `Environment=PADTHRU_ARGS=--pad playstation`, see below).

The virtual DualShock 4 has no motion sensors, touchpad or light bar, because the TV doesn't pass them on. Steam Input usually reads PlayStation controllers through their raw HID interface, which a `uinput` device doesn't have, so in Steam it may show up as a generic controller; SDL games recognise it as a PS4 controller.

It's a single file, `padthru-linux.pyz` (about 120 KB), with no dependencies beyond Python 3.8 or newer.

> **New in 0.2.0.** Every change is [tested automatically](../.github/workflows/test.yml) on Ubuntu with Python 3.8, 3.12 and 3.14. A simulated TV drives the app, and the virtual controller is checked both at the input-device level and through SDL, the library most games use: buttons, sticks, triggers, D-pad, the Guide combo and rumble. It hasn't been tested on a real Linux gaming PC wired to a TV yet. If you run it, please [report your distro](https://github.com/labanmne/padthru/issues/new/choose), even if it just works.

## Requirements

- Any current distribution: SteamOS (Steam Deck desktop mode), Bazzite, ChimeraOS, Ubuntu, Fedora, Arch, Debian and so on. X11 or Wayland doesn't matter, as Padthru works below the desktop.
- **Python 3.8 or newer** (`python3`). Almost every desktop distribution has it.
- **systemd** to start Padthru automatically. Without it you can still run Padthru by hand.

## Install

```bash
curl -fsSLO https://github.com/labanmne/padthru/releases/latest/download/install-linux.sh
bash install-linux.sh
```

Run it as your normal user, not with `sudo`. Then [set up the TV app](../README.md#2-on-the-tv); [Apps2Samsung](https://github.com/Apps2Samsung/Apps2Samsung) also runs on Linux.

### What the installer does

It's a short, readable script. In order, it:

1. Downloads `padthru-linux.pyz` from the latest release and checks it against the release's `SHA256SUMS.txt`.
2. Puts it in `~/.local/share/padthru/` and adds a `padthru` command in `~/.local/bin/`.
3. **Only if your user can't use `/dev/uinput` yet** (sudo): adds the udev rule Steam uses, which lets the user signed in at the computer create virtual controllers, and loads the `uinput` module at boot. If Steam is installed, this is usually already in place and the step is skipped.
4. **Only if a firewall is active** (sudo): opens TCP port **47811** in `ufw` or `firewalld`, so the TV can reach the PC.
5. Adds a systemd **user** service, `padthru.service`, which starts Padthru when you sign in. It starts it right away too.

Run the installer again at any time to update. `bash install-linux.sh --uninstall` removes everything it added, including the udev rule and the firewall rule.

### Install by hand

If you'd rather not run a script:

```bash
python3 padthru-linux.pyz            # runs in the terminal; Ctrl+C to stop
python3 padthru-linux.pyz --debug    # also logs every button press
```

Your user needs read and write access to `/dev/uinput` (see [Permissions](#permissions)), and TCP port 47811 must be reachable from the TV.

## Everyday use

| | |
|---|---|
| Status | `systemctl --user status padthru` |
| Live log | `journalctl --user -u padthru -f` |
| Stop / start | `systemctl --user stop padthru` / `systemctl --user start padthru` |
| Don't start at sign-in | `systemctl --user disable padthru` |
| Log every button press | `systemctl --user edit padthru`, add `[Service]` and `Environment=PADTHRU_ARGS=--debug`, then `systemctl --user restart padthru` |
| Address for manual entry on the TV | `ip -4 route get 1.1.1.1` (the `src` address) |

Padthru also writes a log file to `~/.local/state/padthru/padthru.log`.

**Start before anyone signs in.** The service is tied to your user session, which suits a gaming PC that signs in automatically. To run it from boot regardless, run `loginctl enable-linger $USER` once.

## Test without a TV

`tools/fake_tv.py` from the repository acts like the TV app. It connects to Padthru, presses every button and moves both sticks, so you can watch the controller in Steam (*Settings → Controller*), `evtest` or `jstest-gtk`:

```bash
python3 fake_tv.py            # Padthru on this PC
python3 fake_tv.py 192.168.1.50
```

## Permissions

Creating a virtual controller means writing to `/dev/uinput`, which most distributions reserve for root. The installer uses the same udev rule as Steam:

```
KERNEL=="uinput", SUBSYSTEM=="misc", OPTIONS+="static_node=uinput", TAG+="uaccess"
```

`uaccess` gives access to whoever is signed in **at the computer itself**, the person who would be playing. It doesn't apply over SSH or to a machine that nobody has signed in to. For those setups, give a group access instead:

```bash
sudo groupadd -f uinput
sudo usermod -aG uinput "$USER"
echo 'KERNEL=="uinput", SUBSYSTEM=="misc", OPTIONS+="static_node=uinput", GROUP="uinput", MODE="0660"' |
  sudo tee /etc/udev/rules.d/60-padthru-uinput.rules
sudo udevadm control --reload-rules && sudo udevadm trigger --sysname-match=uinput
```

Then sign out and back in. Note that anyone who can write to `/dev/uinput` can also create virtual keyboards, so only add trusted users.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Log says **isn't allowed to use /dev/uinput** | Run the installer, or set up [Permissions](#permissions). With the default rule, sign in at the PC itself. The service retries every 10 seconds. |
| Log says **/dev/uinput is missing** | `sudo modprobe uinput`. The installer makes this permanent. |
| **The TV keeps "Looking for your PC"** | Check `systemctl --user status padthru`. Open TCP 47811 if you run a firewall the installer doesn't know. TV and PC must be on the same subnet, or type the PC's address under **Green → Settings**. |
| **"port 47811 is already in use"** | Padthru is already running, maybe both the service and a copy in a terminal. |
| **The game doesn't react** | Run `fake_tv.py` and watch Steam's controller screen. Some games only look for controllers at start, so connect first. In Steam, a game's *Steam Input* setting decides whether it sees the Xbox 360 controller directly or through Steam's own virtual controller. Either works. |
| **Steam Deck: the installer asks for a password** | SteamOS has no sudo password until you set one with `passwd`. Steam's rules normally make the uinput step unnecessary on SteamOS, so this should only come up if something else needs sudo. |

## Build it yourself

```bash
git clone https://github.com/labanmne/padthru && cd padthru
python3 tools/build_pyz.py          # dist/padthru-linux.pyz
bash linux/install.sh --file dist/padthru-linux.pyz
```

`build_pyz.py` downloads the pure-Python `websockets` 12.0 wheel from PyPI and checks it against a pinned SHA-256. Pass `--wheel FILE` to use a copy you already have.

## How it maps the controller

| TV controller (W3C standard layout) | Linux event | |
|---|---|---|
| A / B / X / Y | `BTN_SOUTH` / `BTN_EAST` / `BTN_NORTH` / `BTN_WEST` | |
| L1 / R1 | `BTN_TL` / `BTN_TR` | |
| L2 / R2 | `ABS_Z` / `ABS_RZ` | 0–255 |
| Sticks | `ABS_X` `ABS_Y` / `ABS_RX` `ABS_RY` | −32768–32767, +y down |
| L3 / R3 | `BTN_THUMBL` / `BTN_THUMBR` | |
| D-pad | `ABS_HAT0X` / `ABS_HAT0Y` | −1, 0, 1 |
| Share / Options | `BTN_SELECT` / `BTN_START` | |
| Share + Options | `BTN_MODE` (Guide) | |

The device reports USB ID `045e:028e` and the name `Microsoft X-Box 360 pad`, like the `xpad` driver does, because games and SDL recognise controllers by them. Rumble uses the standard force-feedback interface (`FF_RUMBLE`, with `FF_GAIN`).
