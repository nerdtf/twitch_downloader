"""Review queue (folders: clipper.review.lanes).

uv run python -m clipper.review serve      # web app: http://127.0.0.1:8765
uv run python -m clipper.review bot        # Telegram review bot (one clip at a time)
uv run python -m clipper.review lanes      # clips per streamer and stage; tops up the lanes
uv run python -m clipper.review edits      # clips the owner sent to edit/, and why
uv run python -m clipper.review recut <id> [--start -5] [--end +3] [--cam X,Y,W,H | --full]
uv run python -m clipper.review sync       # after moving folders by hand
uv run python -m clipper.review migrate    # one-off: old pending/approved/rejected layout
"""

import argparse
import json
import logging

from clipper.config import REPO_ROOT
from clipper.review import lanes
from clipper.review.lanes import CLIPS
from clipper.store import Store


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("cmd", choices=["sync", "serve", "bot", "lanes", "edits", "recut", "migrate"])
    ap.add_argument("ref", nargs="?", help="recut: candidate id or clip folder")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--start", type=float, default=0.0, help="recut: move the start (s)")
    ap.add_argument("--end", type=float, default=0.0, help="recut: move the end (s)")
    ap.add_argument("--cam", help="recut: facecam box X,Y,W,H in source pixels")
    ap.add_argument("--full", action="store_true", help="recut: no facecam split")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    store = Store(REPO_ROOT / "data/clipper.db")
    if args.cmd == "serve":
        from clipper.review.server import serve

        serve(store, CLIPS, args.port)
    elif args.cmd == "bot":
        from clipper.review.bot import run

        run(store, CLIPS)
    elif args.cmd == "edits":
        from clipper.review.recut import list_edits

        print(json.dumps(list_edits(CLIPS), ensure_ascii=False, indent=2))
    elif args.cmd == "recut":
        from clipper.review.recut import find, recut

        if not args.ref:
            raise SystemExit("recut needs a candidate id or folder")
        cam = tuple(int(x) for x in args.cam.split(",")) if args.cam else None
        d = recut(store, find(CLIPS, args.ref), args.start, args.end, cam, args.full, CLIPS)
        print("re-cut, back in review:", d)
    elif args.cmd == "migrate":
        print("moved:", lanes.migrate(store, CLIPS))
        print("promoted to to_review:", len(lanes.refill(store, CLIPS)))
    elif args.cmd == "lanes":
        lanes.refill(store, CLIPS)
        print(json.dumps(lanes.counts(CLIPS), indent=2))
    else:
        from clipper.pipeline.produce import write_index
        from clipper.review.queue import sync

        counts = sync(store)
        write_index(CLIPS)
        print(f"labels: {counts['approved']} approved, {counts['rejected']} rejected")


if __name__ == "__main__":
    main()
