"""Clip folders and review lanes (owner 2026-11-22: post clips, not only label them).

data/clips/<lang>/<stage>/<login>/<id>/        lang = ru | en (config/streamers.yaml `language`)

backlog/    rendered, waiting for a review slot
to_review/  at most LANE (15) clips per streamer; each decision frees a slot that is refilled
            from the backlog: clips from the last FRESH_S (live, newest first), then best score
edit/       sent back for a re-cut: timings, lag, webcam crop... (edit.json says what)
approved/   ready to post: post.json (platforms, captions, cover) is the posting agent's input
rejected/
posted/     every chosen platform done (moved here by `clipper.post mark`)

data/clips/to_remove/<login>/<old stage>/<id>/   streamers the owner dropped (owner deletes)
"""

import json
import shutil
import time
from datetime import datetime
from pathlib import Path

import yaml

from clipper.config import LANGUAGES, REPO_ROOT

CLIPS = REPO_ROOT / "data/clips"
STREAMERS = REPO_ROOT / "config/streamers.yaml"
STAGES = ("backlog", "to_review", "edit", "approved", "rejected", "posted")
LANE = 15
FRESH_S = 2 * 3600


def languages(path: Path = STREAMERS) -> dict[str, str]:
    """login -> language for every configured streamer (disabled ones too)."""
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    except OSError:
        return {}
    default = str((raw.get("defaults") or {}).get("language", "ru")).lower()
    out = {}
    for e in raw.get("streamers") or []:
        e = {"login": e} if isinstance(e, str) else e
        out[str(e.get("login", "")).lower()] = str(e.get("language", default)).lower()
    return out


def lang_of(login: str, langs: dict | None = None) -> str:
    lang = (langs if langs is not None else languages()).get(login, "ru")
    return lang if lang in LANGUAGES else "ru"


def where(root: Path, d: Path | str) -> tuple[str, str, str]:
    """(lang, stage, login) of a clip folder; ValueError if it is not one."""
    root = Path(root).resolve()
    p = (root / d).resolve() if not Path(d).is_absolute() else Path(d).resolve()
    try:
        parts = p.relative_to(root).parts
    except ValueError:
        raise ValueError(f"not a clip folder: {d}") from None
    if len(parts) != 4 or parts[0] not in LANGUAGES or parts[1] not in STAGES or not p.is_dir():
        raise ValueError(f"not a clip folder: {d}")
    return parts[0], parts[1], parts[2]


def candidate_id(d: Path) -> int | None:
    head = Path(d).name.split("_", 1)[0]
    return int(head) if head.isdigit() else None


def read_meta(d: Path) -> dict:
    try:
        return json.loads((Path(d) / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def clip_dirs(root: Path, stage: str, lang: str | None = None, login: str | None = None):
    out = []
    for lg in [lang] if lang else LANGUAGES:
        base = Path(root) / lg / stage
        for d in base.glob(f"{login or '*'}/*"):
            if (d / "meta.json").is_file():
                out.append(d)
    return out


def event_epoch(m: dict) -> float:
    try:
        return datetime.fromisoformat(m["event_utc"]).timestamp()
    except (KeyError, ValueError, TypeError):
        return float(m.get("start_epoch") or 0)


def priority(m: dict, now: float) -> tuple:
    """Lane order: fresh (live) clips newest first, then the rest by score."""
    ev = event_epoch(m)
    if now - ev <= FRESH_S:
        return (0, -ev)
    return (1, -float(m.get("score") or 0))


def move(store, d: Path, stage: str, root: Path = CLIPS, status: str | None = None) -> Path:
    lang, _, login = where(root, d)
    src = (Path(root) / d).resolve() if not Path(d).is_absolute() else Path(d)
    dst = Path(root) / lang / stage / login / src.name
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dst))
    cid = candidate_id(dst)
    if store is not None and cid is not None:
        if status:
            store.conn.execute(
                "UPDATE candidates SET clip_dir=?, status=? WHERE id=?", (str(dst), status, cid)
            )
        else:
            store.conn.execute("UPDATE candidates SET clip_dir=? WHERE id=?", (str(dst), cid))
    return dst


def refill(store, root: Path = CLIPS, now: float | None = None, lane: int = LANE) -> list[Path]:
    """Top up every streamer's to_review lane to `lane` clips from its backlog."""
    now = now or time.time()
    moved = []
    for lang in LANGUAGES:
        for ldir in sorted((Path(root) / lang / "backlog").glob("*")):
            login = ldir.name
            free = lane - len(clip_dirs(root, "to_review", lang, login))
            if free <= 0:
                continue
            waiting = [(d, read_meta(d)) for d in clip_dirs(root, "backlog", lang, login)]
            waiting.sort(key=lambda x: priority(x[1], now))
            for d, _ in waiting[:free]:
                moved.append(move(store, d, "to_review", root))
    return moved


def counts(root: Path = CLIPS) -> dict[str, dict[str, int]]:
    """stage -> login -> clips."""
    out: dict[str, dict[str, int]] = {s: {} for s in STAGES}
    for stage in STAGES:
        for d in clip_dirs(root, stage):
            out[stage][d.parent.name] = out[stage].get(d.parent.name, 0) + 1
    return out


def migrate(store, root: Path = CLIPS, remove=("bratishkinoff",), langs=None) -> dict[str, int]:
    """Old layout (pending|approved|rejected/<login>/<id>) -> <lang>/<stage>/...; dropped
    streamers -> to_remove/<login>/<old stage>/<id>. Idempotent."""
    langs = langs if langs is not None else languages()
    root = Path(root)
    done: dict[str, int] = {}
    for old, new in (("pending", "backlog"), ("approved", "approved"), ("rejected", "rejected"),
                     ("styles", None), ("practice", None)):  # fmt: skip
        for ldir in sorted((root / old).glob("*")):
            if not ldir.is_dir():
                continue
            login = ldir.name
            if login in remove:
                base = root / "to_remove" / login / old
            elif new is None:
                continue  # styles / practice stay where they are
            else:
                base = root / lang_of(login, langs) / new / login
            for d in sorted(ldir.iterdir()):
                if not d.is_dir():
                    continue
                dst = base / d.name
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(d), str(dst))
                cid = candidate_id(dst)
                if store is not None and cid is not None and old != "styles":
                    store.conn.execute(
                        "UPDATE candidates SET clip_dir=? WHERE id=?", (str(dst), cid)
                    )
                key = "to_remove" if login in remove else new
                done[key] = done.get(key, 0) + 1
            if not any(p for p in ldir.iterdir() if p.name != ".DS_Store"):
                shutil.rmtree(ldir)
    for old in ("pending", "approved", "rejected"):
        p = root / old
        if p.is_dir() and not any(x for x in p.iterdir() if x.name != ".DS_Store"):
            shutil.rmtree(p)
    return done
