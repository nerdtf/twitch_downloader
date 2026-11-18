"""Gold moments: owner labels (data/gold/<source>.csv) or weak gold from viewer-made clips.

Owner CSV format (one row per moment, seconds from source start):
    start_sec,end_sec,label,note        label = good | bad
"""

import csv
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from clipper.config import REPO_ROOT

GOLD_DIR = REPO_ROOT / "data/gold"  # owned by the owner: read-only for code


@dataclass(frozen=True)
class Moment:
    start: float
    end: float
    good: bool
    weight: float = 1.0  # e.g. summed clip views
    n: int = 1
    note: str = ""


def load_owner_gold(source: str, gold_dir: Path = GOLD_DIR) -> list[Moment] | None:
    path = gold_dir / f"{source.replace(':', '_')}.csv"
    if not path.exists():
        return None
    with path.open(encoding="utf-8") as f:
        return [
            Moment(
                float(r["start_sec"]),
                float(r["end_sec"]),
                r["label"].strip().lower() == "good",
                note=r.get("note", ""),
            )
            for r in csv.DictReader(f)
        ]


def weak_gold_from_clips(
    spans: list[tuple[float, float, int]],
    merge_gap: float = 20.0,
    good_quantile: float = 0.8,
    good_min_clips: int = 3,
) -> list[Moment]:
    """Group viewer clips (start, end, views) into moments.

    A moment is 'good' if its summed views are in the top (1 - good_quantile) of moments or at
    least `good_min_clips` different viewers clipped it.
    """
    if not spans:
        return []
    spans = sorted(spans)
    groups: list[list[tuple[float, float, int]]] = [[spans[0]]]
    for s in spans[1:]:
        if s[0] <= max(e for _, e, _ in groups[-1]) + merge_gap:
            groups[-1].append(s)
        else:
            groups.append([s])
    views = np.array([sum(v for *_, v in g) for g in groups], dtype=float)
    cut = float(np.quantile(views, good_quantile)) if len(views) > 1 else 0.0
    return [
        Moment(
            min(a for a, _, _ in g),
            max(b for _, b, _ in g),
            good=(views[i] >= cut and views[i] > 0) or len(g) >= good_min_clips,
            weight=float(views[i]),
            n=len(g),
        )
        for i, g in enumerate(groups)
    ]
