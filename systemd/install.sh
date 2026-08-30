#!/usr/bin/env bash
# Install and enable the systemd *user* units (three timers + the serve daemon).
# Run as yourself — never with sudo.
#
# The .service files are templates: @APP_DIR@ is substituted with wherever this
# repo actually lives, resolved at install time. That is what makes the units
# portable — an absolute path baked into the repo only ever works on the one
# machine it was written on.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="$(cd "$HERE/.." && pwd)"
DEST="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

if [ ! -x "$APP_DIR/idintel.sh" ]; then
  echo "error: $APP_DIR/idintel.sh not found or not executable" >&2
  exit 1
fi
if [ ! -x "$APP_DIR/.venv/bin/python" ]; then
  echo "error: no virtualenv at $APP_DIR/.venv" >&2
  echo "       run: python -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
  exit 1
fi

mkdir -p "$DEST" "$APP_DIR/logs"

# Generate each unit with this machine's path substituted in.
for template in "$HERE"/idintel-*.service.in; do
  unit="$(basename "${template%.in}")"
  sed "s|@APP_DIR@|$APP_DIR|g" "$template" > "$DEST/$unit"
  chmod 644 "$DEST/$unit"
done
# Timers reference no paths, so they install unchanged.
install -m 644 "$HERE"/idintel-*.timer "$DEST/"

echo "installed units into $DEST (APP_DIR=$APP_DIR)"

systemctl --user daemon-reload
# Timers drive the scheduled reports; the serve unit is a long-running daemon
# that keeps the reports + Zotero buttons available at http://localhost:8791.
systemctl --user enable --now idintel-daily.timer idintel-weekly.timer idintel-monthly.timer
systemctl --user enable --now idintel-serve.service

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
echo
systemctl --user --no-pager status idintel-serve.service | head -4
echo
echo "Reports + Zotero buttons: http://localhost:8791"
