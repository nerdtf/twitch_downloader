"""Past broadcasts (VODs): metadata + audio-only download + viewer clips; video per window.

Audio-only keeps a 3 h VOD at ~200 MB, enough for audio scoring and Whisper. Video is fetched
only for the windows that become clips (`download_window`).
"""

import json
import logging
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from clipper.store import Store
from clipper.twitch.helix import HelixClient

log = logging.getLogger(__name__)


def parse_rfc3339(ts: str) -> float:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


def parse_twitch_duration(d: str) -> float:
    """'2h46m39s' -> seconds."""
    total, num = 0, ""
    for ch in d:
        if ch.isdigit():
            num += ch
        else:
            total += int(num or 0) * {"h": 3600, "m": 60, "s": 1}[ch]
            num = ""
    return float(total)


def _hms(seconds: float) -> str:
    s = int(max(seconds, 0))
    return f"{s // 3600:02d}:{s % 3600 // 60:02d}:{s % 60:02d}"


def streamlink_vod(
    vod_id: str, quality: str, out: Path, start: float | None = None, duration: float | None = None
) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        sys.executable,
        "-m",
        "streamlink_cli",
        "--loglevel",
        "warning",
        "--force",
        "--stream-segment-threads",
        "4",
        "-o",
        str(out),
    ]
    if start is not None:
        cmd += ["--hls-start-offset", _hms(start)]
    if duration is not None:
        cmd += ["--hls-duration", _hms(duration + 1)]
    cmd += [f"https://www.twitch.tv/videos/{vod_id}", quality]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 or not out.exists():
        raise RuntimeError(f"streamlink VOD {vod_id} failed: {proc.stderr.strip()[-300:]}")
    return out


def fetch_vod(helix: HelixClient, store: Store, vod_id: str, root: Path) -> dict:
    """Download VOD audio + store metadata and its viewer clips. Idempotent."""
    v = helix.get_video(vod_id)
    if v is None:
        raise RuntimeError(f"VOD {vod_id} not found")
    created = parse_rfc3339(v["created_at"])
    duration = parse_twitch_duration(v["duration"])
    out_dir = root / v["user_login"] / vod_id
    audio = out_dir / "audio.ts"
    if not audio.exists():
        log.info("downloading audio of %s (%s, %s)", vod_id, v["user_login"], v["duration"])
        streamlink_vod(vod_id, "audio_only", audio)
    store.conn.execute(
        "INSERT OR REPLACE INTO vods(id, login, user_id, created_epoch, duration, title,"
        " audio_path) VALUES (?,?,?,?,?,?,?)",
        (vod_id, v["user_login"], v["user_id"], created, duration, v.get("title"), str(audio)),
    )
    n = save_clips(
        helix,
        store,
        v["user_id"],
        v["user_login"],
        v["created_at"],
        _rfc3339(created + duration + 3 * 86400),
        video_id=vod_id,
    )
    (out_dir / "meta.json").write_text(
        json.dumps(v, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {"vod": vod_id, "login": v["user_login"], "duration": duration, "clips": n}


def _rfc3339(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def save_clips(
    helix: HelixClient,
    store: Store,
    user_id: str,
    login: str,
    started_at: str,
    ended_at: str | None = None,
    video_id: str | None = None,
) -> int:
    clips = helix.get_clips(user_id, started_at, ended_at)
    if video_id:
        clips = [c for c in clips if c.video_id == video_id]
    store.conn.executemany(
        "INSERT OR REPLACE INTO twitch_clips(id, login, video_id, vod_offset, duration, view_count,"
        " created_epoch, title, url) VALUES (?,?,?,?,?,?,?,?,?)",
        [
            (
                c.id,
                login,
                c.video_id,
                c.vod_offset,
                c.duration,
                c.view_count,
                parse_rfc3339(c.created_at),
                c.title,
                c.url,
            )
            for c in clips
        ],
    )
    return len(clips)


def download_window(
    vod_id: str, start_offset: float, duration: float, out: Path, quality: str = "1080p60,best"
) -> Path:
    """Video for [start_offset, start_offset+duration] of a VOD (snaps to ~2 s HLS chunks)."""
    return streamlink_vod(vod_id, quality, out, start=start_offset, duration=duration)


def _cli() -> None:
    import argparse

    from clipper.config import REPO_ROOT

    ap = argparse.ArgumentParser(description="Download VOD audio + metadata + viewer clips")
    ap.add_argument("vod_ids", nargs="+")
    ap.add_argument("--db", default=str(REPO_ROOT / "data/clipper.db"))
    ap.add_argument("--root", default=str(REPO_ROOT / "data/vods"))
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    helix, store = HelixClient.from_env(), Store(args.db)
    for vid in args.vod_ids:
        try:
            print(fetch_vod(helix, store, vid, Path(args.root)), flush=True)
        except Exception as e:  # keep going with the other VODs
            log.error("VOD %s failed: %s", vid, e)


if __name__ == "__main__":
    _cli()
