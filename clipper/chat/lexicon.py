"""Chat lexicon (config/lexicon.yaml): classifies a message as laugh / hype / ignored."""

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from clipper.config import REPO_ROOT

DEFAULT_PATH = REPO_ROOT / "config/lexicon.yaml"
_TOKEN_RE = re.compile(r"\S+")


@dataclass(frozen=True)
class _Category:
    regex: re.Pattern | None
    emotes: frozenset[str]
    emoji: tuple[str, ...]

    def matches(self, text: str, tokens: set[str]) -> bool:
        if self.emotes & tokens:
            return True
        if any(e in text for e in self.emoji):
            return True
        return bool(self.regex and self.regex.search(text))


def _category(raw: dict) -> _Category:
    patterns = raw.get("regex") or []
    regex = re.compile("|".join(f"(?:{p})" for p in patterns), re.IGNORECASE) if patterns else None
    return _Category(regex, frozenset(raw.get("emotes") or []), tuple(raw.get("emoji") or []))


class Lexicon:
    def __init__(self, raw: dict):
        self.laugh = _category(raw.get("laugh") or {})
        self.hype = _category(raw.get("hype") or {})
        ign = raw.get("ignore") or {}
        self.ignore_users = frozenset(u.lower() for u in ign.get("users") or [])
        self.command_prefix = ign.get("command_prefix") or None
        pats = ign.get("regex") or []
        self.ignore_regex = (
            re.compile("|".join(f"(?:{x})" for x in pats), re.IGNORECASE) if pats else None
        )

    @classmethod
    def load(cls, path: Path = DEFAULT_PATH) -> "Lexicon":
        return cls(yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {})

    def is_ignored(self, login: str | None, text: str | None) -> bool:
        if login and login.lower() in self.ignore_users:
            return True
        if not text:
            return False
        if self.command_prefix and text.lstrip().startswith(self.command_prefix):
            return True
        return bool(self.ignore_regex and self.ignore_regex.search(text))

    def classify(self, text: str) -> tuple[bool, bool]:
        """(is_laugh, is_hype)."""
        tokens = set(_TOKEN_RE.findall(text))
        return self.laugh.matches(text, tokens), self.hype.matches(text, tokens)


def normalize(text: str) -> str:
    """For copypasta detection: lowercase, collapse whitespace and character repeats."""
    t = re.sub(r"\s+", " ", text.lower()).strip()
    return re.sub(r"(.)\1{2,}", r"\1\1", t)
