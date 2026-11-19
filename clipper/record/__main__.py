"""Recorder service: `uv run python -m clipper.record --config config/streamers.yaml`."""

import argparse
import asyncio
import contextlib
import fcntl
import logging
import shutil
import signal
import time

from clipper.chat.irc import IrcClient
from clipper.config import REPO_ROOT, load_config
from clipper.record.live_status import HelixWatcher
from clipper.record.recorder import StreamRecorder
from clipper.record.retention import run_retention
from clipper.store import Store
from clipper.twitch.helix import HelixClient, HelixError

log = logging.getLogger("clipper.record")


def keep_chat_row(row: dict, recorders: dict, buf: list) -> None:
    rec = recorders.get(row["channel"])
    if rec is not None and rec.live:
        buf.append(row)


async def main(config_path: str, duration_s: float | None = None) -> None:
    cfg = load_config(config_path)
    if not cfg.streamers:
        raise SystemExit("no enabled streamers in config")
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise SystemExit(f"{tool} not found on PATH")

    store = Store(cfg.recorder.db_path)
    if n := store.recover_after_crash(time.time()):
        log.warning("closed %d segments left open by a previous run", n)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)

    try:
        helix = HelixClient.from_env()
    except HelixError:
        helix = None
    logins = [s.login for s in cfg.streamers]
    watcher = HelixWatcher(helix, logins, cfg.recorder.helix_poll_seconds)
    recorders = [StreamRecorder(s, cfg.recorder, store, watcher) for s in cfg.streamers]

    # Chat is only kept while that streamer is being recorded (no video -> chat is useless).
    by_login = {r.s.login: r for r in recorders}
    chat_buf: list[dict] = []
    irc = IrcClient(logins, lambda row: keep_chat_row(row, by_login, chat_buf))
    ttl_map = {s.login: s.ttl_hours for s in cfg.streamers}
    default_ttl = max(ttl_map.values())

    async def flush_chat():
        while not stop.is_set():
            await asyncio.sleep(1)
            if chat_buf:
                rows, chat_buf[:] = chat_buf[:], []
                store.insert_chat(rows)

    async def retention():
        while not stop.is_set():
            run_retention(
                store, cfg.recorder.data_dir, ttl_map, default_ttl, cfg.recorder.min_free_gb
            )
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=cfg.recorder.ttl_check_seconds)

    async def status():
        while not stop.is_set():
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=600)
            live = [r.s.login for r in recorders if r.live]
            free = shutil.disk_usage(cfg.recorder.data_dir).free / 1e9
            log.info("status: live=%s free_disk=%.0fGB", live or "-", free)

    async def deadline():
        if duration_s:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=duration_s)
            stop.set()

    log.info("recording %d streamers: %s", len(recorders), ", ".join(r.s.login for r in recorders))
    tasks = [
        asyncio.create_task(t)
        for t in (
            irc.run(stop),
            watcher.run(stop),
            flush_chat(),
            retention(),
            status(),
            deadline(),
            *(r.run(stop) for r in recorders),
        )
    ]
    try:
        await stop.wait()
    finally:
        log.info("shutting down…")
        await asyncio.gather(*tasks, return_exceptions=True)
        if chat_buf:
            store.insert_chat(chat_buf)
        store.close()
        log.info("stopped")


def cli() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--config", default="config/streamers.yaml")
    ap.add_argument("--duration", type=float, help="stop after N seconds (for tests)")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    lock_path = REPO_ROOT / "data/recorder.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = lock_path.open("w")
    try:  # a second recorder would store every chat message twice
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("another recorder is already running (data/recorder.lock)") from None
    asyncio.run(main(args.config, args.duration))


if __name__ == "__main__":
    cli()
