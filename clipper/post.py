"""Hand-off to the posting agent (it makes TikTok / Reels / Shorts drafts; this code never posts).

  uv run python -m clipper.post list [--platform tiktok] [--lang ru] [--status todo] [--json]
  uv run python -m clipper.post show <candidate_id>
  uv run python -m clipper.post mark <candidate_id> <platform> drafted|posted|failed|skipped
                                     [--url URL] [--note TEXT]

Every approved clip has data/clips/<lang>/approved/<login>/<id>/post.json — the contract is in
docs/POSTING_AGENT.md. `mark ... posted --url` also logs the post for view tracking
(clipper.track); once every chosen platform is posted/skipped the folder moves to posted/.
"""

import argparse
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from clipper.config import REPO_ROOT
from clipper.render.caption import program_for
from clipper.review import lanes
from clipper.review.lanes import CLIPS, candidate_id, event_epoch, read_meta
from clipper.store import Store

STATUSES = ("todo", "drafted", "posted", "failed", "skipped")
DONE = ("posted", "skipped")
SHORTS_TITLE = 100  # YouTube title limit


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, UTC).isoformat(timespec="seconds")


def write_post(d: Path, platforms: list[str], note: str = "", reasons=None) -> dict:
    """post.json + cover.jpg for an approved clip folder."""
    m = read_meta(d)
    login, ev = m.get("login") or d.parent.name, event_epoch(m)
    prog = program_for(login)
    j = m.get("judgement") or {}
    caption = (
        (d / "caption.txt").read_text(encoding="utf-8").strip()
        if (d / "caption.txt").exists()
        else ""
    )
    title = (j.get("title") or "").strip()
    tags = list(prog.get("required_hashtags") or [])
    cover = None
    try:
        from clipper.media.ffmpeg import extract_frame

        at = max(
            0.0, min(ev - float(m.get("start_epoch") or ev), float(m.get("duration") or 1) - 0.5)
        )
        extract_frame(d / "clip.mp4", at + 0.5, d / "cover.jpg")
        cover = "cover.jpg"
    except Exception:  # noqa: BLE001 - a cover is nice to have, never blocks approving
        cover = None
    shorts_title = title or caption.split("\n", 1)[0] or f"{login} — момент со стрима"
    shorts_title = shorts_title[: SHORTS_TITLE - 8].rstrip() + " #shorts"
    now = time.time()
    info = {
        "version": 1,
        "candidate_id": candidate_id(d),
        "login": login,
        "lang": d.parents[2].name if d.parents[2].name in ("ru", "en") else "ru",
        "video": "clip.mp4",
        "cover": cover,
        "duration": m.get("duration"),
        "event_utc": m.get("event_utc"),
        "approved_at": _iso(now),
        "post_by": _iso(ev + 86400 * float(prog.get("window_days") or 7)),
        "twitch_url": f"https://twitch.tv/{login}",
        "stream_title": m.get("stream_title", ""),
        "game": m.get("game", ""),
        "title": title,
        "caption": caption,
        "captions": {"tiktok": caption, "reels": caption, "shorts": shorts_title + "\n" + caption},
        "hashtags": tags,
        "transcript": m.get("transcript", ""),
        "score": m.get("score"),
        "owner_note": note,
        "owner_reasons": reasons or [],
        "platforms": {
            p: {"status": "todo", "url": None, "note": "", "updated_at": None} for p in platforms
        },  # fmt: skip
    }
    (d / "post.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    return info


def _find(root: Path, cid: int) -> Path:
    for stage in ("approved", "posted"):
        for d in lanes.clip_dirs(root, stage):
            if candidate_id(d) == cid and (d / "post.json").exists():
                return d
    raise SystemExit(f"candidate {cid}: no approved clip with post.json")


def items(root: Path = CLIPS, platform=None, lang=None, status="todo") -> list[dict]:
    """Approved clips waiting on a platform, freshest moment first (live posting)."""
    out = []
    for d in lanes.clip_dirs(root, "approved", lang):
        f = d / "post.json"
        if not f.exists():
            continue  # approved before 2026-11-22: no platforms chosen
        info = json.loads(f.read_text(encoding="utf-8"))
        plats = {p: s for p, s in info["platforms"].items() if not platform or p == platform}
        if status:
            plats = {p: s for p, s in plats.items() if s["status"] == status}
        if plats:
            out.append({**info, "dir": str(d), "pending_platforms": sorted(plats)})
    out.sort(key=lambda x: x.get("event_utc") or "", reverse=True)
    return out


def mark(store: Store, cid: int, platform: str, status: str, url=None, note="", root=CLIPS):
    if status not in STATUSES:
        raise SystemExit(f"status must be one of {STATUSES}")
    d = _find(root, cid)
    f = d / "post.json"
    info = json.loads(f.read_text(encoding="utf-8"))
    if platform not in info["platforms"]:
        raise SystemExit(f"{platform} was not chosen for {cid}: {sorted(info['platforms'])}")
    if status == "posted" and not url:
        raise SystemExit("posted needs --url")
    info["platforms"][platform] = {"status": status, "url": url, "note": note,
                                   "updated_at": _iso(time.time())}  # fmt: skip
    f.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    if status == "posted":
        from clipper.track.__main__ import post

        if not store.conn.execute("SELECT 1 FROM posts WHERE url=?", (url,)).fetchone():
            post(store, cid, url, platform)
    if all(s["status"] in DONE for s in info["platforms"].values()):
        d = lanes.move(store, d, "posted", root)
    return d


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    ls = sub.add_parser("list")
    ls.add_argument("--platform", choices=["tiktok", "reels", "shorts"])
    ls.add_argument("--lang", choices=["ru", "en"])
    ls.add_argument("--status", default="todo", help="todo|drafted|failed|'' for all")
    ls.add_argument("--json", action="store_true")
    sh = sub.add_parser("show")
    sh.add_argument("candidate_id", type=int)
    mk = sub.add_parser("mark")
    mk.add_argument("candidate_id", type=int)
    mk.add_argument("platform", choices=["tiktok", "reels", "shorts"])
    mk.add_argument("status", choices=STATUSES)
    mk.add_argument("--url")
    mk.add_argument("--note", default="")
    a = ap.parse_args()
    if a.cmd == "list":
        rows = items(platform=a.platform, lang=a.lang, status=a.status or None)
        if a.json:
            json.dump(rows, sys.stdout, ensure_ascii=False, indent=2)
            return
        for r in rows:
            print(f"{r['candidate_id']:>6} {r['lang']} {r['login']:<14} {r['event_utc'][:16]} "
                  f"{','.join(r['pending_platforms']):<20} {r['dir']}")  # fmt: skip
        print(f"{len(rows)} clips")
    elif a.cmd == "show":
        print((_find(CLIPS, a.candidate_id) / "post.json").read_text(encoding="utf-8"))
    else:
        store = Store(REPO_ROOT / "data/clipper.db")
        print(mark(store, a.candidate_id, a.platform, a.status, a.url, a.note))


if __name__ == "__main__":
    main()
