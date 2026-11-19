"""Stage-1: cheap per-second score over the whole stream -> candidate moments (tuned for recall).

Families: chat (message-rate z), laugh (laugh/hype-message z), audio (loudness jump z),
clips (viewer-made Twitch clips covering the second). A source may lack some families (a VOD has
no chat here) — weights are renormalised over what is available.
"""

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import yaml

from clipper.chat.features import ChatSignals
from clipper.config import REPO_ROOT
from clipper.scoring import timeline as tl

DEFAULT_CONFIG = REPO_ROOT / "config/scoring.yaml"


def load_scoring_config(path: Path = DEFAULT_CONFIG) -> dict:
    return yaml.safe_load(Path(path).read_text(encoding="utf-8"))


@dataclass
class Signals:
    """Everything stage-1 needs for one stream, on a 1-second grid starting at epoch t0."""

    t0: float
    n: int
    audio_db: np.ndarray | None = None  # per-second RMS dBFS (NaN where not recorded)
    chat: ChatSignals | None = None
    clip_spans: list[tuple[float, float]] = field(default_factory=list)  # seconds rel. to t0


@dataclass
class Candidate:
    t: int  # event second (relative to t0)
    score: float
    families: dict[str, float]  # z per family at t (or clip count)
    fired: list[str]
    penalties: dict[str, float]
    start: int
    end: int
    chat_stats: dict | None = None

    def to_features(self) -> dict:
        return {
            "families": {k: round(float(v), 3) for k, v in self.families.items()},
            "fired": self.fired,
            "penalties": self.penalties,
            "chat": self.chat_stats,
        }


def _run(active: np.ndarray, i: int, step: int, max_gap: int) -> int:
    """Walk from i in `step` direction while `active` (gaps up to max_gap); last active index."""
    last, gap, j = i, 0, i
    while 0 <= j + step < len(active):
        j += step
        if active[j]:
            last, gap = j, 0
        else:
            gap += 1
            if gap > max_gap:
                break
    return last


def clip_bounds(sig: Signals, t: int, cfg: dict) -> tuple[int, int]:
    """Clip [start, end) around event second t. Default pre/post roll; longer when chat keeps
    laughing after the reaction (end) or was already laughing before the moment (start)."""
    pk = cfg["peaks"]
    start, end = t - pk["pre_roll_seconds"], t + pk["post_roll_seconds"]
    ext = pk.get("extend")
    if ext and sig.chat is not None and sig.n > 0:
        lag = cfg.get("chat_lag_seconds", 6)
        r = tl.smooth((sig.chat.laugh + sig.chat.hype).astype(np.float32), 5)
        a = max(t - cfg.get("baseline_seconds", 600), 0)
        mu = float(np.mean(r[a:t])) if t > a else 0.0
        rlag = cfg.get("reaction_lag_seconds", lag)
        win = r[
            min(t + rlag, sig.n - 1) : min(t + rlag + cfg.get("reaction_window_seconds", 20), sig.n)
        ]
        peak = float(win.max()) if len(win) else 0.0
        if peak > mu + ext.get("min_peak", 0.5):
            level = mu + ext.get("level", 0.35) * (peak - mu)  # "still laughing" = above this
            active = r > level
            p = min(t + rlag + int(np.argmax(win)), sig.n - 1)
            gap = ext.get("max_gap_seconds", 4)
            chat_end = _run(active, p, +1, gap)  # chat time -> event time: minus the lag
            chat_start = _run(active, p, -1, gap)
            end = max(end, chat_end - lag + ext.get("tail_seconds", 3))
            start = min(start, chat_start - lag - ext.get("setup_seconds", 15))
        start = max(start, t - pk.get("max_pre_roll_seconds", pk["pre_roll_seconds"]))
        end = min(end, t + pk.get("max_post_roll_seconds", pk["post_roll_seconds"]))
    return max(start, 0), min(end, sig.n)


def family_series(sig: Signals, cfg: dict) -> dict[str, np.ndarray]:
    w, base, lag = cfg["window_seconds"], cfg["baseline_seconds"], cfg["chat_lag_seconds"]
    out: dict[str, np.ndarray] = {}
    if sig.chat is not None:
        rate = tl.shift(tl.forward_sum(sig.chat.msgs, w), lag)
        out["chat"] = tl.robust_z(rate, base, floor=2.0)
        if cfg["weights"].get("speedup") or (cfg.get("reaction_gate") or {}).get(
            "speedup_is_reaction"
        ):
            # chat suddenly getting faster: msgs/s in the next 20 s vs the previous 30 s (log ratio)
            m = sig.chat.msgs.astype(np.float32)
            nxt = tl.shift(tl.forward_sum(m, 20), cfg.get("reaction_lag_seconds", lag)) / 20
            prev = (
                np.concatenate([np.zeros(30, np.float32), tl.forward_sum(m, 30)[:-30]])[: sig.n]
                / 30
            )
            out["speedup"] = tl.robust_z(np.log((nxt + 0.2) / (prev + 0.2)), base, floor=0.15)
        if "hype" in cfg["weights"]:  # laugh (funny) and hype (wow/W spam) scored separately
            rw = cfg.get("reaction_window_seconds", w)  # laughter that lasts beats a 10 s burst
            rlag = cfg.get("reaction_lag_seconds", lag)
            laugh = tl.shift(tl.forward_sum(sig.chat.laugh, rw), rlag)
            hype = tl.shift(tl.forward_sum(sig.chat.hype, rw), rlag)
            out["laugh"] = tl.robust_z(laugh, base, counts=True)
            out["hype"] = tl.robust_z(hype, base, counts=True)
        else:
            react = tl.shift(tl.forward_sum(sig.chat.laugh + sig.chat.hype, w), lag)
            out["laugh"] = tl.robust_z(react, base, floor=1.0)
    if sig.audio_db is not None:
        db = np.nan_to_num(sig.audio_db.astype(np.float32), nan=-90.0)
        peak = tl.forward_max(db, cfg["audio_window_seconds"])
        med, _ = tl.trailing_median_mad(db, cfg["audio_baseline_seconds"])
        jump = peak - med
        jmed, jmad = np.median(jump), np.median(np.abs(jump - np.median(jump)))
        z = (jump - jmed) / max(1.4826 * jmad, 0.5)
        z[db < -60] = np.minimum(z[db < -60], 0)  # silence/dead air never scores
        out["audio"] = z.astype(np.float32)
    cw = cfg.get("clip_window_seconds")
    if sig.clip_spans and cw:
        # viewer clips MADE in the next `cw` s (span end = creation), vs this channel's usual rate
        made = np.zeros(sig.n, np.float32)
        for _a, b in sig.clip_spans:
            if 0 <= int(b) < sig.n:
                made[int(b)] += 1
        # clips made in [t + off, t + off + cw): off < 0 keeps the peak on the moment instead of
        # dragging it up to cw seconds early (viewers clip 10-60 s after the moment)
        cnt = tl.shift(tl.forward_sum(made, cw), cfg.get("clip_offset_seconds", 0))
        if cfg.get("clip_count_cap"):  # absolute: log(count) scaled so `cap` clips = z_cap
            out["clips"] = (np.log1p(cnt) / np.log1p(cfg["clip_count_cap"]) * cfg["z_cap"]).astype(
                np.float32
            )
        else:  # relative to this channel's usual clip rate
            out["clips"] = tl.robust_z(cnt, base, counts=True)
    elif sig.clip_spans:
        cover = np.zeros(sig.n, np.float32)
        for a, b in sig.clip_spans:
            cover[max(int(a), 0) : max(min(int(b) + 1, sig.n), 0)] += 1
        out["clips"] = cover
    return out


def stage1(sig: Signals, cfg: dict, k: int = 40, use_clips: bool = True) -> list[Candidate]:
    fam = family_series(sig, cfg)
    if not use_clips:
        fam.pop("clips", None)
    if not fam:
        return []
    all_fam = fam
    weights = {f: cfg["weights"][f] for f in fam if cfg["weights"].get(f, 0) > 0}
    fam = {f: fam[f] for f in weights}
    total_w = sum(weights.values())
    cap, thr = cfg["z_cap"], cfg["family_threshold_z"]
    clips_z = bool(cfg.get("clip_window_seconds"))  # clips as a z-score, not a raw count
    cmul = 1.0 if clips_z else 3.0

    norm = {}
    for f, z in fam.items():
        # clips: 1 clip ~ z 3, 2+ ~ cap
        v = np.minimum(z * 3.0, cap) if f == "clips" and not clips_z else np.clip(z, 0, cap)
        norm[f] = v / cap
    score = sum(weights[f] * norm[f] for f in fam) / total_w

    gate = cfg.get("reaction_gate")
    if gate and "laugh" in fam:
        # Owner (2026-11-18): chat decides whether a moment is funny. Without a chat laugh (or at
        # least a hype) reaction a moment is down-ranked no matter how loud it is.
        laugh_fires = fam["laugh"] >= thr
        hype_fires = fam["hype"] >= thr if "hype" in fam else np.zeros(sig.n, bool)
        if gate.get("speedup_is_reaction") and "speedup" in all_fam:
            hype_fires = hype_fires | (all_fam["speedup"] >= thr)  # chat suddenly faster
        score = score * np.where(
            laugh_fires, 1.0, np.where(hype_fires, gate["hype_only"], gate["no_reaction"])
        )
    # Agreement: chat+laugh are one family; count independent families that fire.
    groups = {"chat": ("chat", "laugh", "hype"), "audio": ("audio",), "clips": ("clips",)}
    present = [g for g, members in groups.items() if any(m in fam for m in members)]
    if len(present) >= 2 and not gate:
        fired_n = np.zeros(sig.n, np.int32)
        for g in present:
            fires = np.zeros(sig.n, bool)
            for m in groups[g]:
                if m in fam:
                    fires |= (fam[m] * (cmul if m == "clips" else 1.0)) >= thr
            fired_n += fires
        score = score * np.where(fired_n >= 2, 1.0, cfg["agreement_single_family"])

    # Penalties need window stats; apply after peak picking on a slightly larger shortlist.
    peaks_cfg = cfg["peaks"]
    smoothed = tl.smooth(score.astype(np.float32), peaks_cfg["smooth_seconds"])
    shortlist = tl.pick_peaks(smoothed, peaks_cfg["min_gap_seconds"], k * 2)
    cands = []
    for t in shortlist:
        s = float(smoothed[t])
        pens: dict[str, float] = {}
        stats = None
        if sig.chat is not None:
            lag, w = cfg["chat_lag_seconds"], cfg["window_seconds"]
            stats = sig.chat.window_stats(t + lag, t + lag + w)
            pen = cfg["penalties"]
            if stats["msgs"] >= 10 and stats["copypasta_share"] > pen["copypasta_share"]:
                pens["copypasta"] = pen["copypasta_penalty"]
            if stats["notices"] > pen["notices_per_window"]:
                pens["notices"] = pen["notices_penalty"]
            weak = pen.get("weak_chat") or {}
            if weak:  # over the laugh family's reaction window, not the 10 s stats window
                a = t + cfg.get("reaction_lag_seconds", lag)
                b = a + cfg.get("reaction_window_seconds", w)
                n_msgs = float(sig.chat.msgs[a:b].sum())
                n_laugh = float(sig.chat.laugh[a:b].sum())
                if n_msgs < weak["min_msgs"] or n_laugh < weak["min_laugh_share"] * n_msgs:
                    pens["weak_chat"] = weak["penalty"]
        s = max(s - sum(pens.values()), 0.0)
        fams = {f: float(fam[f][t]) for f in fam}
        fired = [f for f, z in fams.items() if z * (cmul if f == "clips" else 1.0) >= thr]
        cands.append(
            Candidate(
                t=t,
                score=round(s, 4),
                families=fams,
                fired=fired,
                penalties=pens,
                **dict(zip(("start", "end"), clip_bounds(sig, t, cfg), strict=True)),
                chat_stats=stats,
            )
        )
    cands.sort(key=lambda c: -c.score)
    return cands[:k]
