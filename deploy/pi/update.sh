#!/bin/bash
# Pull the latest code on the Pi and restart its services:  bash deploy/pi/update.sh
set -euo pipefail
cd "$(dirname "$0")/../.."
export PATH="$HOME/.local/bin:$PATH"
git pull --ff-only
uv sync --no-dev -q
sudo systemctl restart clipper-recorder clipper-feed
git log -1 --format='updated to %h %s'
