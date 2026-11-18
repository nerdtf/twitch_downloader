# clipper — Twitch streams → vertical clips for review

Records Twitch streams, finds moments likely to become high-view short clips, renders them as
1080×1920 videos with subtitles, and puts them in a review folder. A human approves and posts.

## Quick start (macOS, Apple Silicon)
```bash
uv sync                                   # Python 3.12 env
cp .env.example .env                      # fill TWITCH_*, LLM_*, (optional) TELEGRAM_*
uv run python -m clipper.twitch.helix --check buster      # Helix OK?
uv run python -m clipper.record           # recorder (or deploy/install_recorder.sh for launchd)
uv run python -m clipper.pipeline live    # finds + renders clips while streams are live
open data/clips/index.html                # review
```

## Layout of the code (one topic = one package)
| Path | What lives there |
|---|---|
| `clipper/record/` | recorder service: streamlink→ffmpeg segments, Helix live check, retention |
| `clipper/chat/` | everything chat: IRC capture, lexicon, per-second chat features |
| `clipper/twitch/` | Twitch API (Helix) and past-broadcast (VOD) download |
| `clipper/media/` | ffmpeg helpers: loudness, cutting, alignment |
| `clipper/scoring/` | stage-1: signals → per-second score → candidates |
| `clipper/judge/` | stage-2: Whisper transcript, LLM judge, hard gates, final score |
| `clipper/render/` | vertical layouts, sponsor blur, subtitles, hook, captions |
| `clipper/pipeline/` | glue: run stage-1 → stage-2 → render for a VOD or live recordings |
| `clipper/review/`, `track/`, `learn/` | review sync, posted-clip view tracker, learning dataset |
| `clipper/eval/` | metrics vs. owner gold / viewer-clip weak gold |
| `clipper/sources.py`, `store.py`, `config.py` | source abstraction, SQLite schema, config loading |
| `config/*.yaml` | streamers, layouts, lexicon, scoring weights, programs, eval set |
| `data/` (gitignored) | recordings, DB, VODs, caches, clips, logs |

## Commands
| Command | Purpose |
|---|---|
| `uv run pytest` · `uv run ruff check . && uv run ruff format .` | tests · lint/format |
| `uv run python -m clipper.record` | recorder (`--duration N` for a test run) |
| `uv run python -m clipper.pipeline live` | live clip finding during streams |
| `uv run python -m clipper.pipeline vod <id>... [--judge 8 --render 3]` | practice on a past broadcast |
| `uv run python -m clipper.twitch.vod <id>...` | download VOD audio + viewer clips |
| `uv run python -m clipper.eval` | p@10 / recall@40 on config/eval.yaml (hold-out reported apart) |
| `uv run python -m clipper.review sync` | record approve/reject decisions from folders |
| `uv run python -m clipper.track posted/views/submitted/due` | posted clips and their views |
| `uv run python -m clipper.learn export` / `train` | dataset / P(approve) model (≥300 labels) |

Docs: `docs/PLAN.md` (milestones), `docs/DECISIONS.md` (why), `docs/PI_MIGRATION.md`.
