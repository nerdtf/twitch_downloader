"""Local review web app: approve/reject clips with a required comment, see the chat and the
signals behind each clip.

  uv run python -m clipper.review serve        # then open http://127.0.0.1:8765

Decisions move the folder to approved/ or rejected/, write note.txt and store the label
(same result as moving folders by hand + `clipper.review sync`). Listens on localhost only.
"""

import json
import logging
import mimetypes
import re
import shutil
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from clipper.chat.lexicon import Lexicon
from clipper.pipeline.produce import CLIPS, write_index
from clipper.review.queue import candidate_id, record_label
from clipper.store import Store

log = logging.getLogger(__name__)
PAGE = Path(__file__).with_name("review.html")
STYLES_PAGE = Path(__file__).with_name("styles.html")
CHAT_BEFORE, CHAT_AFTER = 10.0, 20.0  # chat lags the moment by ~3-10 s
PAGE_SIZE = 100  # clips per page load; decide them and reload for the next batch
DECISIONS = ("approved", "rejected")


def _chat(store: Store, lex: Lexicon, login: str, start: float, end: float) -> list[dict]:
    rows = store.conn.execute(
        "SELECT sent_ms, login, MAX(display_name) display_name, text FROM chat"
        " WHERE channel=? AND kind='msg' AND sent_ms BETWEEN ? AND ?"
        " GROUP BY sent_ms, login, text ORDER BY sent_ms",  # 2026-11-19 night: 2 recorders
        (login, int((start - CHAT_BEFORE) * 1000), int((end + CHAT_AFTER) * 1000)),
    ).fetchall()
    out = []
    for r in rows:
        text = r["text"] or ""
        laugh, hype = lex.classify(text)
        out.append(
            {
                "t": round(r["sent_ms"] / 1000 - start, 1),  # seconds from clip start
                "user": r["display_name"] or r["login"] or "",
                "text": text[:200],
                "laugh": laugh,
                "hype": hype,
                "ignored": lex.is_ignored(r["login"], text),
            }
        )
    return out


def _event_offset(m: dict, start: float) -> float | None:
    try:
        return round(datetime.fromisoformat(m["event_utc"]).timestamp() - start, 1)
    except (KeyError, ValueError):
        return None


def _pending_metas(root: Path) -> list[tuple[Path, dict]]:
    out = []
    for meta_path in (root / "pending").glob("*/*/meta.json"):
        try:
            out.append((meta_path.parent, json.loads(meta_path.read_text(encoding="utf-8"))))
        except (OSError, ValueError):
            continue
    out.sort(key=lambda x: -x[1].get("score", 0))
    return out


def pending_counts(root: Path = CLIPS) -> dict[str, int]:
    counts: dict[str, int] = {}
    for d in (root / "pending").glob("*/*/meta.json"):
        counts[d.parent.parent.name] = counts.get(d.parent.parent.name, 0) + 1
    return counts


def list_pending(
    store: Store,
    root: Path = CLIPS,
    lex: Lexicon | None = None,
    login: str | None = None,
    limit: int | None = None,
) -> list[dict]:
    """Pending clips, best score first; only the first `limit` get chat/DB details."""
    lex = lex or Lexicon.load()
    metas = [(d, m) for d, m in _pending_metas(root) if not login or d.parent.name == login]
    items = []
    for d, m in metas[:limit]:
        start, end = m.get("start_epoch", 0), m.get("end_epoch", 0)
        cid = candidate_id(d)
        row = (
            store.conn.execute(
                "SELECT stage1, features, gate_reason FROM candidates WHERE id=?", (cid,)
            ).fetchone()
            if cid is not None
            else None
        )
        feats = json.loads(row["features"]) if row else {}
        items.append(
            {
                "dir": str(d.relative_to(root)),
                "login": m.get("login"),
                "layout": m.get("layout", ""),
                "layout_reason": m.get("layout_reason", ""),
                "score": m.get("score", 0),
                "duration": m.get("duration", 0),
                "event_utc": m.get("event_utc", ""),
                "event_offset": _event_offset(m, start),
                "families": m.get("stage1_families") or feats.get("families") or {},
                "penalties": feats.get("penalties") or {},
                "judgement": m.get("judgement"),
                "transcript": m.get("transcript", ""),
                "stream_title": m.get("stream_title", ""),
                "caption": (d / "caption.txt").read_text(encoding="utf-8")
                if (d / "caption.txt").exists()
                else "",
                "chat": _chat(store, lex, m.get("login", ""), start, end),
            }
        )
    return items


def decide(store: Store, rel_dir: str, decision: str, note: str, root: Path = CLIPS) -> Path:
    """Move pending/<login>/<id> to <decision>/<login>/<id>, write note.txt, store the label."""
    if decision not in DECISIONS:
        raise ValueError("decision must be approved or rejected")
    note = (note or "").strip()
    if len(note) < 2:
        raise ValueError("a comment is required")
    pending = (root / "pending").resolve()
    src = (root / rel_dir).resolve()
    if src.parent.parent != pending or not src.is_dir():
        raise ValueError(f"not a pending clip: {rel_dir}")
    dst = root / decision / src.parent.name / src.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    (dst / "note.txt").write_text(note + "\n", encoding="utf-8")
    cid = candidate_id(dst)
    if cid is not None:
        record_label(store, cid, decision, note, dst)
    write_index(root)
    return dst


def undo(store: Store, rel_dir: str, root: Path = CLIPS) -> Path:
    """Put a decided clip (approved|rejected/<login>/<id>) back to pending and drop its label."""
    src = (root / rel_dir).resolve()
    if src.parent.parent not in {(root / d).resolve() for d in DECISIONS} or not src.is_dir():
        raise ValueError(f"not a decided clip: {rel_dir}")
    dst = root / "pending" / src.parent.name / src.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    (dst / "note.txt").unlink(missing_ok=True)
    cid = candidate_id(dst)
    if cid is not None:
        store.conn.execute("DELETE FROM labels WHERE candidate_id=?", (cid,))
        store.conn.execute(
            "UPDATE candidates SET status='rendered', clip_dir=? WHERE id=?", (str(dst), cid)
        )
    write_index(root)
    return dst


def list_styles(root: Path = CLIPS) -> list[dict]:
    """Style previews (clipper.render.variants): styles/<login>/<id>/variants.json."""
    out = []
    for f in sorted((root / "styles").glob("*/*/variants.json"), reverse=True):
        try:
            info = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        info["dir"] = str(f.parent.relative_to(root))
        out.append(info)
    return out


def pick_style(rel_dir: str, pick: str, note: str, root: Path = CLIPS) -> None:
    """Store the owner's favourite variant in variants.json and append it to styles/picks.jsonl."""
    styles = (root / "styles").resolve()
    f = (root / rel_dir / "variants.json").resolve()
    if styles not in f.parents or not f.is_file():
        raise ValueError(f"not a style preview: {rel_dir}")
    info = json.loads(f.read_text(encoding="utf-8"))
    if pick not in {v["name"] for v in info.get("variants", [])}:
        raise ValueError(f"unknown variant: {pick}")
    info["pick"], info["note"] = pick, (note or "").strip()
    f.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    rec = {"dir": rel_dir, "pick": pick, "note": info["note"], "at": datetime.now().isoformat()}
    with (styles / "picks.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


class _Handler(BaseHTTPRequestHandler):
    store: Store
    root: Path

    def log_message(self, fmt, *args):  # keep the terminal quiet
        log.debug(fmt, *args)

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, obj):
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json")

    def do_GET(self):
        path = unquote(urlparse(self.path).path)
        if path in ("/", "/index.html"):
            return self._send(200, PAGE.read_bytes(), "text/html; charset=utf-8")
        if path in ("/styles", "/styles.html"):
            return self._send(200, STYLES_PAGE.read_bytes(), "text/html; charset=utf-8")
        if path == "/api/health":
            from clipper.health import summary

            return self._json(200, summary(self.store))
        if path == "/api/styles":
            return self._json(200, {"clips": list_styles(self.root)})
        if path == "/api/clips":
            q = parse_qs(urlparse(self.path).query)
            login = (q.get("login") or [""])[0] or None
            counts = pending_counts(self.root)
            clips = list_pending(self.store, self.root, login=login, limit=PAGE_SIZE)
            total = counts.get(login, 0) if login else sum(counts.values())
            return self._json(200, {"total": total, "counts": counts, "clips": clips})
        if path.startswith("/clips/"):
            return self._file(path[len("/clips/") :])
        self._send(404, b"not found", "text/plain")

    do_HEAD = do_GET

    def _file(self, rel: str):
        f = (self.root / rel).resolve()
        if self.root.resolve() not in f.parents or not f.is_file():
            return self._send(404, b"not found", "text/plain")
        size = f.stat().st_size
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        m = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range", ""))
        if not m:  # Safari needs Range support to play video
            return self._send(200, f.read_bytes(), ctype, {"Accept-Ranges": "bytes"})
        a = int(m.group(1)) if m.group(1) else max(size - int(m.group(2) or 0), 0)
        b = int(m.group(2)) if m.group(1) and m.group(2) else size - 1
        b = min(b, size - 1)
        if a > b:
            return self._send(416, b"", ctype, {"Content-Range": f"bytes */{size}"})
        with f.open("rb") as fh:
            fh.seek(a)
            body = fh.read(b - a + 1)
        self._send(
            HTTPStatus.PARTIAL_CONTENT,
            body,
            ctype,
            {"Accept-Ranges": "bytes", "Content-Range": f"bytes {a}-{b}/{size}"},
        )

    def do_POST(self):
        path = urlparse(self.path).path
        if path not in ("/api/decide", "/api/undo", "/api/style-pick"):
            return self._send(404, b"not found", "text/plain")
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            if path == "/api/style-pick":
                pick_style(req.get("dir", ""), req.get("pick", ""), req.get("note", ""), self.root)
                log.info("style pick %s -> %s", req.get("dir"), req.get("pick"))
                return self._json(200, {"ok": True})
            if path == "/api/undo":
                dst = undo(self.store, req.get("dir", ""), self.root)
                log.info("undo %s", dst)
                return self._json(200, {"ok": True, "dir": str(dst.relative_to(self.root))})
            dir_, dec, note = req.get("dir", ""), req.get("decision", ""), req.get("note", "")
            dst = decide(self.store, dir_, dec, note, self.root)
        except (ValueError, json.JSONDecodeError) as e:
            return self._json(400, {"error": str(e)})
        log.info("%s %s", req["decision"], dst)
        self._json(200, {"ok": True, "dir": str(dst.relative_to(self.root))})


def serve(store: Store, root: Path = CLIPS, port: int = 8765) -> None:
    handler = type("Handler", (_Handler,), {"store": store, "root": root})
    httpd = HTTPServer(("127.0.0.1", port), handler)  # single thread: one SQLite connection
    print(f"review: http://127.0.0.1:{port}  (Ctrl+C to stop)", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
