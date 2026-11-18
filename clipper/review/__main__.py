"""Review queue.

  uv run python -m clipper.review serve    # web app: http://127.0.0.1:8765 (approve/reject + comment)
  uv run python -m clipper.review sync     # after moving folders by hand

Folders: data/clips/pending/<login>/<id>/ -> approved/ (ready to post) or rejected/ (+ note.txt).
"""

import argparse

from clipper.config import REPO_ROOT
from clipper.pipeline.produce import CLIPS
from clipper.review.queue import sync
from clipper.store import Store


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("cmd", choices=["sync", "serve"])
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    store = Store(REPO_ROOT / "data/clipper.db")
    if args.cmd == "serve":
        import logging

        from clipper.review.server import serve

        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
        serve(store, CLIPS, args.port)
        return
    counts = sync(store)
    print(
        f"labels: {counts['approved']} approved, {counts['rejected']} rejected; index: {CLIPS / 'index.html'}"
    )


if __name__ == "__main__":
    main()
