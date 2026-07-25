#!/usr/bin/env bash
# Install the launchd agents (three scheduled reports + the serve daemon) on macOS.
# The macOS counterpart to systemd/install.sh. Run as yourself — never with sudo.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEST="$HOME/Library/LaunchAgents"
UID_NUM="$(id -u)"

mkdir -p "$DEST" "$HERE/../logs"
install -m 644 "$HERE"/com.idintel.*.plist "$DEST/"

# bootout then bootstrap so re-running this script cleanly reloads changed plists.
for label in com.idintel.daily com.idintel.weekly com.idintel.monthly com.idintel.serve; do
  launchctl bootout "gui/$UID_NUM/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$UID_NUM" "$DEST/$label.plist"
  launchctl enable "gui/$UID_NUM/$label"
done

echo
echo "Installed agents:"
launchctl list | grep idintel || true
echo
echo "Scheduled: daily 06:30 · weekly Fri 07:15 · monthly 1st 07:30"
echo "Reports + Zotero buttons: http://localhost:8791"
