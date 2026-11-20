"""Render one clip in every style so the owner can compare them on the review page (Styles tab).

  uv run python -m clipper.render.variants 1203 1210 --hook 1203="КАК БРАТИШКИН СЛИЛ ИНТЕГРАЦИЮ"

Output: data/clips/styles/<login>/<id>/{base,clean,hook,punch}.mp4 + variants.json. The clip's
own folder (pending/approved/...) is not touched. Needs the source video on the Mac.
"""

import argparse
import json
import logging
import shutil
from pathlib import Path

from clipper.pipeline.produce import CLIPS, _cam_spots
from clipper.render.facecam import resolve_layout
from clipper.render.layout import load_layout
from clipper.render.render import render_clip
from clipper.render.style import STYLES
from clipper.sources import RecordingSource
from clipper.store import Store

log = logging.getLogger("clipper.variants")
STYLES_DIR = CLIPS / "styles"


def render_variants(store: Store, cid: int, hook: str = "", names=None, root: Path = CLIPS):
    r = store.conn.execute("SELECT * FROM candidates WHERE id=?", (cid,)).fetchone()
    if r is None or not r["clip_dir"]:
        raise SystemExit(f"candidate {cid}: no rendered clip")
    clip_dir = Path(r["clip_dir"])
    meta = json.loads((clip_dir / "meta.json").read_text(encoding="utf-8"))
    src = RecordingSource(store, int(r["source"].split(":")[1]))
    a, b = meta["start_epoch"], meta["end_epoch"]
    out = root / "styles" / r["login"] / clip_dir.name
    work = out / ".work"
    work.mkdir(parents=True, exist_ok=True)
    cut = src.video_for(a, b, work)
    lay, why = resolve_layout(
        load_layout(r["login"]), cut.path, cut.offset, b - a, meta.get("game", ""),
        recent=_cam_spots().get(r["login"]),
    )  # fmt: skip
    words = None
    done = []
    for name in names or STYLES:
        st = STYLES[name]
        res = render_clip(
            cut.path, cut.offset, b - a, lay, out, hook=hook, words=words, style=st,
            event_at=r["event_epoch"] - a, login=r["login"], out_name=f"{name}.mp4",
        )  # fmt: skip
        words = res["words"]  # transcribe once
        done.append({"name": name, "label": st.label, "desc": st.desc, "file": f"{name}.mp4"})
        log.info("%s: %s", cid, out / f"{name}.mp4")
    shutil.rmtree(work, ignore_errors=True)
    info = {
        "candidate_id": cid,
        "login": r["login"],
        "clip": str(clip_dir.relative_to(root)) if clip_dir.is_relative_to(root) else "",
        "score": meta.get("score"),
        "hook": hook,
        "layout": f"{lay.kind} ({why})",
        "transcript": meta.get("transcript", ""),
        "variants": done,
    }
    (out / "variants.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), "utf-8")
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("ids", nargs="+", type=int)
    ap.add_argument("--hook", action="append", default=[], help='ID="hook text"')
    ap.add_argument("--styles", nargs="*", choices=list(STYLES))
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    hooks = dict(h.split("=", 1) for h in args.hook)
    store = Store(CLIPS.parent / "clipper.db")
    for cid in args.ids:
        print(render_variants(store, cid, hooks.get(str(cid), ""), args.styles))


if __name__ == "__main__":
    main()
