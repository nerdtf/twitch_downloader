# DECISIONS — why things are the way they are

Newest first. Format: date — decision — why. "Proposed" = awaiting owner confirmation.

## 2026-11-19 (second review batch: ~140 labels)
- **Skip categories (`skip_games`)**: evelone's night stream ran SpongeBob under "I'm Only
  Sleeping" (02:30-08:43 UTC) and every clip from it was rejected "streamer not main". Moments are
  dropped when the category *at the moment* (from `game_change` events) is in the list. The 102
  pending clips from that window were moved to `data/clips/skipped/` (deleting was blocked for the
  agent; owner can delete the folder) and marked `dropped / skip_game`.
- **Clip length follows the chat**: default 25 s before / 12 s after; the end grows while chat
  laugh+hype stays above mean + 0.35×(reaction peak − mean), the start moves earlier when chat was
  already laughing before the moment; caps 50 s before / 30 s after. On the last 30 h: 35-45 s.
- **Several webcam boxes per streamer** (`facecam` may be a list; auto picks the box with the
  face). zloyn moved his cam lower on 2026-11-19 (box y 384-690), so his clips fell back to
  fit_blur ("no face 0/7"). Now 7/8 frames hit.
- **evelone co-streams with two others**: "streamer not main" cannot be told from chat; needs the
  LLM/transcript check (who is talking) — next step when the judge is turned on.

## 2026-11-19 (Pi migration)
- **The Pi records, the Mac clips; linked by a pull mirror, not a network share.** Pi runs
  `clipper.record` + `clipper.feed` (read-only HTTP on the LAN, shared token); the Mac runs
  `clipper.mirror` + `clipper.pipeline live`. SQLite over SMB/NFS is fragile and the Mac pipeline
  writes its own rows, so each machine keeps its own DB. Segments are append-only `.ts`, so the
  open one is copied incrementally with HTTP Range (Mac lags the Pi by ~10 s).
- **DB and clips are not migrated.** The Pi starts with an empty DB (it only needs the recorder's
  rows); labels, candidates and clips stay on the Mac, where review and scoring happen.
- **After a gap only the last hour is copied** (`--backfill-hours 1`): old moments are stale for
  live posting; the Pi keeps its own TTL window anyway.
- **Per-machine disk settings via .env** (`CLIPPER_TTL_HOURS`, `CLIPPER_MIN_FREE_GB`), so both
  machines run the same `config/streamers.yaml` from git. mlx-whisper / parakeet-mlx are
  macOS-only dependencies, so `uv sync` works on the Pi.
- **Mac disk guard 25 -> 12 GB** (the Mac had ~30 GB free and was deleting live segments).
- **Review page: one clip at a time**, → / ← then a numbered reason (2 keys per clip), swipe or
  trackpad swipe, autoplay at the remembered speed, undo; 100 clips per load.

## 2026-11-19 (69 owner labels: 22 approved / 47 rejected)
- **Scoring fitted to labels.** Per-feature AUC: viewer clips made in the 2 min after a moment 0.76,
  laughter over 30 s 0.69, laugh share 0.66, message rate 0.52 (useless), loudness 0.55. New stage-1:
  clips 0.45 (count in next 90 s vs the channel's usual rate — the old signal capped at 2 clips),
  laugh 0.40 over 20 s, hype 0.10, audio 0.05, message rate 0. Labels AUC 0.64 -> 0.71; at the 0.35
  threshold it passes 77% of approved vs 40% of rejected (was 82% / 60%). An absolute clip count
  scored 0.77 but favours big channels (zloyn), so the per-channel version was chosen.
- **Chat speed-up** (owner): msgs/s in the next 20 s vs the previous 30 s. As a scoring weight it
  lowered label AUC (0.72 -> 0.65-0.69, it is high for approved and rejected alike); it now counts
  as "chat reacted" in the reaction gate (neutral on labels, rescues hype moments without laughs).
- **Viewer-clip count centred** on the moment (clips made 15 s before to 75 s after): a pure
  look-ahead window pulled clip-driven peaks up to ~40 s early. Labels 0.72 -> 0.69 (noise level;
  the labelled moments themselves were placed by the drifting version).
- **Viewer clips demoted to a tiebreaker** (owner doubts them): weights laugh 0.62, hype 0.15,
  clips 0.15, audio 0.08. Labels AUC 0.69 -> 0.73; at 0.35 it passes 82% approved / 47% rejected.
- **GigaAM not tested**: its own model CDN has an expired TLS certificate (not bypassed), and
  loading it from Hugging Face needs `trust_remote_code`, which this environment blocks.
- Hype lexicon: «цц» (ww in the Russian layout), «пизда», «нахуй» (owner).
- **Live waits ~100 s after a moment** (was 45 s) so viewer clips exist; clips polled every 50 s.
- **Subtitles back to Whisper turbo** — owner: Parakeet's Russian is worse (CLIPPER_ASR=parakeet).
- Top rejection reasons: not funny 15, no context 8, boring 5, streamer not main 4 — the last two
  need the LLM/transcript, not chat.

## 2026-11-18 (night)
- **Small chats scored fairly** (owner: zloyn is funny but got no moments). Laugh/hype z-scores now
  use the channel's own Poisson noise (sqrt of its recent mean) instead of a fixed floor of 1, so
  3 laughs in a quiet chat count like 40 in evelone's. zloyn candidates/h 5.8 -> 7.3; plus a
  per-streamer threshold (`min_score_per_streamer`, zloyn 0.25) -> ~8.6/h vs 10-12 for others.
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

## 2026-11-19 — after ~230 owner labels
- **Chat was stored twice 00:39–11:15 on 2026-11-19**: two recorders ran at once (also two
  recordings of the same stream → same moment clipped twice). The recorder now takes an exclusive
  lock (`data/recorder.lock`); the review app de-duplicates chat; the live overlap check spans all
  recordings of a streamer and also rejects overlapping clip windows.
- **Stage-2 ignored the chat-based clip bounds** (every clip was still 25+12 = 37 s without the LLM).
  Now the stage-1 window is used; the LLM's best_start/end still win when it runs.
- **"What was said" shows only words inside the clip** (it showed the judge's 65 s context).
- **Weak-chat penalty**: <20 messages or <5% laughing in the 3–23 s reaction window → −0.3. On 227
  labels this held 3 of 59 approvals and 41 of 168 rejects.
- **Subtitles/transcripts: GigaAM v3** (RNNT + punctuation, MIT) via the sherpa-onnx ONNX export
  (GitHub release; no `trust_remote_code`, Sber's CDN cert is still expired). On the 6 comparison
  windows it got slang/swearing right where Whisper turbo missed it, adds punctuation, and no
  longer invents Russian for English game dialogue (it drops English). ~1.9 s per 65 s window on
  Mac CPU. Whisper turbo: `CLIPPER_ASR=whisper`, and the fallback when GigaAM returns no words.
