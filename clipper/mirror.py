"""Mac side of the Pi -> Mac split: pull recordings, segments and chat from the Pi feed into the
Mac's own DB and data/recordings, so `clipper.pipeline live` works exactly as before.

  uv run python -m clipper.mirror          # on the Mac, instead of clipper.record

Needs CLIPPER_FEED_URL (e.g. http://raspberrypi.local:8766) and CLIPPER_FEED_TOKEN in .env.
Segments are append-only .ts files, so the open one is copied incrementally with HTTP Range.
After a gap (Mac asleep) only the last `--backfill-hours` are copied: old moments are stale for
live posting. Mirrored segments are deleted by the same TTL/disk-guard rules as the recorder's.
"""

import argparse
import logging
import time
from pathlib import Path
from urllib.parse import quote

import requests

from clipper.config import REPO_ROOT, load_config
from clipper.env import get as env
from clipper.record.retention import run_retention
from clipper.store import CHAT_COLUMNS, Store

log = logging.getLogger(__name__)
CHUNK = 1 << 20
REC_META = (
    "ended_epoch",
    "exit_reason",
    "twitch_stream_id",
    "title",
    "game_name",
    "stream_started_at",
)


class Mirror:
    def __init__(
        self,
        store: Store,
        data_dir: Path,
        url: str,
        token: str,
        backfill_hours: float = 1.0,
        session: requests.Session | None = None,
    ):
        self.store, self.data_dir = store, data_dir
        self.url = url.rstrip("/")
        self.backfill = backfill_hours * 3600
        self.http = session or requests.Session()
        self.http.headers["Authorization"] = f"Bearer {token}"

    # --- one pass ---
    def tick(self, budget_s: float = 60) -> dict:
        t_end = time.time() + budget_s
        st = self.store
        now = time.time()
        r = self.http.get(
            f"{self.url}/sync",
            params={
                "chat_after": st.kv_get("mirror.chat_after", "0"),
                "chat_from_ms": int((now - self.backfill) * 1000),
                "events_after": st.kv_get("mirror.events_after", "0"),
                "hours": 12,
            },
            timeout=30,
        )
        r.raise_for_status()
        data = r.json()
        rec_map = {rec["id"]: self._recording(rec) for rec in data["recordings"]}
        n_chat = self._chat(data)
        self._events(data["events"])
        copied = self._segments(data["segments"], rec_map, data["now"], t_end)
        return {"recordings": len(rec_map), "chat": n_chat, "bytes": copied}

    def _recording(self, rec: dict) -> int:
        st, origin = self.store, f"pi:{rec['id']}"
        row = st.conn.execute("SELECT id FROM recordings WHERE origin=?", (origin,)).fetchone()
        if row is None:
            local_dir = self.data_dir / (rec["rel_dir"] or f"{rec['login']}/pi{rec['id']}")
            local_dir.mkdir(parents=True, exist_ok=True)
            rid = st.start_recording(rec["login"], local_dir, rec["started_epoch"])
            st.conn.execute("UPDATE recordings SET origin=? WHERE id=?", (origin, rid))
            log.info("%s: new recording pi:%s -> local %s", rec["login"], rec["id"], rid)
        else:
            rid = row["id"]
        st.conn.execute(
            f"UPDATE recordings SET {', '.join(f'{c}=?' for c in REC_META)} WHERE id=?",
            (*(rec.get(c) for c in REC_META), rid),
        )
        return rid

    def _chat(self, data: dict) -> int:
        rows, n = data["chat"], 0
        while rows:
            self.store.insert_chat([{c: x.get(c) for c in CHAT_COLUMNS} for x in rows])
            self.store.kv_set("mirror.chat_after", rows[-1]["id"])
            n += len(rows)
            if not data.get("chat_more"):
                break
            r = self.http.get(
                f"{self.url}/sync",
                params={"chat_after": rows[-1]["id"], "events_after": 1 << 62, "hours": 0},
                timeout=30,
            )
            r.raise_for_status()
            data = r.json()
            rows = data["chat"]
        return n

    def _events(self, events: list[dict]) -> None:
        for e in events:
            self.store.conn.execute(
                "INSERT INTO events(login, epoch, kind, data) VALUES (?,?,?,?)",
                (e["login"], e["epoch"], e["kind"], e["data"]),
            )
            self.store.kv_set("mirror.events_after", e["id"])

    def _segments(self, segs: list[dict], rec_map: dict, now: float, t_end: float) -> int:
        # open (growing) segments first, then the newest finished ones
        todo = sorted(
            (s for s in segs if s["rel"] and not s["deleted"] and s["recording_id"] in rec_map),
            key=lambda s: (s["duration"] is not None, -s["start_epoch"]),
        )
        copied = 0
        for s in todo:
            if time.time() > t_end:
                break
            path = self.data_dir / s["rel"]
            row = self.store.conn.execute(
                "SELECT duration, deleted_epoch, pinned FROM segments WHERE path=?", (str(path),)
            ).fetchone()
            if row is not None and (
                row["duration"] is not None or row["deleted_epoch"] is not None or row["pinned"]
            ):
                continue  # complete, expired locally, or being copied by `pipeline catchup`
            end = s["start_epoch"] + (s["duration"] if s["duration"] and s["duration"] > 0 else 300)
            if row is None:
                if end < now - self.backfill:
                    continue  # too old to be worth copying after a gap
                path.parent.mkdir(parents=True, exist_ok=True)
                self.store.add_segment(
                    rec_map[s["recording_id"]], s["login"], path, s["start_epoch"]
                )
            have = path.stat().st_size if path.exists() else 0
            if have > s["size"]:  # bigger than the Pi's file = corrupt (double append): refetch
                log.warning("%s: local copy larger than the Pi's, copying again", s["rel"])
                path.unlink()
                have = 0
            if s["size"] > have:
                copied += self._append(s["rel"], path, have)
            have = path.stat().st_size if path.exists() else 0
            if s["duration"] is not None and have >= s["size"]:
                self.store.finish_segment(path, s["duration"] if s["duration"] >= 0 else None, have)
        return copied

    def _append(self, rel: str, path: Path, have: int) -> int:
        r = self.http.get(
            f"{self.url}/file/{quote(rel)}",
            headers={"Range": f"bytes={have}-"},
            stream=True,
            timeout=60,
        )
        if r.status_code == 416:
            return 0
        r.raise_for_status()
        mode = "ab" if r.status_code == 206 else "wb"  # 200 = server ignored Range: rewrite
        n = 0
        with path.open(mode) as fh:
            for chunk in r.iter_content(CHUNK):
                fh.write(chunk)
                n += len(chunk)
        return n


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--config", default=str(REPO_ROOT / "config/streamers.yaml"))
    ap.add_argument("--interval", type=float, default=10, help="seconds between passes")
    ap.add_argument("--backfill-hours", type=float, default=1.0)
    ap.add_argument("--once", action="store_true", help="one pass, then exit (setup check)")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    url, token = env("CLIPPER_FEED_URL", "http://raspberrypi.local:8766"), env("CLIPPER_FEED_TOKEN")
    if not token:
        raise SystemExit("set CLIPPER_FEED_TOKEN in .env (same value as on the Pi)")
    cfg = load_config(args.config)
    store = Store(cfg.recorder.db_path)
    m = Mirror(store, cfg.recorder.data_dir, url, token, args.backfill_hours)
    ttl_map = {s.login: s.ttl_hours for s in cfg.streamers}
    default_ttl = max(ttl_map.values(), default=6)
    log.info("mirror: %s -> %s", url, cfg.recorder.data_dir)
    healthy, last_ret, last_report = None, 0.0, 0.0
    while True:
        try:
            res = m.tick()
            if healthy is not True:
                log.info("feed reachable: %s", url)
            healthy = True
            if args.once:
                print(f"mirror OK: {res}")
                return
            if time.time() - last_report > 600:
                log.info("mirror status: %s", res)
                last_report = time.time()
        except (requests.RequestException, ValueError) as e:
            if healthy is not False:
                log.warning("feed unreachable (%s): %s — retrying", url, e)
            healthy = False
            if args.once:
                raise SystemExit(f"mirror FAILED: {e}") from e
        if time.time() - last_ret > cfg.recorder.ttl_check_seconds:
            run_retention(
                store, cfg.recorder.data_dir, ttl_map, default_ttl, cfg.recorder.min_free_gb
            )
            last_ret = time.time()
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
