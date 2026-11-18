# PLAN — milestones and status

Status: ⬜ todo · 🟨 works, needs owner input/tuning · ✅ done. Branch: `m1-recorder` (all of
M1–M6 so far; split into PRs per milestone when merging into `mac-setup`).

**Priority:** GTA VI launched 2026-11-19 (streams started 2026-11-18); owner posts *live*.
Fast and working beats future-proof.

---

## M0 — Repo hygiene ✅
Secrets in `.env`; `.gitignore`; Claude settings; uv + ruff + pytest; legacy scripts removed (they
remain on `ubuntu_setup` for the old Pi); one package `clipper/` with one topic per subpackage.

## M1 — Recorder v2 ✅
streamlink→ffmpeg 5-min `.ts` segments with per-segment UTC epoch; Helix live check every 15 s +
keyless fallback; title/game/started_at per recording; anonymous IRC chat with tags, kept only
while recording; TTL 6 h + disk guard; stall watchdog; crash recovery; launchd templates.
Verified live 2026-11-18 on mokrivskyi / zloyn / evelone2004 (GTA VI).
**Owner:** install as service with `deploy/install_recorder.sh` (now it runs inside a Claude session).

## M2 — Stage-1 scorer + eval harness 🟨
Done: per-second chat (rate, laugh/hype lexicon, copypasta, notices), audio loudness, viewer clips;
robust z, agreement, penalties, NMS. VOD audio + viewer-clip download. Eval: p@10, recall@40,
chance level, hold-out VOD; weak gold from viewer clips (vod_offset = clip END, verified).
Findings: on VODs only audio is available (chat replay blocked, see DECISIONS) and audio loudness
alone is ≈ chance. Chat is captured live and is the main signal now.
Next: evaluate on live recordings (rec:*) with viewer clips as weak gold; per-streamer chat-lag
calibration; laughter/shout detector (PANNs) if audio stays weak; lexicon mining from real chat.
**Owner:** labels (approve/reject in `data/clips/`), optional `data/gold/<source>.csv`.

## M3 — Stage-2: Whisper + LLM judge + gates 🟨
Done: mlx-whisper large-v3-turbo (~10× realtime) with word timestamps + cache; LLM judge
(OpenAI-compatible, strict JSON, retry, cache, USD cap); gates (banned topic, streamer not main,
dead air, clarity, overlap, program window); final score. Falls back to "unjudged" without LLM.
**Blocked:** `LLM_API_KEY` returns 401 (owner: run `uv run python -m clipper.judge.llm --check`) on api.moonshot.ai and api.moonshot.cn. Owner: check key /
set `LLM_BASE_URL` (e.g. OpenRouter or DeepSeek endpoint) and `LLM_MODEL`.

## M4 — Render 🟨
Done: 1080×1920 `fit_blur` (default, safe for any scene) and `split` (facecam top) layouts;
sponsor blur rects; word-highlight subtitles + hook drawn with Pillow (Homebrew ffmpeg has no
libass/drawtext); VideoToolbox H.264; caption.txt + meta.json.
Layout `auto` (default): split (webcam top, gameplay below, crop slides off the webcam) when an
OpenCV face check finds a face in the configured webcam box, else fit_blur (IRL, scene changes).
Webcam boxes re-measured from live GTA VI frames 2026-11-18. Past-broadcast (practice) clips go to
`data/clips/practice/`, never to the review queue.
**Owner:** confirm evelone's webcam (left cam in the marathon layout), fill hashtags in
`config/programs.yaml`. Later: better crops/reframing (ideas from open clip tools).

## M5 — Review queue 🟨 (local web app; Telegram is low priority)
`uv run python -m clipper.review serve` → http://127.0.0.1:8765: approve/reject with a required
comment, chat of the moment synced to the video, signals behind the score, caption copy,
fullscreen. Moving folders by hand + `clipper.review sync` still works.

## M6 — Tracker + learning loop 🟨
`clipper.track posted|views|submitted|due` (manual entry), `clipper.learn export|train`
(logistic regression for P(approve) once ≥ 300 labels).

## M7 — Raspberry Pi ⬜
Docs + systemd unit ready (`docs/PI_MIGRATION.md`, `deploy/pi/`). Needs the Pi.
