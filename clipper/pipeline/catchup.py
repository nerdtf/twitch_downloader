"""Catch up on hours the Mac missed (asleep): pull them from the Pi step by step and run the live
clip finder over them as if it had been awake.

  uv run python -m clipper.pipeline catchup --hours 10 [--login zloyn shadowkekw]

The Pi keeps 14 days of video, the Mac only 6 h and has little disk, so each ~15-min step copies
just the segments it needs, pins them while it scores/renders, then unpins them (the mirror's TTL
removes them later). Chat the mirror skipped after the gap is copied first (deduplicated).
"""

import logging
import time
from types import SimpleNamespace

from clipper.config import REPO_ROOT, load_config
from clipper.env import get as env
from clipper.mirror import Mirror
from clipper.store import CHAT_COLUMNS, Store

log = logging.getLogger("clipper.catchup")
STEP = 900  # seconds of stream per pass; live --lookback (1200) overlaps it


def _sync(m: Mirror, **params) -> dict:
    r = m.http.get(f"{m.url}/sync", params=params, timeout=60)
    r.raise_for_status()
    return r.json()


def copy_missing_chat(m: Mirror, store: Store, since: float) -> int:
    """Chat rows on the Pi since `since` that the Mac lacks (mirror skips them after a gap)."""
    stop = int(store.kv_get("mirror.chat_after", "0"))  # newer rows the mirror already has
    after, added = 0, 0
    while True:
        data = _sync(m, chat_after=after, chat_from_ms=int(since * 1000),
                     events_after=1 << 62, hours=0)  # fmt: skip
        rows = [x for x in data["chat"] if x["id"] <= stop]
        if rows:
            lo, hi = min(x["sent_ms"] for x in rows), max(x["sent_ms"] for x in rows)
            have = {
                (r["channel"], r["sent_ms"], r["login"], r["text"])
                for r in store.conn.execute(
                    "SELECT channel, sent_ms, login, text FROM chat WHERE sent_ms BETWEEN ? AND ?",
                    (lo, hi),
                )
            }
            new = [
                {c: x.get(c) for c in CHAT_COLUMNS}
                for x in rows
                if (x["channel"], x["sent_ms"], x["login"], x["text"]) not in have
            ]
            store.insert_chat(new)
            added += len(new)
        if not data["chat_more"] or not data["chat"] or data["chat"][-1]["id"] >= stop:
            return added
        after = data["chat"][-1]["id"]


def ensure_segments(m: Mirror, store: Store, segs: list[dict], rid: int, lo: float, hi: float):
    """Copy (and pin) the Pi segments of one recording overlapping [lo, hi]. Returns their ids."""
    ids = []
    for s in segs:
        end = s["start_epoch"] + (s["duration"] if s["duration"] and s["duration"] > 0 else 300)
        if s["deleted"] or not s["rel"] or end < lo or s["start_epoch"] > hi:
            continue
        path = m.data_dir / s["rel"]
        row = store.conn.execute(
            "SELECT id, duration, deleted_epoch FROM segments WHERE path=?", (str(path),)
        ).fetchone()
        if row is None:
            path.parent.mkdir(parents=True, exist_ok=True)
            store.add_segment(rid, s["login"], path, s["start_epoch"])
            # pin before copying: the running mirror skips pinned segments, so the two never
            # append to the same file (2026-11-20: they did, doubling bratishkin's video = "lags")
            store.conn.execute("UPDATE segments SET pinned=1 WHERE path=?", (str(path),))
        elif row["deleted_epoch"] is not None:  # removed by the Mac TTL earlier: fetch again
            path.unlink(missing_ok=True)
            store.conn.execute(
                "UPDATE segments SET deleted_epoch=NULL, duration=NULL WHERE id=?", (row["id"],)
            )
        have = path.stat().st_size if path.exists() else 0
        if have > s["size"]:  # corrupt local copy: start over
            path.unlink()
            have = 0
        if s["size"] > have:
            m._append(s["rel"], path, have)
        have = path.stat().st_size if path.exists() else 0
        if s["duration"] is not None and have >= s["size"]:
            store.finish_segment(path, s["duration"] if s["duration"] >= 0 else None, have)
        sid = store.conn.execute("SELECT id FROM segments WHERE path=?", (str(path),)).fetchone()
        store.conn.execute("UPDATE segments SET pinned=1 WHERE id=?", (sid["id"],))
        ids.append(sid["id"])
    return ids


def run_catchup(args) -> None:
    from clipper.pipeline.__main__ import DB, make_judge, process_live_recording
    from clipper.pipeline.produce import write_index
    from clipper.scoring.stage1 import load_scoring_config

    url, token = env("CLIPPER_FEED_URL", "http://raspberrypi.local:8766"), env("CLIPPER_FEED_TOKEN")
    if not token:
        raise SystemExit("set CLIPPER_FEED_TOKEN in .env (same value as on the Pi)")
    rec_cfg = load_config(args.config)
    store, cfg = Store(DB), load_scoring_config()
    m = Mirror(store, rec_cfg.recorder.data_dir, url, token)
    judge = make_judge(store, args)
    since = time.time() - args.hours * 3600
    log.info(
        "catchup from %s: chat +%d rows", time.ctime(since), copy_missing_chat(m, store, since)
    )
    data = _sync(m, chat_after=1 << 62, chat_from_ms=0, events_after=1 << 62, hours=args.hours)
    live = SimpleNamespace(lookback=1200, min_score=args.min_score,
                           render_min=args.render_min, daily_cap=args.daily_cap)  # fmt: skip
    for rec in data["recordings"]:
        if args.login and rec["login"] not in args.login:
            continue
        segs = [s for s in data["segments"] if s["recording_id"] == rec["id"]]
        if not segs:
            continue
        rid = m._recording(rec)
        t = max(since, rec["started_epoch"]) + STEP
        last = max(s["start_epoch"] + (s["duration"] or 0) for s in segs)
        stop = min(last, time.time() - live.lookback)  # the live loop covers the rest
        log.info(
            "%s: recording pi:%s, %d steps", rec["login"], rec["id"], int((stop - t) // STEP) + 1
        )
        while t <= stop + STEP:
            now = min(t, stop + 60)
            pinned = ensure_segments(m, store, segs, rid, now - live.lookback - 60, now + 60)
            try:
                process_live_recording(store, rid, cfg, judge, live, now)
            except Exception as e:
                log.exception("%s @ %s: %s", rec["login"], time.ctime(now), e)
            finally:
                store.conn.executemany(
                    "UPDATE segments SET pinned=0 WHERE id=?", [(i,) for i in pinned]
                )
            log.info("%s: done up to %s", rec["login"], time.ctime(now))
            t += STEP
    print("review:", write_index())


def add_parser(sub) -> None:
    c = sub.add_parser("catchup", help="score hours the Mac slept through (copies from the Pi)")
    c.add_argument("--config", default=str(REPO_ROOT / "config/streamers.yaml"))
    c.add_argument("--hours", type=float, required=True, help="how far back to start")
    c.add_argument("--login", nargs="*", help="only these streamers")
    c.add_argument("--min-score", type=float, default=0.35)
