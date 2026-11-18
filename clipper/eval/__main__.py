"""Evaluate stage-1 ranking: `uv run python -m clipper.eval [--sources vod:123 ...]`.

Gold = owner labels in data/gold/<source>.csv when present, else weak gold from viewer clips.
Viewer clips are never used as a scoring feature here (that would leak the answer).
"""

import argparse
import math
from pathlib import Path

import yaml

from clipper.config import REPO_ROOT
from clipper.eval.gold import load_owner_gold, weak_gold_from_clips
from clipper.eval.metrics import chance_precision, precision_at, recall_at
from clipper.scoring.signals import recording_signals, vod_signals
from clipper.scoring.stage1 import load_scoring_config, stage1
from clipper.store import Store


def gold_for(store: Store, source: str):
    owner = load_owner_gold(source)
    if owner is not None:
        return owner, "owner"
    kind, sid = source.split(":", 1)
    if kind == "vod":
        rows = store.conn.execute(
            "SELECT vod_offset, duration, view_count FROM twitch_clips WHERE video_id=? AND vod_offset IS NOT NULL",
            (sid,),
        ).fetchall()
        spans = [(r[0] - r[1], r[0], r[2]) for r in rows]  # vod_offset = clip END (verified)
    else:
        rec = store.conn.execute("SELECT * FROM recordings WHERE id=?", (int(sid),)).fetchone()
        rows = store.conn.execute(
            "SELECT created_epoch, duration, view_count FROM twitch_clips"
            " WHERE login=? AND created_epoch BETWEEN ? AND ?",
            (rec["login"], rec["started_epoch"], (rec["ended_epoch"] or 1e12) + 120),
        ).fetchall()
        t0 = int(rec["started_epoch"])
        spans = [(r[0] - t0 - r[1], r[0] - t0, r[2]) for r in rows]
    return weak_gold_from_clips(spans), "weak(clips)"


def signals_for(store: Store, source: str):
    kind, sid = source.split(":", 1)
    if kind == "vod":
        return vod_signals(store, sid, with_clips=False)
    return recording_signals(store, int(sid), with_clips=False)


def evaluate(store: Store, sources: list[str], cfg: dict, k_recall: int = 40) -> list[dict]:
    out = []
    for src in sources:
        try:
            sig = signals_for(store, src)
        except KeyError as e:
            out.append({"source": src, "error": str(e)})
            continue
        gold, kind = gold_for(store, src)
        events = [c.t for c in stage1(sig, cfg, k=k_recall, use_clips=False)]
        out.append(
            {
                "source": src,
                "gold": kind,
                "hours": sig.n / 3600,
                "moments": len(gold),
                "good": sum(m.good for m in gold),
                "p@10": precision_at(events, gold, 10),
                "chance": chance_precision([m for m in gold if m.good] or gold, sig.n),
                f"recall@{k_recall}": recall_at(events, gold, k_recall),
            }
        )
    return out


def print_table(rows: list[dict], title: str):
    print(f"\n{title}")
    print(
        f"{'source':<18} {'gold':<11} {'hours':>5} {'moments':>7} {'good':>4} "
        f"{'p@10':>5} {'chance':>6} {'recall@40':>9}"
    )
    for r in rows:
        if "error" in r:
            print(f"{r['source']:<18} ERROR {r['error']}")
            continue
        rec = r["recall@40"]
        print(
            f"{r['source']:<18} {r['gold']:<11} {r['hours']:5.1f} {r['moments']:7d} {r['good']:4d} "
            f"{r['p@10']:5.2f} {r['chance']:6.2f} {'n/a' if math.isnan(rec) else f'{rec:.2f}':>9}"
        )
    ok = [r for r in rows if "error" not in r]
    if ok:
        mean = lambda key: sum(r[key] for r in ok if not math.isnan(r[key])) / len(ok)  # noqa: E731
        print(
            f"{'MEAN':<18} {'':<11} {'':>5} {'':>7} {'':>4} {mean('p@10'):5.2f} {mean('chance'):6.2f} "
            f"{mean('recall@40'):9.2f}"
        )


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sources", nargs="*")
    ap.add_argument("--db", default=str(REPO_ROOT / "data/clipper.db"))
    ap.add_argument("--eval-config", default=str(REPO_ROOT / "config/eval.yaml"))
    ap.add_argument("--scoring-config", default=str(REPO_ROOT / "config/scoring.yaml"))
    args = ap.parse_args()
    store = Store(args.db)
    cfg = load_scoring_config(Path(args.scoring_config))
    if args.sources:
        print_table(evaluate(store, args.sources, cfg), "custom")
        return
    ev = yaml.safe_load(Path(args.eval_config).read_text(encoding="utf-8"))
    print_table(evaluate(store, ev.get("train", []), cfg), "TRAIN")
    print_table(evaluate(store, ev.get("holdout", []), cfg), "HOLDOUT (never tuned on)")


if __name__ == "__main__":
    main()
