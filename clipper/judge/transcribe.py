"""Local speech-to-text on Apple Silicon with word timestamps, cached on disk.

Default: Whisper large-v3-turbo (mlx-whisper). Parakeet TDT v3 was tried 2026-11-18 (faster, better
on English game dialogue) but the owner found its Russian worse -> back to Whisper. Set
CLIPPER_ASR=parakeet to use it. Next to try: GigaAM v3 (Russian-specific; install was blocked).
"""

import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path

from clipper.config import REPO_ROOT
from clipper.media.ffmpeg import extract_wav

log = logging.getLogger(__name__)
PARAKEET = "mlx-community/parakeet-tdt-0.6b-v3"
WHISPER = "mlx-community/whisper-large-v3-turbo"
MODEL = PARAKEET if os.environ.get("CLIPPER_ASR") == "parakeet" else WHISPER
_PARAKEET = None
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
            raw = _parakeet(wav) if "parakeet" in model else None
            if not raw or not raw["words"]:
                raw = _whisper(wav, language, WHISPER if "parakeet" in model else model)
        finally:
            wav.unlink(missing_ok=True)
        CACHE.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return Transcript(raw["text"], [Word(**w) for w in raw["words"]], raw["language"])
