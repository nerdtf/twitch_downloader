# DECISIONS — why things are the way they are

Newest first. Format: date — decision — why. "Proposed" = awaiting owner confirmation.

## 2026-11-17
- **Base branch `mac-setup` from `ubuntu_setup`.** It is the newest code, what ran on the Pi, and the
  only branch containing `chatProcessing/chatProcessor.py`. `docker-version` was a Windows-only attempt.
- **Secrets in `.env`, no history rewrite.** Old Telegram token in history is inactive; owner declined
  cleanup. `env.py` is a tiny loader (no dependency) until the uv project lands.
- **Commit format:** owner as sole author, no attribution lines (CLAUDE.md + `.claude/settings.json`).

### Proposed (pending owner answers)
- **SQLite (WAL) for chat.** At Twitch chat rates (peaks ~50 msg/s) with batched inserts it is
  far below SQLite limits, and time-range queries for scoring are trivial. One DB, one writer per process.
- **Keep:** Helix calls + retry session (`twitch.py`), app-token caching (`utils.py`), Telegram
  sender, quality fallback idea (`watcher.py`), chat laughter regex/keywords (→ `config/lexicon.yaml`,
  after fixing), boundary-padding idea from `videoWorker.py`.
- **Delete (when M1 replaces them):** `ATRHandler.py`, `atr_cmd.py`, `main.py` (local HTTP CLI +
  dead webhook code), `streamConverter.py` (full-stream mp4 conversion not needed; cut from `.ts`),
  `cron_script.py` (hard-coded `id = 2`), `db_connection.py`, `patch_streamlink.sh` (streamlink 2.0 hack),
  `desktop.ini`, `requirements.txt` (→ `pyproject.toml`).
- **Audio laughter model deferred.** Start with ffmpeg loudness; add PANNs (PyTorch/MPS) only if eval
  shows audio recall is the bottleneck. YAMNet = TensorFlow, painful on Apple Silicon.
- **Use Twitch viewer clips as weak gold** for early eval (with the clip feature disabled to avoid leakage).
