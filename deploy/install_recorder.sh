#!/bin/bash
# Install/refresh the recorder and the live clip pipeline as per-user launchd agents.
# Run by the owner, not by the agent.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/.." && pwd)"
mkdir -p "$REPO/data/logs"
(cd "$REPO" && uv sync -q)
for name in recorder pipeline; do
  label="com.clipper.$name"
  dest="$HOME/Library/LaunchAgents/$label.plist"
  sed "s|__REPO__|$REPO|g" "$REPO/deploy/$label.plist" > "$dest"
  launchctl bootout "gui/$(id -u)/$label" 2>/dev/null || true
  launchctl bootstrap "gui/$(id -u)" "$dest"
  echo "installed: $dest"
done
echo "logs: tail -f $REPO/data/logs/recorder.log $REPO/data/logs/pipeline_live.log"
echo "stop: launchctl bootout gui/\$(id -u)/com.clipper.recorder; launchctl bootout gui/\$(id -u)/com.clipper.pipeline"
