"""Learning-loop dataset: every candidate's features + LLM output + label + best views.

uv run python -m clipper.learn export data/exports/dataset.csv
uv run python -m clipper.learn train            # P(approve), once >= 300 labels exist
"""

import argparse
import csv
import json
import math
from pathlib import Path

from clipper.config import REPO_ROOT
from clipper.store import Store

MIN_LABELS = 300
FEATURES = [
    "stage1",
    "chat",
    "laugh",
    "audio",
    "clips",
    "humor",
    "intensity",
    "standalone_clarity",
    "setup_payoff",
    "words_per_second",
]


def rows(store: Store) -> list[dict]:
    q = """SELECT c.*, l.decision, (SELECT MAX(v.views) FROM posts p JOIN view_snapshots v ON v.post_id=p.id
           WHERE p.candidate_id=c.id) AS max_views FROM candidates c LEFT JOIN labels l ON l.candidate_id=c.id"""
    out = []
    for r in store.conn.execute(q):
        fam = json.loads(r["features"]).get("families", {})
        s2 = json.loads(r["stage2"]) if r["stage2"] else {}
        j = s2.get("judgement") or {}
        out.append(
            {
                "id": r["id"],
                "source": r["source"],
                "login": r["login"],
                "event_epoch": r["event_epoch"],
                "stage1": r["stage1"],
                **{k: fam.get(k) for k in ("chat", "laugh", "audio", "clips")},
                **{
                    k: j.get(k)
                    for k in (
                        "humor",
                        "intensity",
                        "standalone_clarity",
                        "setup_payoff",
                        "category",
                    )
                },
                "words_per_second": s2.get("words_per_second"),
                "final_score": r["final_score"],
                "status": r["status"],
                "gate_reason": r["gate_reason"],
                "label": r["decision"],
                "max_views": r["max_views"],
            }
        )
    return out


def train(data: list[dict], epochs: int = 500, lr: float = 0.1) -> dict:
    """Plain logistic regression (no extra deps) for P(approve)."""
    labelled = [d for d in data if d["label"] in ("approved", "rejected")]
    if len(labelled) < MIN_LABELS:
        raise SystemExit(
            f"only {len(labelled)} labels; need {MIN_LABELS} before training (CLAUDE.md)"
        )
    xs = [[float(d.get(f) or 0.0) for f in FEATURES] for d in labelled]
    ys = [1.0 if d["label"] == "approved" else 0.0 for d in labelled]
    means = [sum(c) / len(c) for c in zip(*xs, strict=True)]
    stds = [
        max((sum((v - m) ** 2 for v in c) / len(c)) ** 0.5, 1e-6)
        for c, m in zip(zip(*xs, strict=True), means, strict=True)
    ]
    xs = [[(v - m) / s for v, m, s in zip(x, means, stds, strict=True)] for x in xs]
    w, b = [0.0] * len(FEATURES), 0.0
    for _ in range(epochs):
        gw, gb = [0.0] * len(w), 0.0
        for x, y in zip(xs, ys, strict=True):
            p = 1 / (1 + math.exp(-(sum(wi * xi for wi, xi in zip(w, x, strict=True)) + b)))
            for i, xi in enumerate(x):
                gw[i] += (p - y) * xi
            gb += p - y
        w = [wi - lr * g / len(xs) for wi, g in zip(w, gw, strict=True)]
        b -= lr * gb / len(xs)
    return {
        "features": FEATURES,
        "weights": w,
        "bias": b,
        "means": means,
        "stds": stds,
        "n": len(xs),
    }


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    e = sub.add_parser("export")
    e.add_argument("out", nargs="?", default=str(REPO_ROOT / "data/exports/dataset.csv"))
    sub.add_parser("train")
    a = ap.parse_args()
    data = rows(Store(REPO_ROOT / "data/clipper.db"))
    if a.cmd == "export":
        out = Path(a.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        with out.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(data[0]) if data else ["id"])
            w.writeheader()
            w.writerows(data)
        print(f"{len(data)} rows -> {out}")
    else:
        model = train(data)
        path = REPO_ROOT / "data/models/p_approve.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(model, indent=2), encoding="utf-8")
        print(f"trained on {model['n']} labels -> {path}")


if __name__ == "__main__":
    main()
