"""Render judged candidates into the review queue: data/clips/pending/<login>/<id>/."""

import json
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path

from clipper.config import REPO_ROOT
from clipper.pipeline.stage2 import Judged
from clipper.render.caption import build_caption
from clipper.render.layout import load_layout
from clipper.render.render import render_clip
from clipper.sources import Source
from clipper.store import Store

log = logging.getLogger(__name__)
CLIPS = REPO_ROOT / "data/clips"


def render_judged(store: Store, source: Source, j: Judged, root: Path = CLIPS) -> Path:
    stamp = datetime.fromtimestamp(j.event, UTC).strftime("%Y%m%d-%H%M")
    out_dir = root / "pending" / source.login / f"{j.cand_id:05d}_{stamp}"
    work = out_dir / ".work"
    work.mkdir(parents=True, exist_ok=True)
    try:
        cut = source.video_for(j.start, j.end, work)
        hook = (j.judgement or {}).get("hook_line", "")
        result = render_clip(
            cut.path, cut.offset, j.end - j.start, load_layout(source.login), out_dir, hook=hook
        )
        title = (j.judgement or {}).get("title") or f"{source.login}: момент со стрима"
        (out_dir / "caption.txt").write_text(build_caption(source.login, title), encoding="utf-8")
        meta = {
            "candidate_id": j.cand_id,
            "source": source.key,
            "login": source.login,
            "event_utc": datetime.fromtimestamp(j.event, UTC).isoformat(),
            "start_epoch": j.start,
            "end_epoch": j.end,
            "duration": round(j.end - j.start, 2),
            "score": j.score,
            "stage1_families": j.families,
            "judgement": j.judgement,
            "transcript": j.transcript,
            "subtitle_words": len(result["words"]),
            "stream_title": source.title,
            "game": source.game,
        }
        (out_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        store.conn.execute(
            "UPDATE candidates SET status='rendered', clip_dir=? WHERE id=?",
            (str(out_dir), j.cand_id),
        )
        return out_dir
    finally:
        shutil.rmtree(work, ignore_errors=True)


def write_index(root: Path = CLIPS) -> Path:
    """data/clips/index.html — open in a browser to review pending clips, best first."""
    items = []
    for meta_path in sorted((root / "pending").glob("*/*/meta.json")):
        m = json.loads(meta_path.read_text(encoding="utf-8"))
        items.append((m.get("score", 0), meta_path.parent, m))
    items.sort(key=lambda x: -x[0])
    cards = []
    for score, d, m in items:
        rel = d.relative_to(root)
        j = m.get("judgement") or {}
        why = j.get("reason") or "unjudged (stage-1 only)"
        cards.append(
            f"<div class=c><video src='{rel}/clip.mp4' controls preload=metadata></video>"
            f"<p><b>{m['login']}</b> · score {score:.1f} · {m['duration']:.0f}s · {m['event_utc'][:16]}</p>"
            f"<p>{why}</p><p class=f>{rel}</p></div>"
        )
    html = (
        "<!doctype html><meta charset=utf-8><title>Clip review</title><style>"
        "body{font:14px system-ui;background:#111;color:#eee;margin:16px}"
        ".g{display:flex;flex-wrap:wrap;gap:16px}.c{width:300px}video{width:300px;border-radius:8px}"
        ".f{color:#888;font-size:12px}</style>"
        "<h2>Pending clips</h2><p>Approve: move the folder to <code>approved/&lt;login&gt;/</code>. "
        "Reject: move it to <code>rejected/&lt;login&gt;/</code> (optional <code>note.txt</code> with the "
        "reason). Then run <code>uv run python -m clipper.review sync</code>.</p>"
        f"<div class=g>{''.join(cards) or '<p>No pending clips.</p>'}</div>"
    )
    index = root / "index.html"
    root.mkdir(parents=True, exist_ok=True)
    index.write_text(html, encoding="utf-8")
    return index
