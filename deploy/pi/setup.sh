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

# Raw video folder: CLIPPER_DATA_DIR in .env (e.g. an external HDD), else data/recordings.
DATA_DIR=$(grep -E '^CLIPPER_DATA_DIR=' .env | tail -1 | cut -d= -f2- || true)
DATA_DIR=${DATA_DIR:-$REPO/data/recordings}
mkdir -p "$DATA_DIR"
# Size the video window to the disk (~3.5 GB per stream-hour, 3 streams at once). A big disk
# keeps up to 14 days so clips can be re-cut later; a small card keeps a few hours.
free_gb=$(df -BG --output=avail "$DATA_DIR" | tail -1 | tr -dc 0-9)
if ! grep -q '^CLIPPER_TTL_HOURS=' .env; then
  if [ "$free_gb" -ge 500 ]; then
    ttl=$(( (free_gb - 200) / 10 )); [ "$ttl" -gt 336 ] && ttl=336; min_free=100
  else
    ttl=$(( (free_gb - 10) / 10 )); [ "$ttl" -gt 6 ] && ttl=6; [ "$ttl" -lt 1 ] && ttl=1; min_free=5
  fi
  printf '\n# Pi-only (set by deploy/pi/setup.sh, %s GB free in %s)\nCLIPPER_TTL_HOURS=%s\nCLIPPER_MIN_FREE_GB=%s\n' \
    "$free_gb" "$DATA_DIR" "$ttl" "$min_free" >> .env
  echo "disk: ${free_gb} GB free in $DATA_DIR -> keeping ${ttl} h of video (CLIPPER_TTL_HOURS in .env)"
fi

for name in recorder feed; do
  sed -e "s|__USER__|$USER|g" -e "s|__REPO__|$REPO|g" "deploy/pi/clipper-$name.service" \
    | sudo tee "/etc/systemd/system/clipper-$name.service" >/dev/null
  # never record to the SD card when the HDD is missing: wait for the mount
  sudo mkdir -p "/etc/systemd/system/clipper-$name.service.d"
  printf '[Unit]\nRequiresMountsFor=%s\n' "$DATA_DIR" \
    | sudo tee "/etc/systemd/system/clipper-$name.service.d/data.conf" >/dev/null
done
# health check: every 5 min, Telegram if the HDD/services/recording break (no mount dependency)
for unit in clipper-health.service clipper-health.timer; do
  sed -e "s|__USER__|$USER|g" -e "s|__REPO__|$REPO|g" "deploy/pi/$unit" \
    | sudo tee "/etc/systemd/system/$unit" >/dev/null
done
sudo systemctl daemon-reload
sudo systemctl enable --now clipper-recorder clipper-feed clipper-health.timer
sudo systemctl restart clipper-recorder clipper-feed
sleep 3
systemctl --no-pager --lines=0 status clipper-recorder clipper-feed | grep -E "●|Active"
echo
echo "Pi ready. Logs: tail -f $REPO/data/logs/recorder.log $REPO/data/logs/feed.log"
echo "Feed for the Mac: http://$(hostname).local:8766"
