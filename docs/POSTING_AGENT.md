# Posting agent hand-off

The pipeline never posts. The owner approves clips (Telegram bot or web app) and picks the
platforms; a separate posting agent turns them into **drafts**, and the owner publishes them.
This file is that agent's contract.

## Where the work is

```
data/clips/<lang>/approved/<login>/<id>/      lang = ru | en  (ru and en go to different accounts)
  clip.mp4        1080×1920 H.264, burned-in subtitles: upload this file
  cover.jpg       frame at the moment itself (cover / thumbnail)
  caption.txt     title + required hashtags + Twitch line
  post.json       everything below; the agent updates only `platforms`, through the CLI
  meta.json       pipeline data (scores, transcript, layout): read-only
  note.txt        owner's comment
```

Ignore `tg.mp4`: it is a smaller copy made for Telegram.

The queue, with the newest moment first (post live moments first, because they go stale):

```bash
uv run python -m clipper.post list --platform tiktok --json        # todo items for TikTok
uv run python -m clipper.post list --lang en --status drafted      # en drafts not yet published
uv run python -m clipper.post show <candidate_id>
```

## post.json

| field | meaning |
|---|---|
| `candidate_id`, `login`, `lang` | which clip, which streamer, which account language |
| `video`, `cover` | file names inside the folder (`cover` may be null) |
| `captions.tiktok / reels / shorts` | caption to paste. Shorts' first line is a title of 100 characters or less, ending in `#shorts`. |
| `title`, `hashtags`, `twitch_url` | parts for building your own caption. `hashtags` are the program's **required** tags and must stay in. |
| `transcript`, `stream_title`, `game` | context for a better caption or title (the LLM step is off, so `title` is often empty) |
| `post_by` | after this the program no longer pays (program `window_days` from the moment) |
| `owner_note`, `owner_reasons` | owner's comment from review, for example "start at 0:03". Follow it. |
| `platforms.<p>.status` | `todo` → `drafted` → `posted`, or `failed` / `skipped` |

## Reporting back (always use the CLI, never edit post.json by hand)

```bash
uv run python -m clipper.post mark <id> tiktok drafted --note "draft in TikTok app"
uv run python -m clipper.post mark <id> tiktok failed  --note "upload error: ..."
uv run python -m clipper.post mark <id> reels skipped  --note "owner: not for reels"
uv run python -m clipper.post mark <id> tiktok posted  --url https://www.tiktok.com/@acc/video/123
```

- `posted --url` also logs the post for view tracking (`clipper.track due` reminds the owner to
  log views at 24 h, 48 h, 72 h and 7 days, and to submit the clip to the program).
- When every chosen platform is `posted` or `skipped`, the folder moves to `<lang>/posted/`.

## Rules

- Drafts only, unless the owner says otherwise for a given account.
- Use only the platforms listed in `platforms`.
- Keep the required hashtags and the Twitch line. Programs reject clips without them.
- No politics, conflict or "bait" captions. Never fake engagement.
- Clips approved before 2026-11-22 have no post.json, so they are not in the queue.
