# DECISIONS — why things are the way they are

Newest first. Format: date — decision — why. "Proposed" = awaiting owner confirmation.

## 2026-11-18 (night)
- **Speech-to-text: Parakeet TDT 0.6B v3 (parakeet-mlx)** instead of Whisper large-v3-turbo. Owner:
  subtitles miss words. On 6 live windows Parakeet kept slang/swearing Whisper dropped, did not
  "translate" English game dialogue into invented Russian, and is ~25x faster than Whisper large
  (6 s vs 160 s for 6 windows). Whisper turbo stays as fallback. GigaAM (best for Russian) not
  tested: installing from GitHub was blocked here; worth a try later. Paid API possible later.
- **Subtitles** under the webcam (split) / under the frame (fit_blur), font 92 -> 72.
- **Chat gaps were the Mac sleeping** (pmset: "Clamshell Sleep" 18:24 with the lid closed) — video
  stopped too. While connected, no messages are lost (independent listener: 896/896, 110/110).
  IRC now: 20 s connect timeout (one reconnect hung 16 min), backoff reset after a good session,
  PING probe after 60 s of silence (dead socket found in ~75 s instead of 6 min).

## 2026-11-18 (evening, after the first owner review: ~80% of clips rejected)
- **Chat decides what is funny.** Laugh words get half the weight; hype/W, message rate and viewer
  clips 0.15 each; loudness 0.05 (tiebreaker). Moments where chat does not laugh are multiplied
  by 0.6 (hype only) or 0.35 (no reaction). On 14 owner labels: AUC 0.69 -> 0.81 (small sample).
- **No candidates in the first 5 min of a recording** — "stream started" hype was rejected.
- **LLM judge off by default** (`--llm` to enable): owner wants to see chat-picked clips first.
- **No daily cap, no render threshold** beyond stage-1 `min_score`: owner reviews regularly.
- **Review queue is live-only**; practice clips from past broadcasts go to `data/clips/practice/`.
- **Layout `auto`**: split (webcam on top) when a face is found in the webcam box, else fit_blur.

## 2026-11-18 (overnight, autonomous session)
- **Helix hybrid** (owner asked to use Helix): live check every 15 s + keyless open-attempt fallback.
- **Chat only while recording** (owner): IRC stays connected, offline chat discarded.
- **Code layout**: one package, one topic per subpackage (`record`, `chat`, `twitch`, `media`,
  `scoring`, `judge`, `render`, `pipeline`, `review`, `track`, `learn`, `eval`). Legacy root scripts
  deleted (still on `ubuntu_setup`). No `# fmt: skip`; lint allows 120-char literals, format at 100.
- **VOD chat replay not used**: fetching it needs Twitch's internal web GQL API with the web
  player's client id; the agent's safety check blocked it as unauthorized access. Owner decision
  if wanted (chat_downloader/yt-dlp do the same thing). Consequence: practice VODs have audio +
  viewer clips only; chat features are evaluated on our own live recordings.
- **Twitch `vod_offset` = clip END**, not start (docs say start). Verified by aligning 6 clips'
  audio against VOD audio: content starts at `vod_offset − duration` (errors 0.1–2.4 dB).
- **Weak gold** = viewer clips merged into moments; "good" = top 20% by summed views or ≥3 clips.
  Viewer-clip feature is disabled when evaluating against it (leakage).
- **Audio loudness alone ≈ chance** on VODs (p@10 0.25 vs chance 0.22, 4 VODs). Not tuned further:
  chat is the main signal; a laughter/shout model (PANNs) is the next audio step if needed.
- **VOD downloads are audio-only** (~80 MB/h); video fetched per clip window, exact start found
  by cross-correlating 20 ms loudness envelopes with the VOD audio.
- **Whisper**: mlx-whisper large-v3-turbo, ~10× realtime after warm-up (9 s per 90 s). Used for
  candidates only (whole-stream transcription would be ~18 min per 3 h).
- **Subtitles via Pillow PNGs**: Homebrew ffmpeg 9 here has no libass/drawtext; installing another
  ffmpeg needs `brew` (owner prompt). PNG frames + ffconcat = one overlay input, exact timing.
  Subtitles are transcribed from the final cut itself so they line up exactly.
- **Default layout fit_blur**: frames show layouts change per scene (buster desk vs IRL, evelone
  game show), so a fixed facecam crop is unsafe as default. Sponsor overlays seen: Winline
  (buster), PARI (mokrivskyi) — both betting brands; blur rects in config/layouts.yaml.
- **LLM judge without a working key** → pipeline runs "unjudged" (stage-1 ranking, dead-air gate)
  so clips still reach review. Budget cap $2 total by default (`--budget`).
- **Review via folders** (Telegram low priority, owner): pending → approved/rejected + note.txt.

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
  roughly constant offset that the per-streamer chat-lag calibration absorbs. streamlink 8 skips ads
  by default (`--twitch-disable-ads` is deprecated); ad breaks are parsed from its log into `events`.
  Smoke test: segment N+1 birth − (N start + duration) ≈ 0.02–0.3 s, except the first segment of a
  recording, whose birth time is ~0.9 s late (ffmpeg probes input first). Fix in M2's time map:
  anchor segment 1 as segment 2 start − segment 1 duration.
- **Twitch serves fMP4 HLS now**; ffmpeg remuxes to MPEG-TS segments with `-c copy` (verified:
  1080p60 H.264 + AAC, zero decode errors). Measured ~3.2–3.4 GB/hour/stream at 1080p60.
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
