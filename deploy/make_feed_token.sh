#!/bin/bash
# On the Mac: add CLIPPER_FEED_TOKEN (and the Pi's feed URL) to .env if missing. Prints no secret.
#   deploy/make_feed_token.sh [pi-hostname]      (default: raspberrypi)
set -euo pipefail
cd "$(dirname "$0")/.."
touch .env
if ! grep -q '^CLIPPER_FEED_TOKEN=' .env; then
  printf '\n# Pi feed (same token on Pi and Mac)\nCLIPPER_FEED_TOKEN=%s\n' "$(openssl rand -hex 24)" >> .env
  echo "added CLIPPER_FEED_TOKEN to .env"
fi
if ! grep -q '^CLIPPER_FEED_URL=' .env; then
  echo "CLIPPER_FEED_URL=http://${1:-raspberrypi}.local:8766" >> .env
  echo "added CLIPPER_FEED_URL=http://${1:-raspberrypi}.local:8766 to .env"
fi
