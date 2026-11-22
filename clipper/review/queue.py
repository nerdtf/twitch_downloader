"""Review decisions on clip folders (see clipper.review.lanes for the layout). Used by the web app
and the Telegram bot, so both behave the same.

  approved -> approved/ + note.txt + post.json + cover.jpg   (label stored)
  rejected -> rejected/ + note.txt                           (label stored)
  edit     -> edit/ + edit.json (what to fix)                (no label: the clip comes back)
"""

import json
import time
from datetime import UTC, datetime
from pathlib import Path

from clipper.review import lanes
from clipper.review.lanes import CLIPS, candidate_id
from clipper.store import Store

DECISIONS = ("approved", "rejected", "edit")
PLATFORMS = ("tiktok", "reels", "shorts")
REVIEWABLE = ("to_review", "backlog")  # backlog: the web app may reach past the lane


def record_label(store: Store, cid: int, decision: str, reason: str | None, clip_dir: Path):
    store.conn.execute(
        "INSERT INTO labels(candidate_id, decision, reason, epoch) VALUES (?,?,?,?)"
        " ON CONFLICT(candidate_id) DO UPDATE SET decision=excluded.decision, reason=excluded.reason",
        (cid, decision, reason, time.time()),
    )
    store.conn.execute(
        "UPDATE candidates SET status=?, clip_dir=? WHERE id=?", (decision, str(clip_dir), cid)
    )


def _text(reasons, note) -> str:
    return " | ".join([*(r for r in reasons or [] if r), *([note.strip()] if note else [])])


def decide(
    store: Store,
    rel_dir: str | Path,
    decision: str,
    note: str = "",
    root: Path = CLIPS,
    platforms=None,
    reasons=None,
    by: str = "web",
) -> Path:
    """Move a to_review clip to approved/rejected/edit and record why. Returns the new folder."""
    if decision not in DECISIONS:
        raise ValueError(f"decision must be one of {DECISIONS}")
    _, stage, _ = lanes.where(root, rel_dir)
    if stage not in REVIEWABLE:
        raise ValueError(f"not waiting for review ({stage}): {rel_dir}")
    text = _text(reasons, note)
    if decision != "approved" and len(text) < 2:
        raise ValueError("pick a reason or write a note")
    plats = [p for p in (platforms or []) if p in PLATFORMS]
    if decision == "approved" and not plats:
        raise ValueError("pick at least one platform")
    dst = lanes.move(store, Path(root) / rel_dir, decision, root)
    if text:
        (dst / "note.txt").write_text(text + "\n", encoding="utf-8")
    cid = candidate_id(dst)
    if decision == "edit":
        info = {"reasons": list(reasons or []), "note": (note or "").strip(), "by": by,
                "at": datetime.now(UTC).isoformat(), "candidate_id": cid}  # fmt: skip
        (dst / "edit.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), "utf-8")
        if cid is not None:
            store.conn.execute(
                "UPDATE candidates SET status='edit', clip_dir=? WHERE id=?", (str(dst), cid)
            )
    else:
        if cid is not None:
            record_label(store, cid, decision, text or None, dst)
        if decision == "approved":
            from clipper.post import write_post

            write_post(dst, plats, note=(note or "").strip(), reasons=list(reasons or []))
    lanes.refill(store, root)
    return dst


def add_note(store: Store, rel_dir: str | Path, note: str, root: Path = CLIPS) -> Path:
    """Append a note to a clip in any stage (Telegram reply after the decision)."""
    lanes.where(root, rel_dir)
    d = Path(root) / rel_dir
    note = note.strip()
    old = (d / "note.txt").read_text(encoding="utf-8").strip() if (d / "note.txt").exists() else ""
    text = f"{old} | {note}" if old else note
    (d / "note.txt").write_text(text + "\n", encoding="utf-8")
    cid = candidate_id(d)
    if cid is not None:
        store.conn.execute("UPDATE labels SET reason=? WHERE candidate_id=?", (text, cid))
    for name, key in (("post.json", "note"), ("edit.json", "note")):
        f = d / name
        if f.exists():
            info = json.loads(f.read_text(encoding="utf-8"))
            info[key] = f"{info.get(key) or ''} {note}".strip()
            f.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    return d


def undo(store: Store, rel_dir: str | Path, root: Path = CLIPS) -> Path:
    """Put a decided clip (approved|rejected|edit) back into to_review and drop its label."""
    _, stage, _ = lanes.where(root, rel_dir)
    if stage not in DECISIONS:
        raise ValueError(f"not a decided clip: {rel_dir}")
    dst = lanes.move(store, Path(root) / rel_dir, "to_review", root, status="rendered")
    for f in ("note.txt", "post.json", "edit.json", "cover.jpg"):
        (dst / f).unlink(missing_ok=True)
    cid = candidate_id(dst)
    if cid is not None:
        store.conn.execute("DELETE FROM labels WHERE candidate_id=?", (cid,))
    return dst


def sync(store: Store, root: Path = CLIPS) -> dict[str, int]:
    """Record decisions made by moving folders by hand, then top up the lanes."""
    out = {"approved": 0, "rejected": 0}
    for decision in out:
        for d in lanes.clip_dirs(root, decision):
            cid = candidate_id(d)
            if cid is None:
                continue
            note = d / "note.txt"
            reason = note.read_text(encoding="utf-8").strip() if note.exists() else None
            record_label(store, cid, decision, reason, d)
            out[decision] += 1
    lanes.refill(store, root)
    return out
