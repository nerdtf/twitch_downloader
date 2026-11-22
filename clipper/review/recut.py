"""Re-cut a clip sent to edit/ (or any clip folder) and put it back in to_review.

  uv run python -m clipper.review recut <id|dir> [--start -5] [--end +3] [--cam X,Y,W,H | --full]
  uv run python -m clipper.review edits            # what the owner sent to edit, and why

--start/--end move the clip edges in seconds (negative = earlier). --cam forces the facecam box
(source pixels, e.g. when the streamer moved the cam), --full renders without the cam split.
The old video is kept as clip.prev.mp4; meta.json gets a `recuts` history. Needs the source
video on the Mac (Mac TTL ~6 h; the Pi keeps 14 days: `pipeline catchup` copies it back).
"""

import json
import shutil
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

from clipper.review import lanes
from clipper.review.lanes import CLIPS, candidate_id, read_meta
from clipper.store import Store


def find(root: Path, ref: str) -> Path:
    if ref.isdigit():
        for stage in lanes.STAGES:
            for d in lanes.clip_dirs(root, stage):
                if candidate_id(d) == int(ref):
                    return d
        raise SystemExit(f"candidate {ref}: no clip folder")
    d = Path(ref) if Path(ref).is_absolute() else Path(root) / ref
    lanes.where(root, d)
    return d


def list_edits(root: Path = CLIPS) -> list[dict]:
    out = []
    for d in lanes.clip_dirs(root, "edit"):
        try:
            info = json.loads((d / "edit.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            info = {}
        out.append({"dir": str(d.relative_to(root)), **info})
    return out


def recut(
    store: Store,
    d: Path,
    start: float = 0.0,
    end: float = 0.0,
    cam: tuple[int, int, int, int] | None = None,
    full: bool = False,
    root: Path = CLIPS,
) -> Path:
    from clipper.pipeline.produce import _cam_spots
    from clipper.render.facecam import resolve_layout
    from clipper.render.layout import load_layout
    from clipper.render.render import render_clip
    from clipper.render.style import DEFAULT
    from clipper.sources import RecordingSource

    lang, stage, login = lanes.where(root, d)
    m, cid = read_meta(d), candidate_id(d)
    r = store.conn.execute("SELECT source FROM candidates WHERE id=?", (cid,)).fetchone()
    if r is None or not r["source"].startswith("rec:"):
        raise SystemExit(f"{d}: not a live-recording clip")
    src = RecordingSource(store, int(r["source"].split(":")[1]))
    a, b = m["start_epoch"] + start, m["end_epoch"] + end
    if b - a < 5:
        raise SystemExit("clip would be shorter than 5 s")
    if not src.has_video(a, b):
        raise SystemExit("source video is not on the Mac any more (copy it back with catchup)")
    work = d / ".work"
    work.mkdir(exist_ok=True)
    try:
        cut = src.video_for(a, b, work)
        lay = load_layout(login)
        if full:
            lay, why = replace(lay, kind="fit_blur"), "owner: no cam split"
        elif cam:
            lay, why = replace(lay, kind="split", facecam=tuple(cam)), f"owner cam box {list(cam)}"
        else:
            lay, why = resolve_layout(
                lay, cut.path, cut.offset, b - a, m.get("game", ""),
                recent=_cam_spots().get(login),
            )  # fmt: skip
        if (d / "clip.mp4").exists():
            shutil.move(str(d / "clip.mp4"), str(d / "clip.prev.mp4"))
        res = render_clip(
            cut.path, cut.offset, b - a, lay, d, style=DEFAULT, language=lang, login=login
        )
    finally:
        shutil.rmtree(work, ignore_errors=True)
    edit = {}
    if (d / "edit.json").exists():
        edit = json.loads((d / "edit.json").read_text(encoding="utf-8"))
        (d / "edit.json").unlink()
    m.setdefault("recuts", []).append(
        {
            "at": datetime.now(UTC).isoformat(),
            "start": start,
            "end": end,
            "layout": why,
            "request": edit,
        }  # fmt: skip
    )
    m.update(start_epoch=a, end_epoch=b, duration=round(b - a, 2), layout=lay.kind,
             layout_reason=why, subtitle_words=len(res["words"]))  # fmt: skip
    (d / "meta.json").write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    store.conn.execute("UPDATE candidates SET start_epoch=?, end_epoch=? WHERE id=?", (a, b, cid))
    (d / "note.txt").unlink(missing_ok=True)
    if stage != "to_review":
        d = lanes.move(store, d, "to_review", root, status="rendered")
    return d
