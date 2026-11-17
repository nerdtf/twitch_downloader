"""Per-streamer recorder: streamlink --stdout | ffmpeg segment muxer, supervised by asyncio.

Live detection is done by simply trying to open the stream: streamlink exits within seconds with
"No playable streams" when the channel is offline, which needs no Twitch API credentials.
"""

import asyncio
import contextlib
import logging
import os
import signal
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from clipper import notify
from clipper.config import RecorderSettings, Streamer
from clipper.record.segments import SEGMENT_PATTERN, parse_ad_line, segment_start_epoch
from clipper.store import Store

log = logging.getLogger(__name__)

RETRY_AFTER_DROP_S = 10  # stream dropped mid-way: retry quickly before falling back to polling


def streamlink_cmd(s: Streamer) -> list[str]:
    return [
        sys.executable, "-m", "streamlink_cli",
        "--stdout",
        "--loglevel", "info",
        "--twitch-low-latency",
        "--stream-segment-threads", "2",
        "--retry-open", "1",
        f"https://www.twitch.tv/{s.login}",
        s.quality,
    ]  # fmt: skip


def ffmpeg_cmd(out_dir: Path, segment_seconds: int) -> list[str]:
    return [
        "ffmpeg", "-hide_banner", "-nostdin", "-loglevel", "warning",
        "-i", "pipe:0",
        "-map", "0:v?", "-map", "0:a?",
        "-c", "copy",
        "-f", "segment",
        "-segment_time", str(segment_seconds),
        "-reset_timestamps", "1",
        "-strftime", "1",
        str(out_dir / SEGMENT_PATTERN),
    ]  # fmt: skip


async def probe_duration(path: Path) -> float | None:
    proc = await asyncio.create_subprocess_exec(
        "ffprobe", "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(path),
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )  # fmt: skip
    out, _ = await proc.communicate()
    try:
        return float(out.decode().strip())
    except ValueError:
        return None


class StreamRecorder:
    def __init__(self, streamer: Streamer, settings: RecorderSettings, store: Store):
        self.s = streamer
        self.settings = settings
        self.store = store
        self.live = False

    async def run(self, stop: asyncio.Event):
        while not stop.is_set():
            produced = await self._record_once(stop)
            if stop.is_set():
                break
            delay = RETRY_AFTER_DROP_S if produced else self.settings.poll_seconds
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=delay)

    async def _record_once(self, stop: asyncio.Event) -> bool:
        started = time.time()
        stamp = datetime.fromtimestamp(started, UTC).strftime("%Y%m%dT%H%M%SZ")
        out_dir = self.settings.data_dir / self.s.login / stamp
        out_dir.mkdir(parents=True, exist_ok=True)

        r, w = os.pipe()
        try:
            sl = await asyncio.create_subprocess_exec(
                *streamlink_cmd(self.s), stdout=w, stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
            )  # fmt: skip
            ff = await asyncio.create_subprocess_exec(
                *ffmpeg_cmd(out_dir, self.settings.segment_seconds),
                stdin=r, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.PIPE,
                env={**os.environ, "TZ": "UTC"}, start_new_session=True,
            )  # fmt: skip
        finally:
            os.close(r)
            os.close(w)

        rec_id: int | None = None
        state = {"files": [], "last_growth": time.time(), "last_size": -1}
        log_sl = (out_dir / "streamlink.log").open("a", encoding="utf-8")
        log_ff = (out_dir / "ffmpeg.log").open("a", encoding="utf-8")

        async def pump(stream, fh, parse_ads: bool):
            async for raw in stream:
                line = raw.decode("utf-8", errors="replace")
                fh.write(line)
                fh.flush()
                if parse_ads and (ad := parse_ad_line(line)):
                    self.store.add_event(self.s.login, time.time(), "ad_break", ad)

        async def watch_segments():
            nonlocal rec_id
            while True:
                await asyncio.sleep(2)
                files = sorted(out_dir.glob("*.ts"))
                for f in files:
                    if f in state["files"]:
                        continue
                    if rec_id is None:
                        rec_id = self.store.start_recording(self.s.login, out_dir, started)
                        self.live = True
                        log.info("%s: LIVE, recording to %s", self.s.login, out_dir)
                        notify.send(f"🔴 {self.s.login} is live — recording")
                    if state["files"]:
                        await self._finish(state["files"][-1])
                    self.store.add_segment(rec_id, self.s.login, f, segment_start_epoch(f))
                    state["files"].append(f)
                if state["files"]:
                    size = state["files"][-1].stat().st_size
                    if size != state["last_size"]:
                        state["last_size"], state["last_growth"] = size, time.time()
                    elif time.time() - state["last_growth"] > self.settings.stall_seconds:
                        log.warning(
                            "%s: no data for %ss, restarting",
                            self.s.login,
                            self.settings.stall_seconds,
                        )  # noqa: E501
                        self.store.add_event(self.s.login, time.time(), "stall")
                        raise TimeoutError("stall")

        pumps = [
            asyncio.ensure_future(pump(sl.stderr, log_sl, True)),
            asyncio.ensure_future(pump(ff.stderr, log_ff, False)),
        ]
        watcher = asyncio.ensure_future(watch_segments())
        stopper = asyncio.ensure_future(stop.wait())
        sl_wait = asyncio.ensure_future(sl.wait())
        reason = "ended"
        try:
            done, _ = await asyncio.wait(
                {sl_wait, watcher, stopper}, return_when=asyncio.FIRST_COMPLETED
            )
            if stopper in done:
                reason = "shutdown"
            elif watcher in done:
                reason = "stall"
        finally:
            await self._terminate(sl, ff)
            for t in (watcher, stopper):
                t.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            log_sl.close()
            log_ff.close()

        # final segment indexing after ffmpeg flushed
        for f in sorted(out_dir.glob("*.ts")):
            if f in state["files"]:
                continue
            if rec_id is None:
                rec_id = self.store.start_recording(self.s.login, out_dir, started)
            self.store.add_segment(rec_id, self.s.login, f, segment_start_epoch(f))
            state["files"].append(f)
        if state["files"]:
            await self._finish(state["files"][-1])

        self.live = False
        if rec_id is None:
            # Offline: no video produced. Keep a hint if it wasn't the usual "offline" exit.
            sl_log = (out_dir / "streamlink.log").read_text(encoding="utf-8", errors="replace")
            if sl_log.strip() and "No playable streams" not in sl_log:
                log.warning("%s: streamlink exited: %s", self.s.login, sl_log.strip()[-300:])
            for p in out_dir.glob("*.log"):  # only log files can be here (no .ts exists)
                p.unlink()
            with contextlib.suppress(OSError):
                out_dir.rmdir()
            return False

        self.store.end_recording(rec_id, time.time(), reason)
        gb = sum(f.stat().st_size for f in state["files"] if f.exists()) / 1e9
        mins = (time.time() - started) / 60
        log.info("%s: recording stopped (%s), %.0f min, %.2f GB", self.s.login, reason, mins, gb)
        if reason != "shutdown":
            notify.send(
                f"⏹ {self.s.login}: recording stopped ({reason}), {mins:.0f} min, {gb:.2f} GB"
            )  # noqa: E501
        return True

    async def _finish(self, f: Path):
        if f.exists():
            self.store.finish_segment(f, await probe_duration(f), f.stat().st_size)

    @staticmethod
    async def _terminate(sl, ff):
        # Stop streamlink first; ffmpeg then sees EOF and closes the last segment cleanly.
        await _stop(sl, signal.SIGINT, grace=10)
        try:
            await asyncio.wait_for(ff.wait(), timeout=15)
        except TimeoutError:
            await _stop(ff, signal.SIGTERM, grace=10)


async def _stop(proc, sig, grace: float):
    if proc.returncode is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        proc.send_signal(sig)
    try:
        await asyncio.wait_for(proc.wait(), timeout=grace)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
