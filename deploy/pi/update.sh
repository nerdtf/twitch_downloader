#!/bin/bash
# Pull the latest code on the Pi and restart its services:  bash deploy/pi/update.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
export PATH="$HOME/.local/bin:$PATH"
git pull --ff-only
uv sync --no-dev -q
REPO="$(pwd)"
for unit in clipper-health.service clipper-health.timer; do  # added 2026-11-21
  sed -e "s|__USER__|$USER|g" -e "s|__REPO__|$REPO|g" "deploy/pi/$unit" \
    | sudo tee "/etc/systemd/system/$unit" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable --now clipper-health.timer
sudo systemctl restart clipper-recorder clipper-feed
git log -1 --format='updated to %h %s'
