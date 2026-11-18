"""Track posted clips and their views (manual entry; no TikTok scraping).

uv run python -m clipper.track posted <candidate_id> <url> [--platform tiktok]
uv run python -m clipper.track views <url> <views>
uv run python -m clipper.track submitted <url>          # submitted to the bounty program
uv run python -m clipper.track due                      # what to log / submit now
"""

import argparse
import time

from clipper.config import REPO_ROOT
from clipper.render.caption import program_for
from clipper.store import Store

CHECKPOINTS_H = (24, 48, 72, 168)


def post(store: Store, cand_id: int, url: str, platform: str, now: float | None = None) -> int:
    cur = store.conn.execute(
        "INSERT INTO posts(candidate_id, platform, url, posted_epoch) VALUES (?,?,?,?)",
        (cand_id, platform, url, time.time() if now is None else now),
    )
    return cur.lastrowid


def _post_id(store: Store, url: str) -> int:
    row = store.conn.execute("SELECT id FROM posts WHERE url=?", (url,)).fetchone()
    if row is None:
        raise SystemExit(f"unknown post url {url!r} (log it with `posted` first)")
    return row[0]


def views(store: Store, url: str, n: int, now: float | None = None) -> None:
    store.conn.execute(
        "INSERT INTO view_snapshots(post_id, epoch, views) VALUES (?,?,?)",
        (_post_id(store, url), time.time() if now is None else now, n),
    )


def due(store: Store, now: float | None = None) -> list[str]:
    """Checkpoints (24h/48h/72h/7d) without a snapshot, and program submissions nearing the limit."""
    now = time.time() if now is None else now
    out = []
    rows = store.conn.execute(
        "SELECT p.id, p.url, p.posted_epoch, p.submitted_epoch, c.login FROM posts p"
        " JOIN candidates c ON c.id = p.candidate_id"
    ).fetchall()
    for r in rows:
        age_h = (now - r["posted_epoch"]) / 3600
        snaps = [
            s[0]
            for s in store.conn.execute(
                "SELECT epoch FROM view_snapshots WHERE post_id=?", (r["id"],)
            )
        ]
        for cp in CHECKPOINTS_H:
            if age_h >= cp and not any(
                abs((s - r["posted_epoch"]) / 3600 - cp) < cp * 0.25 for s in snaps
            ):
                out.append(f"log views @{cp}h: {r['url']}")
                break
        limit_d = program_for(r["login"]).get("submit_within_days", 7)
        if r["submitted_epoch"] is None and age_h >= 24 * (limit_d - 1):
            out.append(f"SUBMIT to program before {limit_d}d: {r['url']}")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("posted")
    p.add_argument("candidate_id", type=int)
    p.add_argument("url")
    p.add_argument("--platform", default="tiktok", choices=["tiktok", "shorts", "reels"])
    v = sub.add_parser("views")
    v.add_argument("url")
    v.add_argument("views", type=int)
    s = sub.add_parser("submitted")
    s.add_argument("url")
    sub.add_parser("due")
    a = ap.parse_args()
    store = Store(REPO_ROOT / "data/clipper.db")
    if a.cmd == "posted":
        print("post id", post(store, a.candidate_id, a.url, a.platform))
    elif a.cmd == "views":
        views(store, a.url, a.views)
    elif a.cmd == "submitted":
        store.conn.execute(
            "UPDATE posts SET submitted_epoch=? WHERE id=?", (time.time(), _post_id(store, a.url))
        )
    else:
        print("\n".join(due(store)) or "nothing due")


if __name__ == "__main__":
    main()
