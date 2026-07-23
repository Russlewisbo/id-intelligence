#!/usr/bin/env bash
# Install and enable the systemd *user* timers.
# Run as yourself — never with sudo.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

mkdir -p "$DEST" "$HERE/../logs"
install -m 644 "$HERE"/idintel-*.service "$HERE"/idintel-*.timer "$DEST/"

systemctl --user daemon-reload
systemctl --user enable --now idintel-daily.timer idintel-weekly.timer idintel-monthly.timer

# Without lingering, user timers only run while you are logged in. This lets the
# 06:30 job fire on a booted machine before you log in.
if ! loginctl show-user "$USER" --property=Linger 2>/dev/null | grep -q "Linger=yes"; then
  echo
  echo "NOTE: user lingering is off, so timers only run while you are logged in."
  echo "To let them run on a booted-but-logged-out machine:"
  echo "    sudo loginctl enable-linger $USER"
fi

echo
systemctl --user list-timers 'idintel-*' --no-pager
