"""Folder-based review queue: data/clips/{pending,approved,rejected}/<login>/<id>/ -> labels."""

import time
from pathlib import Path

from clipper.pipeline.produce import CLIPS, write_index
from clipper.store import Store


def candidate_id(clip_dir: Path) -> int | None:
    head = clip_dir.name.split("_", 1)[0]
    return int(head) if head.isdigit() else None


def record_label(store: Store, cid: int, decision: str, reason: str | None, clip_dir: Path):
    store.conn.execute(
        "INSERT INTO labels(candidate_id, decision, reason, epoch) VALUES (?,?,?,?)"
        " ON CONFLICT(candidate_id) DO UPDATE SET decision=excluded.decision, reason=excluded.reason",
        (cid, decision, reason, time.time()),
    )
    store.conn.execute(
        "UPDATE candidates SET status=?, clip_dir=? WHERE id=?", (decision, str(clip_dir), cid)
    )


def sync(store: Store, root: Path = CLIPS) -> dict[str, int]:
    """Record decisions made by moving folders by hand."""
    counts = {"approved": 0, "rejected": 0}
    for decision in counts:
        for d in sorted((root / decision).glob("*/*")):
            cid = candidate_id(d)
            if cid is None or not d.is_dir():
                continue
            note = d / "note.txt"
            reason = note.read_text(encoding="utf-8").strip() if note.exists() else None
            record_label(store, cid, decision, reason, d)
            counts[decision] += 1
    write_index(root)
    return counts
