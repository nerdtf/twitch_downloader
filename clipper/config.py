"""Load and validate config/streamers.yaml."""

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

LOGIN_RE = re.compile(r"^[a-z0-9_]{3,25}$")
REPO_ROOT = Path(__file__).resolve().parent.parent


@dataclass(frozen=True)
class Streamer:
    login: str
    quality: str
    ttl_hours: float


@dataclass(frozen=True)
class RecorderSettings:
    data_dir: Path
    db_path: Path
    segment_seconds: int
    poll_seconds: int
    helix_poll_seconds: int
    min_free_gb: float
    stall_seconds: int
    ttl_check_seconds: int


@dataclass(frozen=True)
class Config:
    recorder: RecorderSettings
    streamers: tuple[Streamer, ...]  # enabled only


class ConfigError(ValueError):
    pass


def _path(value, base: Path) -> Path:
    p = Path(value).expanduser()
    return p if p.is_absolute() else (base / p)


def load_config(path: str | Path, base: Path = REPO_ROOT) -> Config:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    defaults = dict(raw.get("defaults") or {})
    rec = dict(raw.get("recorder") or {})
    # Per-machine overrides (.env / environment): the Pi and the Mac have different disks.
    from clipper.env import get as env

    if v := env("CLIPPER_TTL_HOURS"):
        defaults["ttl_hours"] = float(v)
        for e in raw.get("streamers") or []:
            if isinstance(e, dict):
                e.pop("ttl_hours", None)
    if v := env("CLIPPER_MIN_FREE_GB"):
        rec["min_free_gb"] = float(v)

    settings = RecorderSettings(
        data_dir=_path(rec.get("data_dir", "data/recordings"), base),
        db_path=_path(rec.get("db_path", "data/clipper.db"), base),
        segment_seconds=int(rec.get("segment_seconds", 300)),
        poll_seconds=int(rec.get("poll_seconds", 60)),
        helix_poll_seconds=int(rec.get("helix_poll_seconds", 15)),
        min_free_gb=float(rec.get("min_free_gb", 25)),
        stall_seconds=int(rec.get("stall_seconds", 90)),
        ttl_check_seconds=int(rec.get("ttl_check_seconds", 600)),
    )
    if settings.segment_seconds < 10:
        raise ConfigError("recorder.segment_seconds must be >= 10")

    streamers: list[Streamer] = []
    seen: set[str] = set()
    for i, entry in enumerate(raw.get("streamers") or []):
        if isinstance(entry, str):
            entry = {"login": entry}
        login = str(entry.get("login", "")).strip().lower()
        if not LOGIN_RE.match(login):
            raise ConfigError(f"streamers[{i}]: invalid login {login!r}")
        if login in seen:
            raise ConfigError(f"streamers[{i}]: duplicate login {login!r}")
        seen.add(login)
        if not entry.get("enabled", True):
            continue
        streamers.append(
            Streamer(
                login=login,
                quality=str(entry.get("quality", defaults.get("quality", "best"))),
                ttl_hours=float(entry.get("ttl_hours", defaults.get("ttl_hours", 24))),
            )
        )
    return Config(recorder=settings, streamers=tuple(streamers))
