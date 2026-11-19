"""Pi side of the Pi -> Mac split: serve the recorder's rows and segment files over the LAN.

  uv run python -m clipper.feed            # on the Pi, next to clipper.record

The Mac runs `clipper.mirror`, which pulls from here into its own DB and data/recordings.
Read-only: the feed never changes the recorder's DB or files. Every request needs
`Authorization: Bearer $CLIPPER_FEED_TOKEN` (shared secret in both machines' .env).
"""

import argparse
import hmac
import json
import logging
import re
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from clipper.config import REPO_ROOT, load_config
from clipper.env import get as env
from clipper.store import CHAT_COLUMNS

log = logging.getLogger(__name__)
PORT = 8766
CHAT_LIMIT = 5000
CHUNK = 1 << 20


def _rel(path: str, root: Path) -> str | None:
    try:
        return Path(path).resolve().relative_to(root).as_posix()
    except ValueError:
        return None


def sync_payload(
    db: Path, data_dir: Path, chat_after: int, chat_from_ms: int, events_after: int, hours: float
) -> dict:
    """Recent recordings + their segments (with the live file size), new chat and events."""
    root = data_dir.resolve()
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        now = time.time()
        recs = conn.execute(
            "SELECT * FROM recordings WHERE ended_epoch IS NULL OR started_epoch > ? ORDER BY id",
            (now - hours * 3600,),
        ).fetchall()
        recordings, segments = [], []
        for r in recs:
            d = dict(r)
            d["rel_dir"] = _rel(r["dir"], root)
            recordings.append(d)
            for s in conn.execute(
                "SELECT * FROM segments WHERE recording_id=? ORDER BY start_epoch", (r["id"],)
            ):
                rel = _rel(s["path"], root)
                p = root / rel if rel else None
                gone = s["deleted_epoch"] is not None or p is None or not p.is_file()
                segments.append(
                    {
                        "id": s["id"],
                        "recording_id": s["recording_id"],
                        "login": s["login"],
                        "rel": rel,
                        "start_epoch": s["start_epoch"],
                        "duration": s["duration"],
                        "size": 0 if gone else p.stat().st_size,
                        "deleted": gone,
                    }
                )
        cols = ",".join(("id",) + CHAT_COLUMNS)
        chat = [
            dict(x)
            for x in conn.execute(
                f"SELECT {cols} FROM chat WHERE id > ? AND sent_ms >= ? ORDER BY id LIMIT ?",
                (chat_after, chat_from_ms, CHAT_LIMIT),
            )
        ]
        events = [
            dict(x)
            for x in conn.execute(
                "SELECT * FROM events WHERE id > ? ORDER BY id LIMIT 2000", (events_after,)
            )
        ]
    finally:
        conn.close()
    return {
        "now": now,
        "recordings": recordings,
        "segments": segments,
        "chat": chat,
        "chat_more": len(chat) == CHAT_LIMIT,
        "events": events,
    }


class _Handler(BaseHTTPRequestHandler):
    db: Path
    data_dir: Path
    token: str

    def log_message(self, fmt, *args):
        log.debug(fmt, *args)

    def _send(self, code: int, body: bytes, ctype: str = "application/json", extra=None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        auth = self.headers.get("Authorization", "")
        if not hmac.compare_digest(auth.encode(), f"Bearer {self.token}".encode()):
            return self._send(401, b'{"error":"bad token"}')
        url = urlparse(self.path)
        if url.path == "/health":
            return self._send(200, b'{"ok":true}')
        if url.path == "/sync":
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                body = sync_payload(
                    self.db,
                    self.data_dir,
                    int(q.get("chat_after", 0)),
                    int(q.get("chat_from_ms", 0)),
                    int(q.get("events_after", 0)),
                    float(q.get("hours", 12)),
                )
            except (ValueError, sqlite3.Error) as e:
                return self._send(400, json.dumps({"error": str(e)}).encode())
            return self._send(200, json.dumps(body, ensure_ascii=False).encode())
        if url.path.startswith("/file/"):
            return self._file(unquote(url.path[len("/file/") :]))
        self._send(404, b'{"error":"not found"}')

    def _file(self, rel: str):
        root = self.data_dir.resolve()
        f = (root / rel).resolve()
        if root not in f.parents or not f.is_file():
            return self._send(404, b'{"error":"not found"}')
        size = f.stat().st_size  # may still be growing (open segment): serve what is there now
        m = re.match(r"bytes=(\d+)-(\d*)$", self.headers.get("Range", ""))
        a = int(m.group(1)) if m else 0
        b = min(int(m.group(2)), size - 1) if m and m.group(2) else size - 1
        if a > b:
            return self._send(
                416, b"", "application/octet-stream", {"Content-Range": f"bytes */{size}"}
            )
        self.send_response(206 if m else 200)
        self.send_header("Content-Type", "video/mp2t")
        self.send_header("Content-Length", str(b - a + 1))
        if m:
            self.send_header("Content-Range", f"bytes {a}-{b}/{size}")
        self.end_headers()
        with f.open("rb") as fh:
            fh.seek(a)
            left = b - a + 1
            while left > 0:
                chunk = fh.read(min(CHUNK, left))
                if not chunk:
                    break
                self.wfile.write(chunk)
                left -= len(chunk)


def serve(db: Path, data_dir: Path, token: str, host: str = "0.0.0.0", port: int = PORT):
    handler = type("Handler", (_Handler,), {"db": db, "data_dir": data_dir, "token": token})
    httpd = ThreadingHTTPServer((host, port), handler)
    log.info("feed: serving %s on %s:%d", data_dir, host, port)
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--config", default=str(REPO_ROOT / "config/streamers.yaml"))
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--port", type=int, default=PORT)
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    token = env("CLIPPER_FEED_TOKEN")
    if len(token) < 16:
        raise SystemExit(
            "set CLIPPER_FEED_TOKEN (>= 16 chars) in .env — the same value on Pi and Mac"
        )
    cfg = load_config(args.config)
    serve(cfg.recorder.db_path, cfg.recorder.data_dir, token, args.host, args.port)


if __name__ == "__main__":
    main()
