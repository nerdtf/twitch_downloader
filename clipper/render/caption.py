"""caption.txt for posting: title, required hashtags, Twitch line (config/programs.yaml)."""

from pathlib import Path

import yaml

from clipper.config import REPO_ROOT

DEFAULT_PATH = REPO_ROOT / "config/programs.yaml"


def program_for(login: str, path: Path = DEFAULT_PATH) -> dict:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return {**(raw.get("defaults") or {}), **((raw.get("streamers") or {}).get(login) or {})}


def build_caption(login: str, title: str, program: dict | None = None) -> str:
    program = program or program_for(login)
    tags = " ".join(program.get("required_hashtags") or [])
    line = (program.get("caption_line") or "").format(login=login)
    return "\n".join(x for x in (title.strip(), tags, line) if x) + "\n"
