"""Telegram review bot: one clip at a time in the owner's private chat (owner 2026-11-22).

  uv run python -m clipper.review bot          # long-polls Telegram; launchd keeps it running

Each clip comes as a video with buttons:
  ✅ Approve · ✏️ Edit · ❌ Reject        reject / edit open a list of reasons
  ☑ TikTok  ☐ Reels  ☐ Shorts           where to post (approve needs at least one)
  📝 Note · ⏭ Skip
Any text you send is a note for the clip on screen; a text sent as a *reply* to an older clip
goes to that clip (also after deciding it). Decided messages keep an ↶ Undo button.
The next clip comes right after a decision: a different streamer each time, live clips first.
Commands: /next /stats /edits /pause /resume /help

Only the chat in TELEGRAM_CHAT_ID is obeyed. Decisions go through clipper.review.queue, so the
bot and the web app (http://127.0.0.1:8765) can be used side by side.
"""

import contextlib
import json
import logging
import os
import subprocess
import time
from pathlib import Path

import requests

from clipper.env import load_env
from clipper.review import lanes, queue
from clipper.review.lanes import CLIPS, candidate_id, event_epoch, read_meta
from clipper.store import Store

log = logging.getLogger("clipper.bot")
TG_MAX = 49 * 1024 * 1024  # bots may upload up to 50 MB
SKIP_S = 3600  # a skipped clip is not shown again for this long (unless nothing else is left)
REJECT = ["not funny", "streamer not main", "no context", "boring / quiet", "bad crop / timing",
          "nudity / 18+", "politics / drama", "ads / giveaway", "already posted / duplicate"]  # fmt: skip
EDIT = ["start earlier", "start later", "end earlier", "end later", "lag / desync",
        "webcam moved / wrong crop", "subtitles wrong", "other (see note)"]  # fmt: skip
PLAT_LABEL = {"tiktok": "TikTok", "reels": "Reels", "shorts": "Shorts"}
DEFAULT_PLATS = ["tiktok"]
HELP = __doc__.split("\n\n", 1)[1].split("\n\nOnly", 1)[0]


class Telegram:
    def __init__(self, token: str):
        self.token, self.http = token, requests.Session()

    def __call__(self, method: str, files: dict | None = None, timeout: float = 60, **data):
        for k, v in list(data.items()):
            if isinstance(v, dict | list):
                data[k] = json.dumps(v, ensure_ascii=False)
        try:
            r = self.http.post(
                f"https://api.telegram.org/bot{self.token}/{method}",
                data=data, files=files, timeout=timeout,
            )  # fmt: skip
        except requests.RequestException as e:
            raise RuntimeError(str(e).replace(self.token, "<token>")) from None
        body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
        if not body.get("ok"):
            raise RuntimeError(f"{method}: {body.get('description') or r.status_code}")
        return body["result"]


def tg_video(d: Path) -> Path:
    """clip.mp4, or a smaller tg.mp4 copy when it is over Telegram's 50 MB bot limit."""
    clip = d / "clip.mp4"
    if clip.stat().st_size <= TG_MAX:
        return clip
    small = d / "tg.mp4"
    if small.exists() and small.stat().st_mtime >= clip.stat().st_mtime:
        return small
    dur = float(read_meta(d).get("duration") or 60)
    kbps = int(min(6000, (TG_MAX * 8 / 1000) * 0.85 / max(dur, 1) - 160))
    for enc in (["-c:v", "h264_videotoolbox", "-b:v", f"{kbps}k"],
                ["-c:v", "libx264", "-preset", "veryfast", "-b:v", f"{kbps}k"]):  # fmt: skip
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(clip), "-vf", "scale=720:-2", *enc,
               "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", str(small)]  # fmt: skip
        if subprocess.run(cmd, capture_output=True).returncode == 0 and small.exists():
            return small
    return clip


def pick_next(root: Path, last_login: str | None, shown: dict, skipped: dict, now: float):
    """The clip to show next: another streamer than the last one, streamers with live (fresh)
    clips first, then the one not seen the longest; inside a streamer the lane order."""
    by_login: dict[str, list] = {}
    for d in lanes.clip_dirs(root, "to_review"):
        by_login.setdefault(d.parent.name, []).append((d, read_meta(d)))
    if not by_login:
        return None

    def usable(clips):
        ok = [c for c in clips if now - skipped.get(str(c[0].name), 0) > SKIP_S]
        return ok

    pool = {lg: usable(c) for lg, c in by_login.items()}
    pool = {lg: c for lg, c in pool.items() if c} or by_login

    def key(lg):
        fresh = any(now - event_epoch(m) <= lanes.FRESH_S for _, m in pool[lg])
        return (lg == last_login and len(pool) > 1, not fresh, shown.get(lg, 0), lg)

    login = min(pool, key=key)
    return min(pool[login], key=lambda c: lanes.priority(c[1], now))[0]


class ReviewBot:
    def __init__(self, store: Store, api, chat_id: str, root: Path = CLIPS):
        self.store, self.api, self.chat, self.root = store, api, str(chat_id), Path(root)
        try:
            self.st = json.loads(store.kv_get("bot.state", "{}"))
        except ValueError:
            self.st = {}
        self.st.setdefault("offset", 0)
        self.st.setdefault("shown", {})
        self.st.setdefault("skipped", {})
        self.st.setdefault("msgs", {})  # message id -> candidate id (notes by reply)
        self.st.setdefault("cur", None)
        self.st.setdefault("paused", False)

    # ---------- state
    def save(self):
        msgs = self.st["msgs"]
        if len(msgs) > 400:
            self.st["msgs"] = dict(list(msgs.items())[-300:])
        cut = time.time() - SKIP_S
        self.st["skipped"] = {k: v for k, v in self.st["skipped"].items() if v > cut}
        self.store.kv_set("bot.state", json.dumps(self.st, ensure_ascii=False))

    def _dir(self, cid: int) -> Path | None:
        for stage in lanes.STAGES:
            for d in lanes.clip_dirs(self.root, stage):
                if candidate_id(d) == cid:
                    return d
        return None

    def send(self, text: str, **kw):
        return self.api("sendMessage", chat_id=self.chat, text=text, **kw)

    # ---------- what the clip message looks like
    def caption(self, d: Path, cur: dict, head: str = "") -> str:
        m, now = read_meta(d), time.time()
        ev = event_epoch(m)
        ago = (now - ev) / 60
        age = f"{ago:.0f} min ago" if ago < 120 else f"{ago / 60:.0f} h ago"
        c = lanes.counts(self.root)
        lines = [
            head,
            f"🎬 {m.get('login')} · {d.parents[2].name} · score {float(m.get('score') or 0):.0f}"
            f" · {float(m.get('duration') or 0):.0f} s · #{candidate_id(d)}",
            f"🕒 {time.strftime('%d.%m %H:%M', time.localtime(ev))} ({age})"
            + (" · 🔴 live" if now - ev <= lanes.FRESH_S else ""),
            f"📺 {m.get('stream_title', '')[:120]}" if m.get("stream_title") else "",
            f"🎮 {m.get('game', '')} · {m.get('layout', '')}" if m.get("game") else "",
            f"✂️ re-cut {len(m['recuts'])}×" if m.get("recuts") else "",
            f"📝 {cur.get('note')}" if cur.get("note") else "",
            f"📥 to review: {sum(c['to_review'].values())} · waiting: {sum(c['backlog'].values())}",
        ]
        return "\n".join(x for x in lines if x)[:1024]

    def keyboard(self, cid: int, cur: dict) -> dict:
        view = cur.get("view", "main")
        if view in ("reject", "edit"):
            opts, tag = (REJECT, "rr") if view == "reject" else (EDIT, "er")
            rows = [[{"text": t, "callback_data": f"{tag}:{cid}:{i}"} for i, t in
                     enumerate(opts) if i // 2 == r] for r in range((len(opts) + 1) // 2)]  # fmt: skip
            return {"inline_keyboard": [*rows, [{"text": "« back", "callback_data": f"b:{cid}"}]]}
        plats = cur.get("plats", [])
        return {"inline_keyboard": [
            [{"text": "✅ Approve", "callback_data": f"a:{cid}"},
             {"text": "✏️ Edit", "callback_data": f"e:{cid}"},
             {"text": "❌ Reject", "callback_data": f"r:{cid}"}],
            [{"text": ("☑ " if p in plats else "☐ ") + PLAT_LABEL[p], "callback_data": f"p:{cid}:{p}"}
             for p in queue.PLATFORMS],
            [{"text": "📝 Note", "callback_data": f"n:{cid}"},
             {"text": "⏭ Skip", "callback_data": f"s:{cid}"}],
        ]}  # fmt: skip

    def refresh(self):
        cur = self.st["cur"]
        d = self._dir(cur["cid"])
        if not d:
            return
        try:
            self.api("editMessageCaption", chat_id=self.chat, message_id=cur["msg"],
                     caption=self.caption(d, cur), reply_markup=self.keyboard(cur["cid"], cur))  # fmt: skip
        except RuntimeError as e:
            if "not modified" not in str(e):
                raise

    def close_msg(self, msg: int, d: Path | None, head: str, undo_cid: int | None = None):
        kb = {"inline_keyboard": [[{"text": "↶ Undo", "callback_data": f"u:{undo_cid}"}]]}
        cap = self.caption(d, {}, head) if d and d.exists() else head
        try:
            self.api("editMessageCaption", chat_id=self.chat, message_id=msg, caption=cap,
                     reply_markup=kb if undo_cid else {"inline_keyboard": []})  # fmt: skip
        except RuntimeError as e:
            log.info("could not update message %s: %s", msg, e)

    # ---------- showing clips
    def show(self, d: Path, loud: bool = False, note: str = "") -> None:
        cid = candidate_id(d)
        cur = {"cid": cid, "plats": list(DEFAULT_PLATS), "note": note, "view": "main"}
        m = read_meta(d)
        video = tg_video(d)
        with video.open("rb") as fh:
            res = self.api(
                "sendVideo", files={"video": (f"{cid}.mp4", fh, "video/mp4")}, timeout=300,
                chat_id=self.chat, caption=self.caption(d, cur), supports_streaming="true",
                width=1080, height=1920, duration=int(float(m.get("duration") or 0)),
                disable_notification="false" if loud else "true",
                reply_markup=self.keyboard(cid, cur),
            )  # fmt: skip
        cur["msg"] = res["message_id"]
        self.st["cur"] = cur
        self.st["msgs"][str(cur["msg"])] = cid
        self.st["shown"][d.parent.name] = time.time()
        self.st["last_login"] = d.parent.name
        self.save()

    def next(self, loud: bool = False) -> bool:
        if self.st["cur"] or self.st["paused"]:
            return False
        lanes.refill(self.store, self.root)
        d = pick_next(self.root, self.st.get("last_login"), self.st["shown"],
                      self.st["skipped"], time.time())  # fmt: skip
        if d is None:
            return False
        self.show(d, loud=loud)
        return True

    def check_current(self) -> None:
        """The clip on screen was decided in the web app (or recut/moved): let it go."""
        cur = self.st["cur"]
        if not cur:
            return
        d = self._dir(cur["cid"])
        if d is None or d.parents[1].name != "to_review":
            stage = d.parents[1].name if d else "gone"
            self.close_msg(cur["msg"], d, f"— decided elsewhere ({stage})")
            self.st["cur"] = None
            self.save()

    # ---------- decisions
    def decide(self, decision: str, reasons: list[str] | None = None):
        cur = self.st["cur"]
        d = self._dir(cur["cid"])
        plats = cur.get("plats") or []
        dst = queue.decide(self.store, d.relative_to(self.root), decision, cur.get("note", ""),
                           self.root, platforms=plats, reasons=reasons, by="telegram")  # fmt: skip
        head = {
            "approved": "✅ APPROVED → " + ", ".join(PLAT_LABEL[p] for p in plats),
            "rejected": "❌ REJECTED: " + ", ".join(reasons or []),
            "edit": "✏️ TO EDIT: " + ", ".join(reasons or []),
        }[decision]
        self.st["cur"] = None
        self.close_msg(cur["msg"], dst, head, undo_cid=cur["cid"])
        self.save()
        self.next()

    def undo(self, cid: int):
        d = self._dir(cid)
        if d is None or d.parents[1].name not in queue.DECISIONS:
            return "nothing to undo"
        back = queue.undo(self.store, d.relative_to(self.root), self.root)
        cur = self.st["cur"]
        if cur:  # put the clip on screen back in the lane and show the undone one again
            self.close_msg(cur["msg"], self._dir(cur["cid"]), "⏸ put back (undo)")
            self.st["cur"] = None
        self.close_msg(next((int(k) for k, v in self.st["msgs"].items() if v == cid), 0) or 0,
                       None, "↶ undone — sent again below")  # fmt: skip
        self.show(back)
        return "undone"

    # ---------- Telegram updates
    def on_callback(self, q: dict) -> None:
        data, answer = q.get("data", ""), ""
        parts = data.split(":")
        act, cid = parts[0], int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
        cur = self.st["cur"]
        try:
            if act == "u":
                answer = self.undo(cid)
            elif not cur or cur["cid"] != cid:
                answer = "This clip is not the current one any more"
            elif act == "p":
                p = parts[2]
                cur["plats"] = [x for x in cur["plats"] if x != p] if p in cur["plats"] else [
                    *cur["plats"], p]  # fmt: skip
                self.refresh()
            elif act == "a":
                if not cur["plats"]:
                    answer = "Pick at least one platform first"
                else:
                    self.decide("approved")
                    answer = "Approved"
            elif act in ("r", "e"):
                cur["view"] = "reject" if act == "r" else "edit"
                self.refresh()
            elif act == "b":
                cur["view"] = "main"
                self.refresh()
            elif act in ("rr", "er"):
                opts = REJECT if act == "rr" else EDIT
                self.decide("rejected" if act == "rr" else "edit", [opts[int(parts[2])]])
                answer = "Rejected" if act == "rr" else "Sent to edit"
            elif act == "n":
                login = self._dir(cid).parent.name
                res = self.send(f"✍️ Note for {login} #{cid}: write it as a reply",
                                reply_markup={"force_reply": True, "selective": True})  # fmt: skip
                self.st["msgs"][str(res["message_id"])] = cid
            elif act == "s":
                d = self._dir(cid)
                self.st["skipped"][d.name] = time.time()
                self.st["cur"] = None
                self.close_msg(cur["msg"], d, "⏭ skipped (stays in the lane)")
                self.save()
                if not self.next():
                    answer = "Nothing else to review"
        except (ValueError, RuntimeError) as e:
            answer = str(e)[:190]
            log.warning("callback %s: %s", data, e)
        self.save()
        with contextlib.suppress(RuntimeError):
            self.api("answerCallbackQuery", callback_query_id=q["id"], text=answer)

    def on_message(self, msg: dict) -> None:
        text = (msg.get("text") or "").strip()
        if not text:
            return
        cmd = text.split()[0].split("@")[0].lower() if text.startswith("/") else ""
        if cmd in ("/start", "/help"):
            self.send(HELP)
        elif cmd == "/next":  # skip the clip on screen (it stays in the lane) and show another
            if cur := self.st["cur"]:
                d = self._dir(cur["cid"])
                if d is not None:
                    self.st["skipped"][d.name] = time.time()
                self.close_msg(cur["msg"], d, "⏭ skipped (stays in the lane)")
                self.st["cur"] = None
            if not self.next():
                self.send("Nothing (else) to review right now.")
        elif cmd == "/stats":
            self.send(self.stats())
        elif cmd == "/edits":
            from clipper.review.recut import list_edits

            e = list_edits(self.root)
            self.send("\n".join(f"#{x.get('candidate_id')} {x['dir'].split('/')[2]}: "
                                f"{', '.join(x.get('reasons') or [])} {x.get('note') or ''}"
                                for x in e) or "edit/ is empty")  # fmt: skip
        elif cmd == "/pause":
            self.st["paused"] = True
            self.send("Paused. /resume to get clips again.")
        elif cmd == "/resume":
            self.st["paused"] = False
            if not self.next():
                self.send("Resumed — nothing to review right now.")
        elif cmd:
            self.send("Unknown command. /help")
        else:
            self.note(msg, text)
        self.save()

    def note(self, msg: dict, text: str) -> None:
        reply = (msg.get("reply_to_message") or {}).get("message_id")
        cid = self.st["msgs"].get(str(reply)) if reply else None
        cur = self.st["cur"]
        if cid is None and cur:
            cid = cur["cid"]
        if cid is None:
            self.send("No clip on screen — reply to a clip to add a note to it.")
            return
        if cur and cur["cid"] == cid:
            cur["note"] = f"{cur.get('note', '')} {text}".strip()
            self.refresh()
            self.send(f"📝 noted for #{cid} — now pick Approve / Edit / Reject",
                      disable_notification="true")  # fmt: skip
            return
        d = self._dir(cid)
        if d is None:
            self.send(f"#{cid}: clip folder not found")
            return
        queue.add_note(self.store, d.relative_to(self.root), text, self.root)
        self.send(f"📝 added to #{cid} ({d.parents[1].name})", disable_notification="true")

    def stats(self) -> str:
        c = lanes.counts(self.root)
        logins = sorted({lg for s in c.values() for lg in s})
        rows = [f"{lg:<13} review {c['to_review'].get(lg, 0):>2} · wait {c['backlog'].get(lg, 0):>3}"
                f" · ✅{c['approved'].get(lg, 0)} ✏️{c['edit'].get(lg, 0)} 📤{c['posted'].get(lg, 0)}"
                for lg in logins]  # fmt: skip
        return f"Lanes (max {lanes.LANE} per streamer):\n" + "\n".join(rows)

    def handle(self, upd: dict) -> None:
        if q := upd.get("callback_query"):
            if str((q.get("message") or {}).get("chat", {}).get("id")) == self.chat:
                self.on_callback(q)
        elif (msg := upd.get("message")) and str(msg.get("chat", {}).get("id")) == self.chat:
            self.on_message(msg)

    def poll(self, timeout: int = 25) -> None:
        ups = self.api("getUpdates", offset=self.st["offset"], timeout=timeout,
                       allowed_updates=["message", "callback_query"])  # fmt: skip
        for u in ups:
            self.st["offset"] = u["update_id"] + 1
            try:
                self.handle(u)
            except Exception:  # one bad update must not kill the bot
                log.exception("update %s", u.get("update_id"))
        self.save()


def run(store: Store, root: Path = CLIPS) -> None:
    load_env()
    token, chat = os.environ.get("TELEGRAM_BOT_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat:
        raise SystemExit("set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env")
    tg = Telegram(token)

    def api(method, files=None, timeout=60, **data):
        if method == "getUpdates":
            timeout = int(data.get("timeout", 25)) + 15
        return tg(method, files=files, timeout=timeout, **data)

    bot = ReviewBot(store, api, chat, root)
    log.info("review bot started (lane %d per streamer)", lanes.LANE)
    while True:
        try:
            bot.check_current()
            bot.next(loud=True)  # new clips while idle -> with sound
            bot.poll()
        except Exception as e:  # network down, Telegram 5xx...: wait and go on
            log.warning("bot loop: %s", e)
            time.sleep(10)
