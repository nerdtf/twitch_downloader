#!/bin/bash
# One-time setup on the Raspberry Pi (64-bit Raspberry Pi OS). Run from the repo:
#   bash deploy/pi/setup.sh
# Needs .env copied from the Mac first (scp, see docs/PI_MIGRATION.md). Safe to re-run.
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO"

[ "$(uname -m)" = "aarch64" ] || echo "WARNING: $(uname -m) — use 64-bit Raspberry Pi OS (some wheels are 64-bit only)"
if [ ! -f .env ]; then
  echo "No .env here. On the Mac run:  scp .env $USER@$(hostname).local:$REPO/.env  then re-run."; exit 1
fi
grep -q '^CLIPPER_FEED_TOKEN=.\{16,\}' .env || { echo "CLIPPER_FEED_TOKEN missing in .env (run deploy/make_feed_token.sh on the Mac, copy .env again)"; exit 1; }

sudo apt-get update -qq
sudo apt-get install -y -qq ffmpeg git sqlite3 curl
command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh
export PATH="$HOME/.local/bin:$PATH"
uv sync --no-dev -q
mkdir -p data/logs data/recordings

# Pi disk: size the raw-video window to the card/SSD (≈3.5 GB per stream-hour, 3 streams at once).
free_gb=$(df -BG --output=avail data | tail -1 | tr -dc 0-9)
if ! grep -q '^CLIPPER_TTL_HOURS=' .env; then
  ttl=$(( (free_gb - 10) / 10 )); [ "$ttl" -gt 6 ] && ttl=6; [ "$ttl" -lt 1 ] && ttl=1
  printf '\n# Pi-only (set by deploy/pi/setup.sh, %s GB free)\nCLIPPER_TTL_HOURS=%s\nCLIPPER_MIN_FREE_GB=5\n' "$free_gb" "$ttl" >> .env
  echo "disk: ${free_gb} GB free -> keeping ${ttl} h of video (CLIPPER_TTL_HOURS in .env)"
fi

for name in recorder feed; do
  sed -e "s|__USER__|$USER|g" -e "s|__REPO__|$REPO|g" "deploy/pi/clipper-$name.service" \
    | sudo tee "/etc/systemd/system/clipper-$name.service" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable --now clipper-recorder clipper-feed
sudo systemctl restart clipper-recorder clipper-feed
sleep 3
systemctl --no-pager --lines=0 status clipper-recorder clipper-feed | grep -E "●|Active"
echo
echo "Pi ready. Logs: tail -f $REPO/data/logs/recorder.log $REPO/data/logs/feed.log"
echo "Feed for the Mac: http://$(hostname).local:8766"
