# M7 — Recorder on the Raspberry Pi, clipping on the Mac

The Pi records 24/7 (always on, lid problems gone). The Mac does the heavy work (Whisper, render,
review). Code is developed on the Mac, pushed to GitHub and pulled on the Pi.

## What runs where
| Machine | Services | Data |
|---|---|---|
| **Pi** | `clipper.record` (streamlink + ffmpeg, IRC chat, Helix) · `clipper.feed` (port 8766) | own small DB, last N hours of video |
| **Mac** | `clipper.mirror` (pulls from the Pi every 10 s) · `clipper.pipeline live` · review app | DB with labels/candidates, clips |

The mirror writes into the Mac's normal DB and `data/recordings/`, so the pipeline and review app
are unchanged. The Mac's DB and clips are **not** copied to the Pi (the Pi never needs them).
If the Mac sleeps, the Pi keeps recording; on wake the mirror copies the last hour and catches up.

## Setup (≈15 min)
**Pi:** 64-bit Raspberry Pi OS, wired Ethernet preferred, storage ≥ 128 GB (SSD best; ~3.5 GB per
stream-hour). Hostname below assumes `raspberrypi` — use yours.

1. **Mac** — create the shared feed token (adds two lines to `.env`, prints no secret):
   `deploy/make_feed_token.sh raspberrypi`
2. **Pi** — get the code (public repo, no login needed):
   ```bash
   sudo apt-get install -y git
   git clone -b m1-recorder https://github.com/nerdtf/twitch_downloader.git ~/twitch_downloader
   ```
3. **Mac** — copy the secrets file to the Pi (never commit it):
   `scp .env <pi-user>@raspberrypi.local:~/twitch_downloader/.env`
   With an external HDD (twitchpi: 4 TB exFAT at `/mnt/hdd`), put the video there:
   `echo CLIPPER_DATA_DIR=/mnt/hdd/clipper/recordings >> ~/twitch_downloader/.env`
4. **Pi** — install everything and start both services:
   `cd ~/twitch_downloader && bash deploy/pi/setup.sh`
   It installs ffmpeg + uv, sizes the video window to the free disk (`CLIPPER_TTL_HOURS`; a disk
   ≥ 500 GB keeps up to 14 days so old moments can be re-cut), and
   installs `clipper-recorder` + `clipper-feed` as systemd services (start on boot).
5. **Mac** — check the link: `uv run python -m clipper.mirror --once` → `mirror OK: {...}`
6. **Mac** — switch the Mac from recording to mirroring (stops the Mac recorder):
   `deploy/install_recorder.sh mirror`

## Daily use
- New code: push from the Mac, then on the Pi `bash deploy/pi/update.sh` (pull + restart).
- Logs on the Pi: `tail -f ~/twitch_downloader/data/logs/{recorder,feed}.log`;
  `systemctl status clipper-recorder clipper-feed`.
- Logs on the Mac: `tail -f data/logs/mirror.log data/logs/pipeline_live.log`.
- Back to Mac-only recording (Pi away): `deploy/install_recorder.sh` (recorder mode).

## Notes
- Feed is read-only and needs the token; it listens on the LAN only (no port forwarding).
- Telegram start/stop alerts now come from the Pi (same `.env`).
- Pi disk settings live in the Pi's `.env` (`CLIPPER_TTL_HOURS`, `CLIPPER_MIN_FREE_GB`); the
  shared `config/streamers.yaml` stays identical on both machines.
- Bandwidth: ~8 Mbit/s per live stream from Pi to Mac. Wi-Fi is fine; catch-up after sleep is faster
  on Ethernet.
