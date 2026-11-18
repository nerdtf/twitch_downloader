"""Build stage-1 `Signals` from a source: a downloaded VOD or a live recording (segments + chat)."""

import hashlib
import logging
from pathlib import Path

import numpy as np

from clipper.chat.features import chat_signals
from clipper.chat.lexicon import Lexicon
from clipper.config import REPO_ROOT
from clipper.media.ffmpeg import loudness_series
from clipper.scoring.stage1 import Signals
from clipper.store import Store

log = logging.getLogger(__name__)
CACHE = REPO_ROOT / "data/cache/loudness"


def cached_loudness(path: Path) -> np.ndarray:
    """Loudness of a finished file, cached by path+size+mtime."""
    st = path.stat()
    key = hashlib.sha1(f"{path.resolve()}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()
    f = CACHE / f"{key}.npy"
    if f.exists():
        return np.load(f)
    arr = loudness_series(path)
    CACHE.mkdir(parents=True, exist_ok=True)
    np.save(f, arr)
    return arr


def clip_spans(store: Store, login: str, t0: float, t1: float, video_id: str | None = None):
    """Viewer clips as (start, end) seconds relative to t0.

    Twitch's `vod_offset` marks where the clip ENDS (verified by aligning clip audio against VOD
    audio: content starts at vod_offset - duration). Live: a clip covers ~duration before creation.
    """
    if video_id:
        rows = store.conn.execute(
            "SELECT vod_offset, duration FROM twitch_clips WHERE video_id=? AND vod_offset IS NOT NULL",
            (video_id,),
        ).fetchall()
        return [(r[0] - r[1], r[0]) for r in rows]
    rows = store.conn.execute(
        "SELECT created_epoch, duration FROM twitch_clips WHERE login=? AND created_epoch BETWEEN ? AND ?",
        (login, t0, t1 + 120),
    ).fetchall()
    return [(r[0] - t0 - r[1], r[0] - t0) for r in rows]


def vod_signals(store: Store, vod_id: str, with_clips: bool = True) -> Signals:
    v = store.conn.execute("SELECT * FROM vods WHERE id=?", (vod_id,)).fetchone()
    if v is None:
        raise KeyError(f"VOD {vod_id} not fetched (python -m clipper.twitch.vod {vod_id})")
    audio = cached_loudness(Path(v["audio_path"]))
    n = int(min(len(audio), v["duration"]))
    spans = clip_spans(store, v["login"], v["created_epoch"], v["created_epoch"] + n, vod_id)
    return Signals(
        t0=v["created_epoch"], n=n, audio_db=audio[:n], clip_spans=spans if with_clips else []
    )


def recording_signals(
    store: Store,
    rec_id: int,
    t_from: float | None = None,
    t_to: float | None = None,
    lexicon: Lexicon | None = None,
    with_clips: bool = True,
) -> Signals:
    """Signals for a live recording between t_from..t_to (epoch); defaults to the whole recording."""
    rec = store.conn.execute("SELECT * FROM recordings WHERE id=?", (rec_id,)).fetchone()
    segs = store.conn.execute(
        "SELECT path, start_epoch, duration FROM segments WHERE recording_id=? AND deleted_epoch IS NULL"
        " ORDER BY start_epoch",
        (rec_id,),
    ).fetchall()
    if not segs:
        raise KeyError(f"recording {rec_id} has no segments")
    t0 = float(int(t_from if t_from is not None else segs[0]["start_epoch"]))
    end_guess = max(
        s["start_epoch"] + (s["duration"] if s["duration"] and s["duration"] > 0 else 300)
        for s in segs
    )
    t1 = float(int(t_to if t_to is not None else end_guess))
    n = max(int(t1 - t0), 0)
    audio = np.full(n, np.nan, np.float32)
    for s in segs:
        p = Path(s["path"])
        if not p.exists():
            continue
        finished = s["duration"] is not None
        if s["start_epoch"] + (s["duration"] if finished and s["duration"] > 0 else 300) < t0:
            continue
        if s["start_epoch"] > t1:
            break
        arr = cached_loudness(p) if finished else loudness_series(p)  # open segment: no cache
        i0 = int(round(s["start_epoch"] - t0))
        a, b = max(i0, 0), min(i0 + len(arr), n)
        if b > a:
            audio[a:b] = arr[a - i0 : b - i0]
    rows = store.conn.execute(
        "SELECT sent_ms, kind, login, user_id, text FROM chat WHERE channel=? AND sent_ms BETWEEN ? AND ?",
        (rec["login"], int(t0 * 1000), int(t1 * 1000)),
    ).fetchall()
    chat = chat_signals(rows, int(t0), int(t1), lexicon or Lexicon.load())
    spans = clip_spans(store, rec["login"], t0, t1) if with_clips else []
    return Signals(t0=t0, n=n, audio_db=audio, chat=chat, clip_spans=spans)
