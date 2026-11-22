#!/bin/bash
# Install/refresh the Mac's launchd agents. Run by the owner.
#   deploy/install_recorder.sh          Mac records itself:      recorder + pipeline + bot + review
#   deploy/install_recorder.sh mirror   the Pi records:          mirror (pulls from Pi) + pipeline + bot + review
# Switching modes removes the agent of the other mode (only one of recorder/mirror may run).
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
MODE="${1:-recorder}"
case "$MODE" in
  recorder) want="recorder pipeline bot review"; drop="mirror" ;;
  mirror)   want="mirror pipeline bot review";   drop="recorder" ;;
  *) echo "usage: $0 [recorder|mirror]"; exit 2 ;;
esac
mkdir -p "$REPO/data/logs"
(cd "$REPO" && uv sync -q)
for name in $drop; do
  label="com.clipper.$name"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null && echo "removed: $label" || true
  rm -f "$HOME/Library/LaunchAgents/$label.plist"
done
for name in $want; do
  label="com.clipper.$name"
  dest="$HOME/Library/LaunchAgents/$label.plist"
  sed "s|__REPO__|$REPO|g" "$REPO/deploy/$label.plist" > "$dest"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$dest"
  echo "installed: $dest"
done
first="${want%% *}"
echo "logs: tail -f $REPO/data/logs/$first.log $REPO/data/logs/pipeline_live.log"
