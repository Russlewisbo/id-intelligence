#!/usr/bin/env bash
# Remove the launchd agents installed by install.sh. Run as yourself — no sudo.
set -euo pipefail

DEST="$HOME/Library/LaunchAgents"
UID_NUM="$(id -u)"

for label in com.idintel.daily com.idintel.weekly com.idintel.monthly com.idintel.serve; do
  launchctl bootout "gui/$UID_NUM/$label" 2>/dev/null || true
  rm -f "$DEST/$label.plist"
done

echo "Removed all com.idintel.* agents."
