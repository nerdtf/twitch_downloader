"""Folder-based review queue (Telegram later, low priority).

  data/clips/pending/<login>/<id>/    new clips (open data/clips/index.html)
  data/clips/approved/<login>/<id>/   move here to approve  = ready to post
  data/clips/rejected/<login>/<id>/   move here to reject   (optional note.txt with the reason)

`uv run python -m clipper.review sync` records decisions as labels and refreshes index.html.
"""

import argparse
import time
from pathlib import Path

from clipper.config import REPO_ROOT
from clipper.pipeline.produce import CLIPS, write_index
from clipper.store import Store


def candidate_id(clip_dir: Path) -> int | None:
    head = clip_dir.name.split("_", 1)[0]
    return int(head) if head.isdigit() else None


def sync(store: Store, root: Path = CLIPS) -> dict[str, int]:
    counts = {"approved": 0, "rejected": 0}
    for decision in counts:
        for d in sorted((root / decision).glob("*/*")):
            cid = candidate_id(d)
            if cid is None or not d.is_dir():
                continue
            note = d / "note.txt"
            reason = note.read_text(encoding="utf-8").strip() if note.exists() else None
            store.conn.execute(
                "INSERT INTO labels(candidate_id, decision, reason, epoch) VALUES (?,?,?,?)"
                " ON CONFLICT(candidate_id) DO UPDATE SET decision=excluded.decision, reason=excluded.reason",
                (cid, decision, reason, time.time()),
            )
            store.conn.execute(
                "UPDATE candidates SET status=?, clip_dir=? WHERE id=?", (decision, str(d), cid)
            )
            counts[decision] += 1
    write_index(root)
    return counts


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("cmd", choices=["sync"])
    ap.parse_args()
    counts = sync(Store(REPO_ROOT / "data/clipper.db"))
    print(
        f"labels: {counts['approved']} approved, {counts['rejected']} rejected; index: {CLIPS / 'index.html'}"
    )


if __name__ == "__main__":
    main()
