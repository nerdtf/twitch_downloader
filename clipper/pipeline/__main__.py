"""Clip pipeline.

  uv run python -m clipper.pipeline vod <vod_id>... [--judge 8 --render 3]   # practice / backfill
  uv run python -m clipper.pipeline live                                     # during streams

Output: data/clips/pending/<login>/<id>/{clip.mp4,caption.txt,meta.json} + data/clips/index.html
"""

import argparse
import logging
import time
from datetime import UTC, datetime

from clipper.config import REPO_ROOT, load_config
from clipper.judge.llm import LLMJudge
from clipper.pipeline.produce import render_judged, write_index
from clipper.pipeline.stage2 import insert_candidates, judge_candidates
from clipper.scoring.stage1 import load_scoring_config, stage1
from clipper.sources import RecordingSource, VodSource
from clipper.store import Store
from clipper.twitch.helix import HelixClient, HelixError
from clipper.twitch.vod import save_clips

log = logging.getLogger("clipper.pipeline")
DB = REPO_ROOT / "data/clipper.db"


def make_judge(store: Store, budget: float) -> LLMJudge | None:
    j = LLMJudge(store, budget_usd=budget)
    return j if j.api_key else None


def run_vod(args) -> None:
    store, cfg = Store(DB), load_scoring_config()
    judge = make_judge(store, args.budget)
    for vid in args.vod_ids:
        src = VodSource(store, vid)
        cands = stage1(src.signals(with_clips=False), cfg, k=args.judge)
        ids = insert_candidates(store, src, cands)
        judged = judge_candidates(store, src, cands, ids, judge)
        survivors = [j for j in judged if j.passed][: args.render]
        for j in survivors:
            try:
                print("rendered", render_judged(store, src, j), f"score={j.score}", flush=True)
            except Exception as e:  # one bad window must not stop the batch
                log.error("render failed for candidate %s: %s", j.cand_id, e)
        dropped = [(j.cand_id, j.reason) for j in judged if not j.passed]
        print(f"{src.key}: {len(cands)} candidates, {len(survivors)} rendered, dropped={dropped}")
    print("review:", write_index())


def _rendered_today(store: Store) -> int:
    day0 = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    return store.conn.execute(
        "SELECT COUNT(*) FROM candidates WHERE status IN ('rendered','approved','rejected') AND created_epoch>=?",
        (day0,),
    ).fetchone()[0]


def run_live(args) -> None:
    rec_cfg = load_config(args.config)
    store, cfg = Store(DB), load_scoring_config()
    judge = make_judge(store, args.budget)
    try:
        helix = HelixClient.from_env()
        user_ids = {
            lg: u["id"] for lg, u in helix.get_users([s.login for s in rec_cfg.streamers]).items()
        }
    except HelixError:
        helix, user_ids = None, {}
    last_clip_poll: dict[int, float] = {}
    log.info(
        "live pipeline started (min_score=%.2f, daily_cap=%d, llm=%s)",
        args.min_score,
        args.daily_cap,
        "on" if judge else "off",
    )
    while True:
        now = time.time()
        recs = store.conn.execute(
            "SELECT id, login, started_epoch FROM recordings WHERE ended_epoch IS NULL OR ended_epoch > ?",
            (now - 600,),
        ).fetchall()
        for r in recs:
            try:
                if helix and r["login"] in user_ids and now - last_clip_poll.get(r["id"], 0) > 120:
                    started = datetime.fromtimestamp(r["started_epoch"], UTC).strftime(
                        "%Y-%m-%dT%H:%M:%SZ"
                    )
                    save_clips(helix, store, user_ids[r["login"]], r["login"], started)
                    last_clip_poll[r["id"]] = now
                process_live_recording(store, r["id"], cfg, judge, args, now)
            except Exception as e:
                log.exception("recording %s: %s", r["id"], e)
        write_index()
        time.sleep(args.interval)


def process_live_recording(store, rec_id, cfg, judge, args, now) -> None:
    src = RecordingSource(store, rec_id)
    t_from = max(src.t0, now - args.lookback)
    sig = src.signals(with_clips=True, t_from=t_from, t_to=now - 5)
    if sig.n < 120:
        return
    settled = [
        c for c in stage1(sig, cfg, k=10) if sig.t0 + c.t <= now - 45 and c.score >= args.min_score
    ]
    fresh = []
    for c in settled:
        ev = sig.t0 + c.t
        near = store.conn.execute(
            "SELECT 1 FROM candidates WHERE source=? AND ABS(event_epoch - ?) < 90", (src.key, ev)
        ).fetchone()
        if not near:
            c.t, c.start, c.end = (
                int(ev - src.t0),
                int(sig.t0 + c.start - src.t0),
                int(sig.t0 + c.end - src.t0),
            )
            fresh.append(c)
    if not fresh:
        return
    ids = insert_candidates(store, src, fresh)
    for j in judge_candidates(store, src, fresh, ids, judge):
        if j.passed and _rendered_today(store) < args.daily_cap:
            out = render_judged(store, src, j)
            log.info("NEW CLIP %s score=%.1f -> %s", src.login, j.score, out)


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--budget", type=float, default=2.0, help="total LLM spend cap in USD")
    sub = ap.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("vod")
    v.add_argument("vod_ids", nargs="+")
    v.add_argument("--judge", type=int, default=8, help="stage-1 candidates sent to stage-2")
    v.add_argument("--render", type=int, default=3, help="max clips rendered per VOD")
    lv = sub.add_parser("live")
    lv.add_argument("--config", default=str(REPO_ROOT / "config/streamers.yaml"))
    lv.add_argument("--interval", type=float, default=60)
    lv.add_argument(
        "--lookback", type=float, default=1200, help="seconds of stream scored each pass"
    )
    lv.add_argument("--min-score", type=float, default=0.35)
    lv.add_argument("--daily-cap", type=int, default=12)
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    run_vod(args) if args.cmd == "vod" else run_live(args)


if __name__ == "__main__":
    main()
