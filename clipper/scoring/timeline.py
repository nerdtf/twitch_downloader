"""Vectorised helpers over per-second arrays."""

import warnings

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view


def forward_sum(x: np.ndarray, w: int) -> np.ndarray:
    """y[t] = sum(x[t : t+w])."""
    c = np.concatenate([[0.0], np.cumsum(x, dtype=np.float64)])
    idx = np.arange(len(x))
    return (c[np.minimum(idx + w, len(x))] - c[idx]).astype(np.float32)


def forward_max(x: np.ndarray, w: int) -> np.ndarray:
    """y[t] = max(x[t : t+w])."""
    if len(x) == 0:
        return x.copy()
    padded = np.concatenate([x, np.full(w - 1, x.min(), dtype=x.dtype)])
    return sliding_window_view(padded, w).max(axis=1)


def shift(x: np.ndarray, k: int, fill: float = 0.0) -> np.ndarray:
    """y[t] = x[t + k] (look ahead by k seconds)."""
    if k == 0:
        return x.copy()
    y = np.full_like(x, fill)
    if k > 0:
        y[:-k] = x[k:]
    else:
        y[-k:] = x[:k]
    return y


def trailing_median_mad(x: np.ndarray, w: int) -> tuple[np.ndarray, np.ndarray]:
    """Median and MAD of x[t-w : t] (the past only). Early seconds use whatever past exists."""
    n = len(x)
    med = np.zeros(n, np.float32)
    mad = np.zeros(n, np.float32)
    if n == 0:
        return med, mad
    padded = np.concatenate([np.full(w, np.nan, np.float32), x.astype(np.float32)])
    win = sliding_window_view(padded, w)[:n]  # win[t] = x[t-w : t]
    with warnings.catch_warnings():  # all-NaN windows at the very start are expected
        warnings.simplefilter("ignore", RuntimeWarning)
        med = np.nanmedian(win, axis=1)
        mad = np.nanmedian(np.abs(win - med[:, None]), axis=1)
    first = x[0] if n else 0.0
    med = np.where(np.isnan(med), first, med)
    mad = np.where(np.isnan(mad), 0.0, mad)
    return med.astype(np.float32), mad.astype(np.float32)


def trailing_mean(x: np.ndarray, w: int) -> np.ndarray:
    """Mean of x[t-w : t] (the past only; early seconds use whatever past exists)."""
    c = np.concatenate([[0.0], np.cumsum(x, dtype=np.float64)])
    idx = np.arange(len(x))
    lo = np.maximum(idx - w, 0)
    n = np.maximum(idx - lo, 1)
    return ((c[idx] - c[lo]) / n).astype(np.float32)


def robust_z(x: np.ndarray, w: int, floor: float = 1.0, counts: bool = False) -> np.ndarray:
    """(x - trailing median) / scale, scale = max(1.4826·MAD, 10% of median, floor).

    counts=True (event counts like laughs per window): the floor is the Poisson noise of that
    channel, sqrt(trailing mean + 0.25), instead of a fixed number. A small chat where 3 people
    laugh at once is as unusual as a big chat where 40 do (owner, 2026-11-18: zloyn).
    """
    med, mad = trailing_median_mad(x, w)
    base = np.sqrt(trailing_mean(x, w) + 0.25) if counts else np.full_like(med, floor)
    scale = np.maximum.reduce([1.4826 * mad, 0.1 * np.abs(med), base])
    return ((x - med) / scale).astype(np.float32)


def smooth(x: np.ndarray, w: int) -> np.ndarray:
    if w <= 1 or len(x) == 0:
        return x.copy()
    k = np.ones(w, np.float32) / w
    return np.convolve(x, k, mode="same").astype(np.float32)


def pick_peaks(score: np.ndarray, min_gap: int, k: int, min_score: float = 0.0) -> list[int]:
    """Indices of the k highest local maxima, at least `min_gap` apart (greedy NMS)."""
    if len(score) < 3:
        return []
    is_max = np.r_[False, (score[1:-1] >= score[:-2]) & (score[1:-1] > score[2:]), False]
    order = np.argsort(-score)
    chosen: list[int] = []
    for i in order:
        if score[i] < min_score or len(chosen) >= k:
            break
        if is_max[i] and all(abs(int(i) - c) >= min_gap for c in chosen):
            chosen.append(int(i))
    return chosen
