"""Local speech-to-text with word timestamps, cached on disk.

Default (2026-11-19): GigaAM v3 RNNT with punctuation (Sber, MIT), the ONNX export from sherpa-onnx:
plain model files, no remote code, CPU-only (~40x real time on the Mac, also runs on the Pi).
Better Russian slang/swearing than Whisper and adds punctuation; drops English speech.
CLIPPER_ASR=whisper -> Whisper large-v3-turbo (mlx-whisper, Mac only); =parakeet -> Parakeet TDT v3
(owner found its Russian worse). Whisper is also the fallback when a backend returns no words.
"""

import hashlib
import json
import logging
import os
import re
import sys
import tarfile
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path

from clipper.config import REPO_ROOT
from clipper.media.ffmpeg import extract_wav

log = logging.getLogger(__name__)
PARAKEET = "mlx-community/parakeet-tdt-0.6b-v3"
WHISPER = "mlx-community/whisper-large-v3-turbo"
GIGAAM = "sherpa-onnx-nemo-transducer-punct-giga-am-v3-russian-2025-12-16"
GIGAAM_URL = f"https://github.com/k2-fsa/sherpa-onnx/releases/download/asr-models/{GIGAAM}.tar.bz2"
MODELS = REPO_ROOT / "data/models"
MODEL = {"parakeet": PARAKEET, "whisper": WHISPER}.get(os.environ.get("CLIPPER_ASR", ""), GIGAAM)
GIGAAM_CHUNK = 25.0  # seconds; long windows are cut at the quietest moment near each boundary
_PARAKEET = None
_GIGAAM = None
CACHE = REPO_ROOT / "data/cache/transcripts"
# Phrases Whisper invents on music/silence (it learned them from subtitle credits on YouTube).
HALLUCINATIONS = re.compile(
    r"субтитр|DimaTorzok|редактор|корректор|продолжение следует|спасибо за просмотр|"
    r"подпишись|ставьте лайк|amara\.org|thanks for watching",
    re.IGNORECASE,
)


def keep_segment(seg: dict) -> bool:
    """Drop segments that are likely not real speech."""
    if HALLUCINATIONS.search(seg.get("text", "")):
        return False
    if seg.get("no_speech_prob", 0) > 0.6 and seg.get("avg_logprob", 0) < -0.7:
        return False
    return seg.get("compression_ratio", 0) <= 2.6  # repetitive loops ("да да да да ...")


@dataclass(frozen=True)
class Word:
    text: str
    start: float  # seconds relative to the transcribed window start
    end: float
    prob: float


@dataclass
class Transcript:
    text: str
    words: list[Word]
    language: str

    def words_per_second(self, duration: float) -> float:
        return len(self.words) / duration if duration > 0 else 0.0

    def text_between(self, lo: float, hi: float) -> str:
        """Words spoken inside [lo, hi] seconds of the window (what the clip itself contains)."""
        return " ".join(w.text.strip() for w in self.words if lo - 0.3 <= w.start < hi).strip()

    def timed_text(self, offset: float = 0.0, every: float = 5.0) -> str:
        """Text with [mm:ss] markers every few seconds — what the LLM judge reads."""
        out, last = [], -every
        for w in self.words:
            t = w.start + offset
            if t - last >= every:
                out.append(f"\n[{int(t // 60):02d}:{t % 60:04.1f}]")
                last = t
            out.append(w.text.strip())
        return " ".join(out).strip()


def _parakeet(wav: Path) -> dict:
    global _PARAKEET
    if _PARAKEET is None:
        from parakeet_mlx import from_pretrained  # heavy import, only when needed

        _PARAKEET = from_pretrained(PARAKEET)
    # chunking also fixes empty output on some mixed-language windows
    r = _PARAKEET.transcribe(str(wav), chunk_duration=30.0, overlap_duration=5.0)
    words: list[dict] = []
    for sent in r.sentences:
        for tok in sent.tokens:
            piece = tok.text
            if not piece.strip() and not words:
                continue
            if piece.startswith(" ") or not words:  # SentencePiece: leading space = new word
                if piece.strip():
                    words.append({"text": piece.strip(), "start": float(tok.start),
                                  "end": float(tok.end), "prob": 1.0})  # fmt: skip
            else:
                words[-1]["text"] += piece
                words[-1]["end"] = float(tok.end)
    words = [w for w in words if w["text"]]
    return {"text": r.text.strip(), "language": "auto", "words": words}


def _gigaam_dir() -> Path:
    d = MODELS / GIGAAM
    if not (d / "tokens.txt").exists():
        MODELS.mkdir(parents=True, exist_ok=True)
        tmp = MODELS / f"{GIGAAM}.tar.bz2"
        log.info("downloading GigaAM v3 (~170 MB) from %s", GIGAAM_URL)
        urllib.request.urlretrieve(GIGAAM_URL, tmp)
        with tarfile.open(tmp) as tf:
            tf.extractall(MODELS, filter="data")
        tmp.unlink()
    return d


def _cut_points(a, sr: int, chunk: float) -> list[int]:
    """Chunk starts; each cut sits at the quietest 0.1 s in the last 5 s before `chunk`."""
    import numpy as np

    cuts, i, n, hop = [0], 0, len(a), sr // 10
    while n - i > chunk * sr:
        lo, hi = i + int((chunk - 5) * sr), i + int(chunk * sr)
        frames = a[lo:hi][: (hi - lo) // hop * hop].reshape(-1, hop)
        i = lo + int(np.argmin((frames**2).mean(1))) * hop + hop // 2
        cuts.append(i)
    return cuts


def _gigaam(wav: Path) -> dict:
    global _GIGAAM
    import numpy as np

    if _GIGAAM is None:
        import sherpa_onnx

        d = _gigaam_dir()
        _GIGAAM = sherpa_onnx.OfflineRecognizer.from_transducer(
            encoder=str(d / "encoder.int8.onnx"),
            decoder=str(d / "decoder.onnx"),
            joiner=str(d / "joiner.onnx"),
            tokens=str(d / "tokens.txt"),
            model_type="nemo_transducer",
            num_threads=min(4, os.cpu_count() or 1),
        )
    with wave.open(str(wav)) as w:
        sr = w.getframerate()
        a = np.frombuffer(w.readframes(w.getnframes()), np.int16).astype(np.float32) / 32768
    cuts = _cut_points(a, sr, GIGAAM_CHUNK)
    words: list[dict] = []
    texts = []
    for k, c0 in enumerate(cuts):
        c1 = cuts[k + 1] if k + 1 < len(cuts) else len(a)
        st = _GIGAAM.create_stream()
        st.accept_waveform(sr, a[c0:c1])
        _GIGAAM.decode_stream(st)
        r, off = st.result, c0 / sr
        texts.append(r.text.strip())
        first, new_word = len(words), True
        for tok, t in zip(r.tokens, r.timestamps, strict=False):
            new_word |= tok.startswith(" ")  # SentencePiece: a space (alone or leading) = new word
            piece = tok.strip()
            if not piece or piece in "—–-":
                continue
            if new_word or len(words) == first:
                words.append({"text": piece, "start": off + t, "end": off + t + 0.3, "prob": 1.0})
            else:
                words[-1]["text"] += piece
                words[-1]["end"] = off + t + 0.15
            new_word = False
        for w0, w1 in zip(words[first:], words[first + 1 :], strict=False):
            w0["end"] = min(max(w0["end"], w0["start"] + 0.1), w1["start"])
    return {"text": " ".join(t for t in texts if t), "language": "ru", "words": words}


def _whisper(wav: Path, language: str, model: str) -> dict:
    import mlx_whisper  # heavy import, only when needed

    r = mlx_whisper.transcribe(
        str(wav),
        path_or_hf_repo=model,
        word_timestamps=True,
        language=language,
        condition_on_previous_text=False,
    )
    kept = [s for s in r.get("segments", []) if keep_segment(s)]
    return {
        "text": " ".join(s.get("text", "").strip() for s in kept),
        "language": r.get("language", language),
        "words": [
            {
                "text": w["word"],
                "start": float(w["start"]),
                "end": float(w["end"]),
                "prob": float(w.get("probability", 0)),
            }
            for s in kept
            for w in s.get("words", [])
        ],
    }


def transcribe_window(
    audio_src: Path, start: float, duration: float, language: str = "ru", model: str = MODEL
) -> Transcript:
    """Transcribe [start, start+duration] seconds of `audio_src` (any file ffmpeg reads)."""
    key = hashlib.sha1(
        f"{Path(audio_src).resolve()}|{start:.2f}|{duration:.2f}|{language}|{model}".encode()
    )
    cache = CACHE / f"{key.hexdigest()}.json"
    if cache.exists():
        raw = json.loads(cache.read_text(encoding="utf-8"))
    else:
        wav = extract_wav(audio_src, cache.with_suffix(".wav"), start, duration)
        try:
            raw = None
            if "giga-am" in model:
                raw = _gigaam(wav)
            elif "parakeet" in model:
                raw = _parakeet(wav)
            if (not raw or not raw["words"]) and (raw is None or sys.platform == "darwin"):
                raw = _whisper(wav, language, model if "whisper" in model else WHISPER)
        finally:
            wav.unlink(missing_ok=True)
        CACHE.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return Transcript(raw["text"], [Word(**w) for w in raw["words"]], raw["language"])
