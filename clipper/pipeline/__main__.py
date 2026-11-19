"""Clip pipeline.

  uv run python -m clipper.pipeline vod <vod_id>... [--judge 8 --render 3]   # practice / backfill
  uv run python -m clipper.pipeline live                                     # during streams

Output: data/clips/pending/<login>/<id>/{clip.mp4,caption.txt,meta.json} + data/clips/index.html
"""

import argparse
import json
import logging
import time
from datetime import UTC, datetime

from clipper.config import REPO_ROOT, load_config
from clipper.judge.llm import LLMJudge
from clipper.pipeline.produce import render_judged, write_index
from clipper.pipeline.stage2 import (
    CONTEXT_PRE,
    Judged,
    insert_candidates,
    judge_candidates,
)
from clipper.scoring.stage1 import load_scoring_config, stage1
from clipper.sources import RecordingSource, VodSource
from clipper.store import Store
from clipper.twitch.helix import HelixClient, HelixError
from clipper.twitch.vod import save_clips

log = logging.getLogger("clipper.pipeline")
DB = REPO_ROOT / "data/clipper.db"


def make_judge(store: Store, args) -> LLMJudge | None:
    """Off unless --llm (owner 2026-11-18: see chat-picked clips first, add the LLM later)."""
    if not getattr(args, "llm", False):
        return None
    j = LLMJudge(store, budget_usd=args.budget)
    return j if j.api_key else None


def run_vod(args) -> None:
    store, cfg = Store(DB), load_scoring_config()
    judge = make_judge(store, args)
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


def _rendered_today(store: Store, login: str) -> int:
    """Clips already rendered today (UTC) for one streamer — the cap is per streamer so the first
    stream of the day cannot use up everyone's quota."""
    day0 = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0).timestamp()
    return store.conn.execute(
        "SELECT COUNT(*) FROM candidates WHERE login=? AND status IN ('rendered','approved','rejected')"
        " AND created_epoch>=?",
        (login, day0),
    ).fetchone()[0]


def _may_render(store: Store, login: str, score: float, args) -> bool:
    if score < args.render_min:
        return False
    if args.daily_cap and _rendered_today(store, login) >= args.daily_cap:
        log.info(
            "daily cap %d reached for %s, not rendering score=%.1f", args.daily_cap, login, score
        )
        return False
    return True


def run_live(args) -> None:
    rec_cfg = load_config(args.config)
    store, cfg = Store(DB), load_scoring_config()
    judge = make_judge(store, args)
    try:
        helix = HelixClient.from_env()
        user_ids = {
            lg: u["id"] for lg, u in helix.get_users([s.login for s in rec_cfg.streamers]).items()
        }
    except HelixError:
        helix, user_ids = None, {}
    last_clip_poll: dict[int, float] = {}
    log.info(
        "live pipeline started (min_score=%.2f, render_min=%.0f, daily_cap=%d/streamer (0=none), llm=%s)",
        args.min_score,
        args.render_min,
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
                if helix and r["login"] in user_ids and now - last_clip_poll.get(r["id"], 0) > 50:
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
    warm = src.t0 + cfg.get("warmup_seconds", 0)
    min_score = (cfg.get("min_score_per_streamer") or {}).get(src.login, args.min_score)
    settle = max(45, cfg.get("clip_window_seconds", 0) + 10)  # wait until viewer clips are in
    settled = [
        c
        for c in stage1(sig, cfg, k=10)
        if warm <= sig.t0 + c.t <= now - settle and c.score >= min_score
    ]
    skip_games = {g.lower() for g in cfg.get("skip_games") or []}
    fresh = []
    for c in settled:
        ev = sig.t0 + c.t
        if skip_games and src.game_at(ev).lower() in skip_games:
            continue  # e.g. "I'm Only Sleeping": cartoons on stream, streamer not main
        a, b = sig.t0 + c.start, sig.t0 + c.end
        near = store.conn.execute(  # any recording of this streamer: no clip of the same moment
            "SELECT 1 FROM candidates WHERE login=? AND (ABS(event_epoch - ?) < 90"
            " OR (start_epoch < ? AND end_epoch > ?))",
            (src.login, ev, b, a),
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
        if j.passed and _may_render(store, src.login, j.score, args):
            out = render_judged(store, src, j)
            log.info("NEW CLIP %s score=%.1f -> %s", src.login, j.score, out)


def run_backfill(args) -> None:
    """Render the best judged-but-unrendered candidates of recordings still on disk (e.g. after
    the cap blocked them). Respects --render-min and the per-streamer --daily-cap."""
    store = Store(DB)
    rows = store.conn.execute(
        "SELECT * FROM candidates WHERE status='judged' AND source LIKE 'rec:%' AND final_score>=?"
        " ORDER BY final_score DESC",
        (args.render_min,),
    ).fetchall()
    for r in rows:
        if args.login and r["login"] not in args.login:
            continue
        if not _may_render(store, r["login"], r["final_score"], args):
            continue
        src = RecordingSource(store, int(r["source"].split(":")[1]))
        st2 = json.loads(r["stage2"] or "{}")
        jd = st2.get("judgement") or None
        start, end = r["start_epoch"], r["end_epoch"]  # stage-1 bounds
        if jd and 0 <= float(jd["best_start"]) < float(jd["best_end"]):
            a = r["event_epoch"] - CONTEXT_PRE
            start, end = a + float(jd["best_start"]), a + float(jd["best_end"])
        j = Judged(
            r["id"], r["event_epoch"], start, end, r["final_score"], True, None, jd,
            st2.get("transcript", ""), json.loads(r["features"]).get("families", {}),
        )  # fmt: skip
        try:
            print("rendered", render_judged(store, src, j), f"score={j.score}", flush=True)
        except Exception as e:  # segment expired by TTL, etc.
            log.error("backfill render failed for candidate %s: %s", r["id"], e)
    print("review:", write_index())


def main() -> None:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--budget", type=float, default=2.0, help="total LLM spend cap in USD")
    ap.add_argument("--llm", action="store_true", help="use the LLM judge (off by default for now)")
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
    for sp in (lv, bf := sub.add_parser("backfill")):
        sp.add_argument(
            "--daily-cap", type=int, default=0, help="max clips/streamer/day, 0 = no limit"
        )
        # 2026-11-19, 229 labels: lowest approved scores 43 (buster) / 57 / 63; >=55 keeps 55 of 59
        # approvals. No daily cap: the owner reviews everything to tune the scoring.
        sp.add_argument("--render-min", type=float, default=55, help="min final score to render")
    bf.add_argument("--login", nargs="*", help="only these streamers")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    {"vod": run_vod, "live": run_live, "backfill": run_backfill}[args.cmd](args)


if __name__ == "__main__":
    main()
