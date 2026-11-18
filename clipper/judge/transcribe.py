"""Local speech-to-text (mlx-whisper on Apple Silicon) with word timestamps, cached on disk."""

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from clipper.config import REPO_ROOT
from clipper.media.ffmpeg import extract_wav

log = logging.getLogger(__name__)
MODEL = "mlx-community/whisper-large-v3-turbo"
CACHE = REPO_ROOT / "data/cache/transcripts"


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
        import mlx_whisper  # heavy import, only when needed

        wav = extract_wav(audio_src, cache.with_suffix(".wav"), start, duration)
        try:
            r = mlx_whisper.transcribe(
                str(wav),
                path_or_hf_repo=model,
                word_timestamps=True,
                language=language,
                condition_on_previous_text=False,
            )
        finally:
            wav.unlink(missing_ok=True)
        raw = {
            "text": r.get("text", "").strip(),
            "language": r.get("language", language),
            "words": [
                {
                    "text": w["word"],
                    "start": float(w["start"]),
                    "end": float(w["end"]),
                    "prob": float(w.get("probability", 0)),
                }
                for s in r.get("segments", [])
                for w in s.get("words", [])
            ],
        }
        CACHE.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(raw, ensure_ascii=False), encoding="utf-8")
    return Transcript(raw["text"], [Word(**w) for w in raw["words"]], raw["language"])
