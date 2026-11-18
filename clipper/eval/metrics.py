"""Ranking metrics: precision@k and recall of good moments in the top-K candidates."""

from clipper.eval.gold import Moment

TOLERANCE_S = 10.0


def hits(t: float, m: Moment, tol: float = TOLERANCE_S) -> bool:
    return m.start - tol <= t <= m.end + tol


def precision_at(events: list[float], gold: list[Moment], k: int = 10) -> float:
    """Share of the top-k candidate events that land on any gold moment (good or labelled)."""
    top = events[:k]
    if not top:
        return 0.0
    targets = [m for m in gold if m.good] if any(not m.good for m in gold) else gold
    return sum(any(hits(t, m) for m in targets) for t in top) / len(top)


def recall_at(events: list[float], gold: list[Moment], k: int = 40) -> float:
    good = [m for m in gold if m.good]
    if not good:
        return float("nan")
    top = events[:k]
    return sum(any(hits(t, m) for t in top) for m in good) / len(good)


def chance_precision(gold: list[Moment], duration: float, tol: float = TOLERANCE_S) -> float:
    """Precision a random time would get: covered share of the timeline."""
    covered = sum((m.end - m.start) + 2 * tol for m in gold)
    return min(covered / duration, 1.0) if duration > 0 else 0.0
