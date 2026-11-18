"""Thin, synchronous wrappers around ffmpeg/ffprobe used by every stage."""

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

FFMPEG = ["ffmpeg", "-hide_banner", "-nostdin", "-v", "error", "-y"]
FFPROBE = ["ffprobe", "-v", "error"]
SILENCE_DB = -90.0
_RMS_RE = re.compile(r"lavfi\.astats\.Overall\.RMS_level=(-?inf|-?\d+(?:\.\d+)?)")


def _loudness_filter(block_seconds: float) -> str:
    sr = 8000
    return (
        f"aresample={sr},asetnsamples=n={max(int(sr * block_seconds), 1)}:p=0,"
        "astats=metadata=1:reset=1,ametadata=print:key=lavfi.astats.Overall.RMS_level:file=-"
    )


class FFmpegError(RuntimeError):
    pass


def run(cmd: list[str], timeout: float | None = None) -> subprocess.CompletedProcess:
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise FFmpegError(f"{cmd[0]} failed ({proc.returncode}): {proc.stderr.strip()[-500:]}")
    return proc


def _seek(start: float | None, duration: float | None) -> tuple[list[str], list[str]]:
    """Input-side -ss (fast seek) and output-side -t."""
    pre = ["-ss", f"{start:.3f}"] if start else []
    post = ["-t", f"{duration:.3f}"] if duration is not None else []
    return pre, post


def probe_duration(path: Path) -> float:
    fmt = ["-show_entries", "format=duration", "-of", "default=noprint_wrappers=1:nokey=1"]
    return float(run([*FFPROBE, *fmt, str(path)]).stdout.strip())


def probe_video(path: Path) -> dict:
    """Width, height, fps of the first video stream."""
    fmt = ["-select_streams", "v:0", "-show_entries", "stream=width,height,r_frame_rate"]
    out = run([*FFPROBE, *fmt, "-of", "default=noprint_wrappers=1", str(path)]).stdout
    kv = dict(line.split("=", 1) for line in out.strip().splitlines() if "=" in line)
    num, _, den = kv.get("r_frame_rate", "30/1").partition("/")
    return {
        "width": int(kv["width"]),
        "height": int(kv["height"]),
        "fps": float(num) / float(den or 1),
    }


def loudness_series(
    path: Path, start: float = 0.0, duration: float | None = None, block_seconds: float = 1.0
) -> np.ndarray:
    """RMS level in dBFS per block of audio (default 1 s; silence -> SILENCE_DB)."""
    pre, post = _seek(start, duration)
    af = _loudness_filter(block_seconds)
    cmd = [*FFMPEG, *pre, "-i", str(path), *post, "-vn", "-ac", "1", "-af", af]
    out = run([*cmd, "-f", "null", "-"]).stdout
    values = [SILENCE_DB if v.endswith("inf") else float(v) for v in _RMS_RE.findall(out)]
    return np.maximum(np.asarray(values, dtype=np.float32), SILENCE_DB)


def align_offset(
    clip: Path, reference: Path, ref_guess: float, search: float = 15.0, resolution: float = 0.02
) -> float:
    """Where (seconds in `reference`) does `clip` start? Cross-correlates loudness envelopes.

    `ref_guess` is the expected start; the true one is searched within ±`search` seconds.
    """
    c = loudness_series(clip, duration=20.0, block_seconds=resolution)
    lo = max(ref_guess - search, 0.0)
    r = loudness_series(reference, start=lo, duration=2 * search + 20.0, block_seconds=resolution)
    if len(c) < 50 or len(r) <= len(c):
        return ref_guess
    c = (c - c.mean()) / (c.std() + 1e-6)
    best_k, best = 0, -np.inf
    for k in range(len(r) - len(c)):
        w = r[k : k + len(c)]
        score = float(np.dot(c, (w - w.mean()) / (w.std() + 1e-6)))
        if score > best:
            best_k, best = k, score
    return lo + best_k * resolution


def extract_wav(src: Path, out: Path, start: float, duration: float, sr: int = 16000) -> Path:
    """Mono 16-bit PCM WAV for speech models."""
    out.parent.mkdir(parents=True, exist_ok=True)
    pre, post = _seek(max(start, 0.0), duration)
    audio = ["-vn", "-ac", "1", "-ar", str(sr), "-c:a", "pcm_s16le"]
    run([*FFMPEG, *pre, "-i", str(src), *post, *audio, str(out)])
    return out


def extract_frame(src: Path, at: float, out: Path) -> Path:
    out.parent.mkdir(parents=True, exist_ok=True)
    run([*FFMPEG, "-ss", f"{at:.3f}", "-i", str(src), "-frames:v", "1", str(out)])
    return out


@dataclass(frozen=True)
class SegmentPart:
    path: Path
    inpoint: float  # seconds inside the file
    outpoint: float


def concat_copy(parts: list[SegmentPart], out: Path) -> Path:
    """Join parts of .ts segments into one file without re-encoding (concat demuxer)."""
    out.parent.mkdir(parents=True, exist_ok=True)
    listing = out.with_suffix(".concat.txt")
    lines = []
    for p in parts:
        lines += [
            f"file '{p.path.resolve()}'",
            f"inpoint {p.inpoint:.3f}",
            f"outpoint {p.outpoint:.3f}",
        ]
    listing.write_text("\n".join(lines) + "\n", encoding="utf-8")
    streams = ["-map", "0:v?", "-map", "0:a?", "-c", "copy"]
    try:
        run([*FFMPEG, "-f", "concat", "-safe", "0", "-i", str(listing), *streams, str(out)])
    finally:
        listing.unlink(missing_ok=True)
    return out
