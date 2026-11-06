#!/usr/bin/env bash
# Padthru installer for Linux. https://github.com/labanmne/padthru
#
#   bash install.sh               install or update, and start Padthru in the background
#   bash install.sh --uninstall   remove Padthru and everything this script set up
#   bash install.sh --file PATH   install a padthru-linux.pyz you built or downloaded yourself
#
# Run it as your normal user. It asks for sudo only if it has to, to let you use
# /dev/uinput (virtual controllers) or to open TCP port 47811 in the firewall.
set -euo pipefail

REPO="labanmne/padthru"
DOWNLOAD="${PADTHRU_DOWNLOAD:-https://github.com/$REPO/releases/latest/download}"  # override for mirrors and testing
PORT=47811
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/padthru"
STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/padthru"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT="$UNIT_DIR/padthru.service"
LAUNCHER="$HOME/.local/bin/padthru"
UDEV_RULE=/etc/udev/rules.d/60-padthru-uinput.rules
MODULES_CONF=/etc/modules-load.d/padthru-uinput.conf

if [ -t 1 ]; then B=$'\e[1m' Y=$'\e[33m' R=$'\e[31m' N=$'\e[0m'; else B='' Y='' R='' N=''; fi
say()  { printf '%s==>%s %s\n' "$B" "$N" "$*"; }
warn() { printf '%swarning:%s %s\n' "$Y" "$N" "$*" >&2; }
die()  { printf '%serror:%s %s\n' "$R" "$N" "$*" >&2; exit 1; }
have() { command -v "$1" >/dev/null 2>&1; }
as_root() { if [ "$(id -u)" -eq 0 ]; then "$@"; else sudo "$@"; fi; }
has_user_systemd() { have systemctl && systemctl --user show-environment >/dev/null 2>&1; }
uinput_ok() { [ -r /dev/uinput ] && [ -w /dev/uinput ]; }
tmp=""

usage() {
  cat <<'EOF'
Padthru installer for Linux (https://github.com/labanmne/padthru)

  bash install.sh               install or update, and start Padthru in the background
  bash install.sh --uninstall   remove Padthru and everything this script set up
  bash install.sh --file PATH   install a padthru-linux.pyz you built or downloaded yourself
EOF
}

fetch() {  # url file
  if have curl; then curl -fsSL --retry 2 -o "$2" "$1"
  elif have wget; then wget -q -O "$2" "$1"
  else die "Please install curl or wget."; fi
}

# ---------------------------------------------------------------- uninstall

do_uninstall() {
  say "Removing Padthru"
  if has_user_systemd; then
    systemctl --user disable --now padthru.service >/dev/null 2>&1 || true
  fi
  rm -f "$UNIT" "$LAUNCHER"
  has_user_systemd && systemctl --user daemon-reload || true
  local fw=""
  [ -f "$APP_DIR/firewall" ] && fw="$(cat "$APP_DIR/firewall")"
  case "$fw" in
    ufw) say "Closing port $PORT in ufw"; as_root ufw delete allow "$PORT/tcp" >/dev/null || true ;;
    firewalld) say "Closing port $PORT in firewalld"
      as_root firewall-cmd --quiet --permanent --remove-port="$PORT/tcp" || true
      as_root firewall-cmd --quiet --reload || true ;;
  esac
  if [ -e "$UDEV_RULE" ] || [ -e "$MODULES_CONF" ]; then
    say "Removing the udev rule for /dev/uinput"
    as_root rm -f "$UDEV_RULE" "$MODULES_CONF"
    as_root udevadm control --reload-rules || true
  fi
  rm -rf "$APP_DIR" "$STATE_DIR"
  say "Padthru is removed. (The TV app stays on the TV; remove it there if you like.)"
}

# ---------------------------------------------------------------- install

setup_uinput() {
  if uinput_ok; then
    return
  fi
  say "Allowing your user to create virtual controllers (/dev/uinput); this needs sudo"
  [ -e /dev/uinput ] || as_root modprobe uinput || die "Could not load the uinput kernel module."
  # Same rule Steam uses: the user signed in at the computer may use /dev/uinput.
  printf '%s\n' '# Padthru: let the signed-in user create virtual game controllers (same as Steam)' \
    'KERNEL=="uinput", SUBSYSTEM=="misc", OPTIONS+="static_node=uinput", TAG+="uaccess"' |
    as_root tee "$UDEV_RULE" >/dev/null
  echo uinput | as_root tee "$MODULES_CONF" >/dev/null
  as_root udevadm control --reload-rules
  as_root udevadm trigger --action=change --sysname-match=uinput || true
  as_root udevadm settle || true
  if ! uinput_ok; then
    warn "/dev/uinput still isn't usable by $(id -un). Access is given to the user signed in at the computer itself,"
    warn "so it works once you sign in on the PC's own screen (not over SSH). See docs/linux.md for other setups."
  fi
}

setup_firewall() {
  if [ -r /etc/ufw/ufw.conf ] && grep -q '^ENABLED=yes' /etc/ufw/ufw.conf && have ufw; then
    say "Opening TCP port $PORT in ufw so the TV can reach this PC; this needs sudo"
    as_root ufw allow "$PORT/tcp" comment Padthru >/dev/null && echo ufw > "$APP_DIR/firewall"
  elif have firewall-cmd && systemctl is-active --quiet firewalld 2>/dev/null; then
    say "Opening TCP port $PORT in firewalld so the TV can reach this PC; this needs sudo"
    as_root firewall-cmd --quiet --permanent --add-port="$PORT/tcp" &&
      as_root firewall-cmd --quiet --reload && echo firewalld > "$APP_DIR/firewall"
  fi
}

this_pc_address() {
  local a
  a="$(ip -4 -o route get 10.254.254.254 2>/dev/null | sed -n 's/.* src \([0-9.]*\).*/\1/p')" || true
  [ -n "$a" ] || a="$(hostname -I 2>/dev/null | cut -d' ' -f1)" || true
  printf '%s' "$a"
}

do_install() {
  local src="$1"
  [ "$(id -u)" -ne 0 ] || die "Run this as your normal user, not as root. It asks for sudo when it needs it."
  [ "$(uname -s)" = Linux ] || die "This installer is for Linux. On Windows, use Padthru.exe."
  local py
  py="$(command -v python3 || true)"
  [ -x /usr/bin/python3 ] && py=/usr/bin/python3
  [ -n "$py" ] || die "Padthru needs Python 3.8 or newer (package 'python3')."
  "$py" -c 'import sys; sys.exit(sys.version_info < (3, 8))' ||
    die "Padthru needs Python 3.8 or newer; $py is $("$py" -V 2>&1)."

  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' EXIT
  if [ -n "$src" ]; then
    [ -f "$src" ] || die "No such file: $src"
    cp "$src" "$tmp/padthru-linux.pyz"
  else
    say "Downloading the latest Padthru"
    fetch "$DOWNLOAD/padthru-linux.pyz" "$tmp/padthru-linux.pyz"
    fetch "$DOWNLOAD/SHA256SUMS.txt" "$tmp/SHA256SUMS.txt"
    if have sha256sum; then
      (cd "$tmp" && tr -d '\r' < SHA256SUMS.txt | grep ' padthru-linux.pyz$' | sha256sum -c --quiet -) ||
        die "The download doesn't match its published checksum. Please try again."
    else
      warn "sha256sum not found; skipping the checksum check."
    fi
  fi
  "$py" "$tmp/padthru-linux.pyz" --version >/dev/null || die "The downloaded Padthru doesn't run with $py."

  mkdir -p "$APP_DIR" "$(dirname "$LAUNCHER")"
  install -m 0644 "$tmp/padthru-linux.pyz" "$APP_DIR/padthru.pyz"
  printf '#!/bin/sh\nexec %s "%s" "$@"\n' "$py" "$APP_DIR/padthru.pyz" > "$LAUNCHER"
  chmod 0755 "$LAUNCHER"
  say "Installed $("$py" "$APP_DIR/padthru.pyz" --version) to $APP_DIR"

  setup_uinput
  setup_firewall

  if has_user_systemd; then
    mkdir -p "$UNIT_DIR"
    cat > "$UNIT" <<EOF
[Unit]
Description=Padthru: use the controller paired to your Samsung TV on this PC
Documentation=https://github.com/$REPO

[Service]
# Extra options, e.g. --debug: systemctl --user edit padthru  ->  Environment=PADTHRU_ARGS=--debug
Environment=PADTHRU_ARGS=
ExecStart="$py" "$APP_DIR/padthru.pyz" \$PADTHRU_ARGS
Restart=on-failure
RestartSec=10

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable padthru.service >/dev/null 2>&1
    systemctl --user restart padthru.service
    sleep 2
    if systemctl --user is-active --quiet padthru.service; then
      say "Padthru is running in the background and starts when you sign in."
    else
      warn "Padthru didn't start. See: journalctl --user -u padthru"
    fi
    local how="journalctl --user -u padthru -f"
  else
    warn "systemd isn't available for your user, so Padthru won't start by itself. Start it with: padthru"
    local how="padthru --debug   (runs in the terminal)"
  fi

  local addr
  addr="$(this_pc_address)"
  cat <<EOF

  ${B}This PC:${N}  ${addr:-unknown}   (the TV finds it by itself; you only need this to enter it by hand)
  ${B}Log:${N}      $how
  ${B}Next:${N}     install the Padthru TV app and open it:
            https://github.com/$REPO#2-on-the-tv

EOF
  case ":$PATH:" in *":$HOME/.local/bin:"*) ;; *) warn "$HOME/.local/bin isn't in your PATH yet: use $LAUNCHER, or sign out and back in." ;; esac
}

# ---------------------------------------------------------------- main

case "${1:-}" in
  ""|--install) do_install "" ;;
  --file) [ -n "${2:-}" ] || die "--file needs a path"; do_install "$(realpath "$2")" ;;
  --uninstall|--remove) do_uninstall ;;
  -h|--help) usage ;;
  *) usage; exit 2 ;;
esac
