# CLAUDE.md — Twitch Downloader

## What this project is
An automated pipeline that records Twitch streams, finds the moments most likely to become
high-view short-form clips (TikTok first, then YouTube Shorts / Reels), renders them as
vertical videos with subtitles, and puts them in a review queue. A human (the owner) approves
and posts manually. You (the agent) never post anything.

## Business goal (the reason every decision is made)
Net ≥ $500/month after costs (APIs, subscriptions). Money comes from streamer-run clip bounty
programs that pay per clip once it crosses view milestones. **A clip under 100k views pays $0.**
So the pipeline optimises for "can this cross 100k views?", not "is this mildly funny?".
Precision beats volume: the owner wants ~10 strong candidates per day, not 200 weak ones.


Program rules that are HARD GATES in the pipeline:
- The streamer must be the main person in the clip (their voice/facecam dominates).
- Only moments from new streams (inside the program's date window).
- No politics, conflicts, "bait"/negative drama — programs reject these even if otherwise valid.
- Ads/sponsor banners on screen must be blurred (per-streamer blur regions).
- No filler layouts (split-screen "satisfying" videos, half-screen games) and no fake reactions.
- Views are not summed; one payout per clip at the highest tier reached.

English-speaking streamers may be added later; treat them as config entries, not code changes.

## Hardware and environment
- MacBook / Mac with M4 Pro, 16 GB RAM, macOS. Apple Silicon: prefer MLX / whisper.cpp / Metal paths.
  The LLM "judge" runs through a paid API (cheap model), not locally.
- Recording runs as a background service (launchd), not inside an agent session. Use
  `caffeinate` so the machine does not sleep.

## Existing code (the starting point)
Repo: github.com/nerdtf/twitch_downloader. Python. Known facts:
- `watcher.py` polls streamers, records with streamlink into one big `.ts` per stream by reading
  1 KB chunks in a Python loop, converts to MP4 only after the stream ends (blocking).
- Chat is captured via `chat_downloader` with only timestamp/text/type. No author, emotes,
  badges, subs/raids/bits. Video start time exists only in the filename (second precision).
- Has a Telegram bot and stream metadata (title, viewer count, started_at) — reuse these.
- `videoProcessing/` contains the old keyword-based clip detection. Read it, reuse what is good,
  replace the selection logic.
Read the code before changing it. Do not assume anything in this file about the code is still true.

## Target architecture (stages)
1. **Recorder v2**: streamlink → ffmpeg segment muxer (5-min `.ts` segments, no Python byte loop),
   explicit UTC start epoch per segment, TTL cleanup. Anonymous Twitch IRC client with tags
   (user-id, emotes, badges, bits, USERNOTICE for sub/raid). Store chat in SQLite - questionable! 
2. **Stage-1 cheap scoring (every 5 min, whole stream)**: chat features + audio features +
   Twitch viewer-made clips (Helix API) → candidate windows. Tuned for recall.
3. **Stage-2 expensive scoring (candidates only)**: local Whisper transcript of ~60 s windows →
   API LLM judge returning strict JSON → hard gates → final score → global ranking.
4. **Render (survivors only)**: 1080×1920, per-streamer layout (facecam crop + gameplay crop),
   ad blur regions, word-level styled subtitles (ASS, burned in), short hook text, caption with the
   program's required hashtag + Twitch line.
5. **Review queue**: Telegram bot sends the clip with Approve / Reject(reason) / Trim buttons.
   Approved → `ready_to_post/<streamer>/<id>/` with `clip.mp4` + `caption.txt` + `meta.json`.
6. **Tracker**: owner logs posted URLs; store views at 24h/48h/72h/7d; remind to submit to the
   program before the age limit. (Manual entry first; no scraping of TikTok.)

## Scoring design
Hard gates (drop, log reason):
banned topic (politics/conflict/bait), dead air, overlaps a higher-scored candidate (<90 s apart),
low standalone clarity, program window expired.

Score = 100 × (0.30·Chat + 0.25·Audio + 0.35·Content + 0.10·Visual) × Agreement − penalties. It's not a final desicion yet, we need to adress there. 
- Chat: message-rate z-score vs 10-min rolling baseline, laugh/emote density (per-language lexicon:
  ru/uk "ахах", "ору", "кек", "))))", "лол", 😂, KEKW, LUL…), unique-chatter ratio,
  viewer-clip count within ±20 s. Penalise copypasta, raid/sub/giveaway spikes.
  Chat lags the event by ~3–10 s: shift and calibrate lag per streamer against audio peaks.
- Audio: loudness jump vs 30 s baseline, laughter/shout probability (YAMNet/PANNs-class model),
  speech-rate/pitch change.
- Content (LLM judge, JSON): humor, intensity, standalone_clarity, setup_payoff, streamer_is_main,
  banned_topic, hook_line, best_start, best_end, category, title/caption draft.
- Agreement multiplier: ≥2 independent families above threshold → 1.0, else 0.6.
- Global priority = score × streamer_value (payout tiers, program active, recent hit rate).
- Daily cap: top N overall (default 12) with a minimum score. A dull day yields fewer clips.
Weights are initial guesses. Replace them with a learned model once ≥300 labels exist.

## Learning loop
Log every candidate's features + LLM output + gate results at detection time (SQLite).
Labels: approve/reject + reason + boundary edits, then posted views. Two separate models:
P(approve) first; P(≥100k views) only after ~100 posted clips. Evaluate on a frozen gold set:
precision@10 per stream-day and recall of gold moments in top-40 candidates.

## How to work in this repo
- Python 3.12, `uv` for env/deps, `ruff` for lint+format, `pytest` for tests.
- Small, reviewable commits on a feature branch. Never push to `main` without the owner saying so.
- **Commit format (owner's hard rule):** short subject line (≤ 72 chars, imperative), blank line,
  a few lines of description. The owner is the only author (use the repo's git user as-is).
  **No `Co-Authored-By` trailers, no "Generated with Claude Code" lines, no attribution of any kind**
  in commits or PRs. (Also enforced via `attribution` in `.claude/settings.json`.)
- Active branch for the Mac rewrite: `mac-setup` (branched from `ubuntu_setup`, the version that
  ran on the Raspberry Pi). Feature branches for milestones branch from `mac-setup`.
- Every new module gets at least a smoke test. Scoring code gets unit tests with synthetic chat/audio.
- Prefer deterministic code over LLM calls. Call an LLM only where it clearly beats a heuristic.
- Config over code: streamers, programs, layouts, blur regions, lexicons live in `config/*.yaml`.
- Keep `docs/PLAN.md` (milestones + status) and `docs/DECISIONS.md` (why) up to date.
- When a decision needs the owner (money, accounts, deleting data, which streamers), stop and ask.

## Never do
- Never post, upload or submit anything to TikTok, YouTube, Instagram, Telegram channels or
  bounty programs. Never automate account creation or logins.
- Never commit secrets. Secrets live in `.env` (gitignored). The repo is public — check history.
- Never delete recordings outside the TTL logic, and never touch `data/gold/` or `data/labels/`.
- Never fake views or engagement, or build anything that does.
- Never claim clip quality is good without labels: you cannot watch video. Quality is measured
  only by the owner's approve/reject decisions and real view counts.

## Commands (keep this section current)
- `uv run pytest` — tests
- `uv run ruff check . && uv run ruff format .` — lint/format
- `uv run python -m clipper.record --config config/streamers.yaml` — recorder (TBD)
- `uv run python -m clipper.score --vod <path>` — score a local recording (TBD)
- `uv run python -m clipper.eval` — metrics on the gold set (TBD)
