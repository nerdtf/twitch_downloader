#!/bin/bash
# Install/refresh the recorder as a per-user launchd agent. Run by the owner, not by the agent.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
DEST="$HOME/Library/LaunchAgents/com.clipper.recorder.plist"
mkdir -p "$REPO/data/logs"
(cd "$REPO" && uv sync -q)
sed "s|__REPO__|$REPO|g" "$REPO/deploy/com.clipper.recorder.plist" > "$DEST"
launchctl bootout "gui/$(id -u)/com.clipper.recorder" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$DEST"
echo "installed: $DEST"
echo "logs:      tail -f $REPO/data/logs/recorder.log"
echo "stop:      launchctl bootout gui/\$(id -u)/com.clipper.recorder"
