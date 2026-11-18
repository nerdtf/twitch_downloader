# M7 — Moving the recorder to a Raspberry Pi

Goal: the Pi records 24/7 (cheap, always on); the Mac does the heavy work (Whisper, LLM, render).

## What runs where
| Component | Pi | Mac |
|---|---|---|
| `clipper.record` (streamlink + ffmpeg copy, IRC chat, Helix) | ✅ | — |
| `clipper.pipeline live` (stage-1/2, Whisper via MLX, render via VideoToolbox) | — | ✅ |

`mlx-whisper` and `h264_videotoolbox` are Apple-only, so the pipeline stays on the Mac.

## Steps
1. Pi OS 64-bit, external SSD (≥ 500 GB; ~3.3 GB/h/stream at 1080p60), wired Ethernet if possible.
2. `sudo apt install ffmpeg git` · install uv · `git clone` · `uv sync` (mlx-whisper will fail to
   install on Linux — install with `uv sync --no-install-package mlx-whisper` or split extras).
3. Copy `.env` (Twitch keys) — never commit it.
4. `deploy/pi/clipper-recorder.service` → systemd (see header of the file).
5. Share `data/` with the Mac: simplest is SMB/NFS export of `data/recordings` + `data/clipper.db`
   from the Pi, mounted on the Mac. SQLite over network shares is fragile, so the better option
   (to build in M7) is a tiny sync: the Pi writes chat/segment rows, the Mac pulls them every
   minute with `rsync` + an import command.

## Open decisions (owner)
- Pi model / disk; network share vs. pull-sync; should the Pi also keep a copy of rendered clips?
