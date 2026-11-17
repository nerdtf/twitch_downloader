# DECISIONS — why things are the way they are

Newest first. Format: date — decision — why. "Proposed" = awaiting owner confirmation.

## 2026-11-18
- **Speed over future-proofing** until after GTA VI launch week (launch 2026-11-19).
- **Live posting** → stage-1 runs during the stream; first deliverable is a Telegram alert + raw cut.
- **No streamer_value weighting** (owner): views are unpredictable; ranking uses the moment only.
- **LLM judge: Kimi or DeepSeek** via OpenAI-compatible API; ru/uk transcripts may be sent.
- **Recorder on the Mac now, Pi later (M7).** Recorder code avoids Mac-only deps; `st_birthtime`
  is used when present, filename (UTC, 1 s) otherwise.
- **Live detection without API keys:** just try to open the stream with streamlink (offline exits in
  ~2 s). Helix only adds metadata/viewer clips.
- **Time anchoring:** each segment's start = file birth time (sub-second, APFS). HLS latency adds a
  roughly constant offset that the per-streamer chat-lag calibration absorbs. `--twitch-disable-ads`
  is on; ad breaks are parsed from streamlink's log into `events` so the gap is known.
- **Retention:** per-streamer TTL (default 12 h — with live posting, older raw video is rarely used)
  plus a disk guard deleting oldest *unpinned* segments when free space < `min_free_gb` (25).
  `clipper/record/ttl.py` is the only code that deletes recordings; refuses paths outside data_dir.
- **Chat is recorded continuously** (not only while live) — it's tiny and catches pre-stream hype.
- **Twitch portrait renditions exist** (e.g. `1280p60_portrait`): default quality list prefers
  landscape 1080p60; evaluate portrait feeds for M4 where a streamer offers them.

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
