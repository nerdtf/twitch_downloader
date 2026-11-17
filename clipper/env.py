"""Minimal .env loader (no third-party dependency).

Reads KEY=VALUE lines from the project's .env into os.environ without
overriding variables that are already set in the real environment.
"""

import os
from pathlib import Path

ENV_FILE = Path(__file__).resolve().parent.parent / ".env"
_loaded = False


def load_env(path=ENV_FILE):
    global _loaded
    if _loaded:
        return
    _loaded = True
    if not Path(path).is_file():
        return
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key.strip(), value)


def get(key, default=""):
    load_env()
    return os.environ.get(key, default)
