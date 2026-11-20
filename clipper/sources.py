"""A *source* is one stream timeline (a downloaded VOD or a live recording). Every later stage —
stage-2 judging, rendering — talks to sources only through this interface, so it does not care
where the media came from. Times are wall-clock UTC epoch seconds.
"""

import json
import logging
import tempfile
from dataclasses import dataclass
from pathlib import Path

from clipper.chat.lexicon import Lexicon
from clipper.media.ffmpeg import SegmentPart, align_offset, concat_copy
from clipper.scoring.signals import recording_signals, vod_signals
from clipper.scoring.stage1 import Signals
from clipper.store import Store
from clipper.twitch.vod import download_window

log = logging.getLogger(__name__)


@dataclass
class VideoCut:
    path: Path
    offset: float  # seconds into `path` where the requested window starts (exact)


class Source:
    key: str
    login: str
    t0: float
    title: str
    game: str

    def signals(self, with_clips: bool = True) -> Signals:
        raise NotImplementedError

    def audio_for(self, a: float, b: float) -> tuple[Path, float]:
        """(file, offset in file) holding audio for epoch [a, b]."""
        raise NotImplementedError

    def video_for(self, a: float, b: float, workdir: Path) -> VideoCut:
        raise NotImplementedError

    def chat_sample(self, a: float, b: float, limit: int = 40) -> list[str]:
        return []


class VodSource(Source):
    def __init__(self, store: Store, vod_id: str):
        v = store.conn.execute("SELECT * FROM vods WHERE id=?", (vod_id,)).fetchone()
        if v is None:
            raise KeyError(f"VOD {vod_id} not fetched")
        self.store, self.vod_id = store, vod_id
        self.key, self.login, self.t0 = f"vod:{vod_id}", v["login"], v["created_epoch"]
        self.title, self.game = v["title"] or "", ""
        self.audio_path = Path(v["audio_path"])

    def signals(self, with_clips: bool = True) -> Signals:
        return vod_signals(self.store, self.vod_id, with_clips=with_clips)

    def audio_for(self, a, b):
        return self.audio_path, a - self.t0

    def video_for(self, a, b, workdir):
        # HLS VOD chunks snap the start; download a margin and find the exact start by audio.
        margin = 12.0
        start_off = max(a - self.t0 - margin, 0.0)
        out = download_window(self.vod_id, start_off, (b - a) + margin + 2, workdir / "source.ts")
        true_start = align_offset(out, self.audio_path, ref_guess=start_off, search=margin + 4)
        return VideoCut(out, offset=(a - self.t0) - true_start)


class RecordingSource(Source):
    def __init__(self, store: Store, rec_id: int, lexicon: Lexicon | None = None):
        r = store.conn.execute("SELECT * FROM recordings WHERE id=?", (rec_id,)).fetchone()
        if r is None:
            raise KeyError(f"recording {rec_id} not found")
        self.store, self.rec_id, self.lexicon = store, rec_id, lexicon or Lexicon.load()
        self.key, self.login, self.t0 = f"rec:{rec_id}", r["login"], r["started_epoch"]
        self.title, self.game = r["title"] or "", r["game_name"] or ""

    def game_at(self, epoch: float) -> str:
        """Twitch category at that moment (streamers switch, e.g. to "I'm Only Sleeping")."""
        r = self.store.conn.execute(
            "SELECT data FROM events WHERE login=? AND kind='game_change' AND epoch BETWEEN ? AND ?"
            " ORDER BY epoch DESC LIMIT 1",
            (self.login, self.t0 - 60, epoch),
        ).fetchone()
        if r is not None:
            try:
                return json.loads(r["data"]).get("game") or ""
            except (TypeError, ValueError):
                pass
        return self.game

    def signals(self, with_clips: bool = True, t_from=None, t_to=None) -> Signals:
        return recording_signals(self.store, self.rec_id, t_from, t_to, self.lexicon, with_clips)

    def _segments(self, a: float, b: float):
        """Segments covering [a, b]. Raises KeyError unless the whole window is on disk: a gap, or
        an unfinished segment that is not the recording's last one (the mirror is still copying
        it after the Mac slept), would otherwise cut the wrong moment (2026-11-20: one half-copied
        file gave five "different" zloyn clips with the same video)."""
        rows = self.store.conn.execute(
            "SELECT path, start_epoch, duration FROM segments WHERE recording_id=? AND deleted_epoch IS NULL"
            " ORDER BY start_epoch",
            (self.rec_id,),
        ).fetchall()
        newest = self.store.conn.execute(
            "SELECT MAX(start_epoch) FROM segments WHERE recording_id=?", (self.rec_id,)
        ).fetchone()[0]
        out = []
        for r in rows:
            dur = r["duration"]
            if dur is None and r["start_epoch"] == newest:
                dur = 1e9  # the open, growing segment
            elif dur is None:
                dur = 0.0  # incomplete copy: no usable video yet
            elif dur < 0:
                dur = 300.0  # finished, length unknown: assume a normal 5-min segment
            if r["start_epoch"] + dur >= a and r["start_epoch"] <= b and dur > 0:
                out.append((r, dur))
        covered = a
        for r, dur in out:
            if r["start_epoch"] > covered + 2.0:
                break
            covered = max(covered, r["start_epoch"] + dur)
        if not out or covered < b:
            raise KeyError(
                f"segments for {a:.0f}-{b:.0f} in recording {self.rec_id} not on disk yet"
            )
        return [r for r, _ in out]

    def has_video(self, a: float, b: float) -> bool:
        try:
            self._segments(a, b)
            return True
        except KeyError:
            return False

    def _joined(self, a: float, b: float, out: Path) -> tuple[Path, float]:
        segs = self._segments(a, b)
        if len(segs) == 1:
            return Path(segs[0]["path"]), a - segs[0]["start_epoch"]
        parts = [SegmentPart(Path(s["path"]), 0.0, s["duration"] or 1e9) for s in segs]
        return concat_copy(parts, out), a - segs[0]["start_epoch"]

    def audio_for(self, a, b):
        tmp = Path(tempfile.gettempdir()) / f"clipper_rec{self.rec_id}_{int(a)}.ts"
        return self._joined(a, b, tmp)

    def video_for(self, a, b, workdir):
        path, off = self._joined(a, b, workdir / "source.ts")
        return VideoCut(path, offset=off)

    def chat_sample(self, a, b, limit=40):
        rows = self.store.conn.execute(
            "SELECT login, text FROM chat WHERE channel=? AND kind='msg' AND sent_ms BETWEEN ? AND ?"
            " ORDER BY sent_ms",
            (self.login, int(a * 1000), int(b * 1000)),
        ).fetchall()
        seen, out = set(), []
        for r in rows:
            if not r["text"] or self.lexicon.is_ignored(r["login"], r["text"]) or r["text"] in seen:
                continue
            seen.add(r["text"])
            out.append(r["text"][:120])
        step = max(len(out) // limit, 1)
        return out[::step][:limit]


def open_source(store: Store, key: str) -> Source:
    kind, sid = key.split(":", 1)
    return VodSource(store, sid) if kind == "vod" else RecordingSource(store, int(sid))
