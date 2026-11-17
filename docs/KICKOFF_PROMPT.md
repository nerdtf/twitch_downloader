# Kickoff prompts for Claude Code

Paste these one at a time. Each block is one session (or one phase).
Start a fresh session (`/clear`) between phases so context stays small.
CLAUDE.md is loaded automatically — you don't need to repeat it.

---

## Phase 0 — Understand and plan (run in PLAN MODE: Shift+Tab until it says "plan mode")

```
Read CLAUDE.md, then read the whole repository: watcher.py, daemon.py, videoProcessing/,
the Telegram bot, config files, requirements. Do not change any files yet.

Then write back to me:
1. How the current code actually works, module by module (one short paragraph each),
   and where it differs from what CLAUDE.md says.
2. What to keep, what to rewrite, what to delete — with reasons.
3. Any secrets or tokens committed to git history (check `git log -p` for tokens/keys).
   If you find any, tell me which and stop — I will rotate them.
4. A milestone plan for docs/PLAN.md: M1 recorder v2, M2 stage-1 scorer + eval harness,
   M3 stage-2 (Whisper + LLM judge + gates), M4 render, M5 Telegram review queue,
   M6 tracker + learning loop. For each: files, tests, acceptance criteria, and what
   you need from me (keys, decisions, labels).
5. Your top 5 questions for me, ordered by how much they change the design.

Be critical. If something in CLAUDE.md is a bad idea on this hardware, say so.
```

---

## Phase 1 — Recorder v2 (normal mode, after you approve the plan)

```
Implement milestone M1 from docs/PLAN.md on a new branch `m1-recorder`.
Requirements are in CLAUDE.md (segmented recording, start epochs, TTL, IRC chat with tags,
SQLite, launchd service, caffeinate). Streamers come from config/streamers.yaml.

Work in small commits. Write tests for: segment naming/epoch math, TTL cleanup (on a temp dir),
IRC tag parsing (use recorded sample lines), chat-to-video time alignment.

When done: run the recorder for 15 minutes on 2 streamers that are live right now
, then report: files produced,
disk use per hour per stream, CPU/RAM use, chat messages captured, any dropped segments.
Do not install the launchd service until I say so — give me the plist and the command.
```

---

## Phase 2 — Stage-1 scorer + evaluation harness

```
Implement M2 on branch `m2-scoring`. Before scoring code, build the evaluation harness:
- `data/gold/<stream_id>.csv` will contain my labelled moments (start_sec, end_sec, label
  good/bad, note). Create the format, a loader, and `clipper.eval` that reports
  recall of gold-good moments in the top-40 candidates and precision@10.
- Then implement stage-1 scoring exactly as CLAUDE.md describes (chat + audio + Twitch clips),
  with per-streamer chat-lag calibration.
- Output per stream: candidates.jsonl with all features and the score breakdown, plus a
  simple HTML or Telegram preview so I can label candidates fast.

You cannot judge if a clip is funny — I will label. After I label, tune thresholds and weights
against the gold set only, and show me before/after metrics. Do not overfit: hold out at
least one stream-day that you never tune on.
```

---

## Phase 3 onward — reuse this template

```
Implement milestone <Mx> from docs/PLAN.md on branch `<mx-name>`.
Read the relevant CLAUDE.md sections first. Plan briefly, then build in small commits with tests.
At the end: run it on real data in data/recordings, show me metrics or sample outputs,
update docs/PLAN.md status and docs/DECISIONS.md, and list what you need from me next.
Stop and ask before: spending money on APIs beyond a $2 test, deleting data, or changing
anything in data/gold or data/labels.
```

---

## Useful one-liners during work

- "Run the eval and show me what changed since the last commit."
- "Explain the score breakdown for candidate <id>: which signals fired and why it ranked where it did."
- "Here are my labels for today (file attached / path). Retune and report on the held-out day."
- "Something feels off with <X>. Investigate first, don't fix yet. Tell me the root cause."
