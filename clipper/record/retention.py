"""Retention: per-streamer TTL plus a free-disk guard. Only code allowed to delete recordings."""

import logging
import shutil
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SegRow:
    id: int
    login: str
    path: str
    start_epoch: float
    duration: float | None  # None = still being written -> never deleted
    size_bytes: int | None
    pinned: bool


def select_for_deletion(
    rows: list[SegRow],
    now: float,
    ttl_hours: dict[str, float],
    default_ttl_hours: float,
    free_bytes: int,
    min_free_bytes: int,
) -> list[tuple[SegRow, str]]:
    """Return (segment, reason) pairs. Pure function: no I/O."""
    candidates = sorted(
        (r for r in rows if not r.pinned and r.duration is not None),
        key=lambda r: r.start_epoch,
    )
    chosen: list[tuple[SegRow, str]] = []
    chosen_ids: set[int] = set()
    for r in candidates:
        ttl = ttl_hours.get(r.login, default_ttl_hours) * 3600
        if r.start_epoch + max(r.duration, 0) + ttl < now:
            chosen.append((r, "ttl"))
            chosen_ids.add(r.id)

    projected = free_bytes + sum(r.size_bytes or 0 for r, _ in chosen)
    for r in candidates:  # oldest first
        if projected >= min_free_bytes:
            break
        if r.id in chosen_ids:
            continue
        chosen.append((r, "disk_guard"))
        chosen_ids.add(r.id)
        projected += r.size_bytes or 0
    return chosen


def run_retention(
    store,
    data_dir: Path,
    ttl_hours: dict[str, float],
    default_ttl: float,
    min_free_gb: float,
    now: float | None = None,
) -> list[tuple[SegRow, str]]:
    now = time.time() if now is None else now
    data_dir = data_dir.resolve()
    data_dir.mkdir(parents=True, exist_ok=True)
    rows = [
        SegRow(
            id=r["id"],
            login=r["login"],
            path=r["path"],
            start_epoch=r["start_epoch"],
            duration=r["duration"],
            size_bytes=r["size_bytes"],
            pinned=bool(r["pinned"]),
        )
        for r in store.live_segments()
    ]
    free = shutil.disk_usage(data_dir).free
    chosen = select_for_deletion(rows, now, ttl_hours, default_ttl, free, int(min_free_gb * 1e9))
    for row, reason in chosen:
        p = Path(row.path).resolve()
        if not p.is_relative_to(data_dir):
            log.error("refusing to delete outside data_dir: %s", p)
            continue
        p.unlink(missing_ok=True)
        store.mark_deleted(row.id, now)
        log.info("deleted %s (%s)", p.name, reason)
    return chosen
