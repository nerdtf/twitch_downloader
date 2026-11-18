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


def family_series(sig: Signals, cfg: dict) -> dict[str, np.ndarray]:
    w, base, lag = cfg["window_seconds"], cfg["baseline_seconds"], cfg["chat_lag_seconds"]
    out: dict[str, np.ndarray] = {}
    if sig.chat is not None:
        rate = tl.shift(tl.forward_sum(sig.chat.msgs, w), lag)
        out["chat"] = tl.robust_z(rate, base, floor=2.0)
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
        cnt = tl.forward_sum(made, cw)
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
                start=max(t - peaks_cfg["pre_roll_seconds"], 0),
                end=min(t + peaks_cfg["post_roll_seconds"], sig.n),
                chat_stats=stats,
            )
        )
    cands.sort(key=lambda c: -c.score)
    return cands[:k]
