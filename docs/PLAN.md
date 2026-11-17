# PLAN — milestones and status

Branching: `mac-setup` is the integration branch (from `ubuntu_setup`). Each milestone gets
`mN-<name>` branched from `mac-setup`. Status: ⬜ todo · 🟨 in progress · ✅ done.

Package layout target: `clipper/` (Python 3.12, uv, ruff, pytest). Config in `config/*.yaml`.
One SQLite DB (`data/clipper.db`, WAL) for streams, segments, chat, candidates, labels.

---

## M0 — Repo hygiene 🟨
- ✅ Secrets out of code → `.env` / `.env.example`; `.gitignore` rewritten (old `*/*` rule hid everything).
- ✅ Claude settings moved to `.claude/settings.json`; commit-format rule in CLAUDE.md.
- ⬜ `pyproject.toml` (uv, py3.12), ruff config, `tests/` with one smoke test, `uv run pytest` green.
- ⬜ Delete dead code once M1 replaces it (see "Delete" in DECISIONS.md).

**Needs from owner:** new Twitch app client id/secret (config.txt is lost) → `.env`.

---

## M1 — Recorder v2 ⬜
**Files**
- `clipper/config.py` + `config/streamers.yaml` (login, quality, program, enabled, TTL override)
- `clipper/twitch_api.py` — Helix: app token, users, streams, clips (port of `twitch.py` + `utils.get_app_access_token`)
- `clipper/record/recorder.py` — per-streamer supervisor: `streamlink --stdout` → `ffmpeg -c copy -f segment`
  (5-min `.ts`, `-segment_list` CSV), restart on failure, stops when Helix says offline
- `clipper/record/segments.py` — naming, per-segment UTC start epoch (re-anchored every segment so
  ad-break gaps don't accumulate drift), segment index rows in SQLite
- `clipper/chat/irc.py` — anonymous IRC (`justinfan…`), IRCv3 tags: user-id, display-name, emotes,
  badges, bits, USERNOTICE (sub/resub/subgift/raid), CLEARCHAT/CLEARMSG; batched inserts
- `clipper/store.py` — schema + migrations (streams, segments, chat_messages, events)
- `clipper/record/ttl.py` — delete segments older than TTL unless pinned by a candidate
- `clipper/record/__main__.py`, `deploy/com.clipper.recorder.plist` (+ `caffeinate -i`)

**Tests** IRC tag parsing on recorded raw lines; segment-epoch math incl. a simulated ad gap;
TTL on a temp dir (pinned files survive); chat→video offset mapping; config validation.

**Acceptance**
- 15-min live run on 2 streamers: no missing segments, segment epochs monotonic, chat captured
  incl. subs/raids, report of GB/hour/stream, CPU and RAM.
- Kill -9 the recorder mid-stream → restarts and continues in a new segment, nothing corrupted.
- Chat/video alignment error measured on one known event (e.g. a raid) < 2 s.
- launchd plist delivered but **not installed** until owner says so.

**Needs from owner:** where the recorder runs (Pi vs Mac — Q1), disk budget + TTL hours, streamer list.

---

## M2 — Stage-1 scorer + eval harness ⬜
Harness first, scorer second.

**Files**
- `clipper/eval/gold.py` — loader for `data/gold/<stream_id>.csv` (start_sec,end_sec,label,note)
- `clipper/eval/__main__.py` — recall of gold-good in top-40, precision@10 per stream-day; held-out day
- `clipper/tools/fetch_vod.py` — download past VOD + chat replay (for dev data before live recordings pile up)
- `clipper/features/chat.py` — rate z-score vs 10-min rolling baseline, laugh/emote density
  (`config/lexicon.yaml`, ru/uk/en), unique-chatter ratio, copypasta + sub/raid/giveaway penalties
- `clipper/features/audio.py` — per-second loudness via ffmpeg `ebur128`, jump vs 30 s baseline
  (laughter model deferred — see DECISIONS)
- `clipper/features/clips.py` — Helix viewer clips within ±20 s
- `clipper/features/lag.py` — per-streamer chat-lag calibration (cross-correlate chat rate vs loudness)
- `clipper/score/stage1.py` → `candidates.jsonl` + rows in SQLite with full feature breakdown
- `clipper/tools/preview.py` — static HTML: candidate list + embedded segment players for fast labelling

**Tests** synthetic chat bursts (z-score fires, copypasta penalised, raid spike penalised),
lag recovery on synthetic shifted signals, metric functions on hand-computed cases.

**Acceptance**
- Baselines reported: (a) old `chatProcessor` keyword method, (b) Twitch top clips as weak gold.
- On ≥3 labelled stream-days: recall@40 ≥ 0.8 of gold-good moments on the held-out day.
- Stage-1 on 5 min of data runs in < 10 s on the Mac.

**Needs from owner:** gold labels for ≥3 stream-days (≈30–45 min each with the preview page).

---

## M3 — Stage-2: Whisper + LLM judge + gates ⬜
**Files**
- `clipper/transcribe.py` — mlx-whisper (large-v3-turbo), ~60–90 s windows, word timestamps, cache
- `clipper/judge/llm.py` + `clipper/judge/prompt.md` — strict JSON (schema-validated, 1 retry),
  response cache keyed by transcript hash, cost ledger
- `clipper/gates.py` — banned topic, dead air, overlap (<90 s, keep higher), low clarity,
  streamer_is_main, program window expired; every drop logged with reason
- `clipper/score/final.py` — score, agreement multiplier, streamer_value, daily cap N=12

**Tests** each gate on fixtures; overlap suppression; JSON validation/retry with a mocked client;
cost ledger maths; end-to-end on a fixture stream with mocked LLM.

**Acceptance** precision@10 on the held-out day improves vs stage-1 alone; cost per stream-day
reported and under budget; no clip passes with `banned_topic=true`.

**Needs from owner:** LLM provider + API key + monthly cap; program rule texts; examples of
rejected-for-politics/drama moments.

---

## M4 — Render ⬜
**Files** `config/layouts.yaml` (facecam rect, gameplay rect, blur regions per streamer),
`clipper/render/subtitles.py` (ASS, word-level karaoke style), `clipper/render/render.py`
(ffmpeg filtergraph, `h264_videotoolbox`), `clipper/render/caption.py` (hook, hashtag, Twitch line).

**Tests** ASS golden file; filtergraph builder; layout schema; smoke render of 5 s `testsrc` video.

**Acceptance** 60 s clip renders in < 30 s; owner approves the look on 5 sample clips;
blur regions verified visually by owner.

**Needs from owner:** a screenshot per streamer layout (or coordinates), subtitle style, fonts,
required hashtags/caption lines per program.

---

## M5 — Telegram review queue ⬜
**Files** `clipper/review/bot.py` (long polling, private chat with owner only),
Approve / Reject(reason) / Trim(±s) buttons, `ready_to_post/<streamer>/<id>/` with
`clip.mp4`, `caption.txt`, `meta.json`; labels written to SQLite.

**Tests** callback handlers with fake updates; idempotent approve; folder layout; trim re-render call.

**Acceptance** end-to-end: candidate → Telegram → approve → folder; reject reason stored;
trim produces a new render. Preview sent ≤ 50 MB (Bot API upload limit) — send a lower-bitrate
preview, keep the full-quality file locally.

**Needs from owner:** new bot token (private), reject-reason list, owner's Telegram user id.

---

## M6 — Tracker + learning loop ⬜
**Files** `clipper/track/` (`/posted <id> <url>`, `/views <id> <n>`, reminders at 24h/48h/72h/7d and
before program submission deadline), `clipper/learn/` (dataset export, P(approve) model, weekly report).

**Tests** reminder scheduling with frozen time; dataset assembly; model training on synthetic data.

**Acceptance** weekly report with approve rate and views per streamer; P(approve) model trained
once ≥300 labels and compared to hand weights on the gold set.

**Needs from owner:** consistent logging of posted URLs and view counts; program age limits.
