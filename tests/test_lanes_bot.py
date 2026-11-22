"""Review lanes (15 per streamer, live first), decisions, posting hand-off and the Telegram bot."""

import json
import time
from datetime import UTC, datetime

from clipper import post as posting
from clipper.review import lanes, queue
from clipper.review.bot import ReviewBot
from clipper.store import Store


def clip(root, stage, login, cid, score, ev, lang="ru"):
    d = root / lang / stage / login / f"{cid:05d}_x"
    d.mkdir(parents=True)
    meta = {"login": login, "score": score, "duration": 30, "start_epoch": ev - 10,
            "end_epoch": ev + 20, "event_utc": datetime.fromtimestamp(ev, UTC).isoformat()}  # fmt: skip
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (d / "clip.mp4").write_bytes(b"\0" * 10)
    return d


def seed(store, cid, login="zloyn"):
    store.conn.execute(
        "INSERT INTO candidates(id, source, login, event_epoch, start_epoch, end_epoch, stage1,"
        " features, created_epoch) VALUES (?,?,?,0,0,0,0,'{}',0)",
        (cid, f"rec:{cid}", login),
    )


def test_refill_live_first_then_score_and_slot_frees_on_decision(tmp_path):
    store, root, now = Store(tmp_path / "x.db"), tmp_path / "clips", time.time()
    for i in range(17):  # old clips, score = i
        seed(store, i + 1)
        clip(root, "backlog", "zloyn", i + 1, float(i), now - 86400)
    for i, age in enumerate((600, 300, 60)):  # live clips, low score
        seed(store, 100 + i)
        clip(root, "backlog", "zloyn", 100 + i, 1.0, now - age)
    lanes.refill(store, root, now)
    lane = sorted(lanes.candidate_id(d) for d in lanes.clip_dirs(root, "to_review"))
    assert len(lane) == 15 and {100, 101, 102} <= set(lane)
    assert set(lane) - {100, 101, 102} == set(range(6, 18))  # 12 best old ones
    row = store.conn.execute("SELECT clip_dir FROM candidates WHERE id=100").fetchone()
    assert "/ru/to_review/zloyn/" in row[0]
    d = next(d for d in lanes.clip_dirs(root, "to_review") if lanes.candidate_id(d) == 17)
    queue.decide(store, d.relative_to(root), "rejected", "", root, reasons=["not funny"])
    assert len(lanes.clip_dirs(root, "to_review")) == 15  # topped up from the backlog
    assert len(lanes.clip_dirs(root, "backlog")) == 4


def test_migrate_old_layout_and_removed_streamer(tmp_path):
    store, root = Store(tmp_path / "x.db"), tmp_path / "clips"
    for stage, login, cid in [("pending", "zloyn", 1), ("approved", "zloyn", 2),
                              ("rejected", "bratishkinoff", 3), ("styles", "bratishkinoff", 4),
                              ("practice", "zloyn", 5)]:  # fmt: skip
        seed(store, cid)
        d = root / stage / login / f"{cid:05d}_x"
        d.mkdir(parents=True)
        (d / "meta.json").write_text("{}")
    done = lanes.migrate(store, root, langs={"zloyn": "ru"})
    assert done == {"backlog": 1, "approved": 1, "to_remove": 2}
    assert (root / "ru/backlog/zloyn/00001_x").is_dir() and (
        root / "ru/approved/zloyn/00002_x"
    ).is_dir()
    assert (root / "to_remove/bratishkinoff/rejected/00003_x").is_dir()
    assert (root / "to_remove/bratishkinoff/styles/00004_x").is_dir()
    assert (root / "practice/zloyn/00005_x").is_dir() and not (root / "pending").exists()
    assert (
        "to_remove"
        in store.conn.execute("SELECT clip_dir FROM candidates WHERE id=3").fetchone()[0]
    )
    assert lanes.migrate(store, root, langs={"zloyn": "ru"}) == {}  # idempotent


def test_edit_keeps_no_label_and_post_flow_to_posted(tmp_path):
    store, root, now = Store(tmp_path / "x.db"), tmp_path / "clips", time.time()
    for cid in (1, 2):
        seed(store, cid)
        clip(root, "to_review", "zloyn", cid, 50, now - 100)
    e = queue.decide(store, "ru/to_review/zloyn/00001_x", "edit", "start 5 s earlier", root,
                     reasons=["start earlier"])  # fmt: skip
    info = json.loads((e / "edit.json").read_text())
    assert info["reasons"] == ["start earlier"] and "5 s" in info["note"]
    assert store.conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0] == 0
    from clipper.review.recut import list_edits

    assert list_edits(root)[0]["dir"] == "ru/edit/zloyn/00001_x"
    a = queue.decide(store, "ru/to_review/zloyn/00002_x", "approved", "", root,
                     platforms=["tiktok", "shorts"])  # fmt: skip
    p = json.loads((a / "post.json").read_text())
    assert set(p["platforms"]) == {"tiktok", "shorts"} and p["lang"] == "ru"
    assert p["captions"]["shorts"].split("\n")[0].endswith("#shorts")
    assert [x["candidate_id"] for x in posting.items(root, platform="tiktok")] == [2]
    posting.mark(store, 2, "tiktok", "posted", url="https://tiktok.com/@me/video/9", root=root)
    assert store.conn.execute("SELECT platform FROM posts").fetchone()[0] == "tiktok"
    assert posting.items(root, platform="tiktok") == [] and a.is_dir()
    d = posting.mark(store, 2, "shorts", "skipped", root=root)
    assert d == root / "ru/posted/zloyn/00002_x" and not a.exists()


class FakeTG:
    def __init__(self):
        self.calls, self.n = [], 100

    def __call__(self, method, files=None, timeout=60, **data):
        self.calls.append((method, data))
        self.n += 1
        if method == "getUpdates":
            return []
        return {"message_id": self.n} if method in ("sendVideo", "sendMessage") else True

    def sent(self):
        return [d for m, d in self.calls if m == "sendVideo"]


def cb(data, n=[0]):  # noqa: B006 - a counter for unique ids
    n[0] += 1
    return {"update_id": n[0], "callback_query": {"id": str(n[0]), "data": data,
                                                   "message": {"chat": {"id": 42}}}}  # fmt: skip


def text(t, reply=None, n=[1000]):  # noqa: B006
    n[0] += 1
    msg = {"chat": {"id": 42}, "text": t}
    if reply:
        msg["reply_to_message"] = {"message_id": reply}
    return {"update_id": n[0], "message": msg}


def test_bot_rotates_streamers_platforms_notes_reject_and_undo(tmp_path):
    store, root, now = Store(tmp_path / "x.db"), tmp_path / "clips", time.time()
    for cid, login, score in [(1, "zloyn", 90), (2, "zloyn", 80), (3, "buster", 10)]:
        seed(store, cid, login)
        clip(root, "to_review", login, cid, score, now - 86400)
    tg = FakeTG()
    bot = ReviewBot(store, tg, "42", root)
    assert bot.next()
    first = bot.st["cur"]["cid"]
    bot.handle(text("лучший момент"))  # plain text = note for the clip on screen
    assert bot.st["cur"]["note"] == "лучший момент"
    bot.handle(cb(f"p:{first}:reels"))
    assert bot.st["cur"]["plats"] == ["tiktok", "reels"]
    bot.handle(cb(f"a:{first}"))
    approved = lanes.clip_dirs(root, "approved")
    assert [lanes.candidate_id(d) for d in approved] == [first]
    assert set(json.loads((approved[0] / "post.json").read_text())["platforms"]) == {
        "tiktok",
        "reels",
    }
    assert "лучший момент" in (approved[0] / "note.txt").read_text()
    second = bot.st["cur"]["cid"]
    logins = {1: "zloyn", 2: "zloyn", 3: "buster"}
    assert logins[second] != logins[first]  # another streamer next
    bot.handle(cb(f"a:{first}"))  # stale button: ignored
    assert bot.st["cur"]["cid"] == second
    bot.handle(cb(f"r:{second}"))
    assert bot.st["cur"]["view"] == "reject"
    bot.handle(cb(f"rr:{second}:1"))  # "streamer not main"
    lab = store.conn.execute("SELECT reason FROM labels WHERE candidate_id=?", (second,)).fetchone()
    assert lab[0] == "streamer not main"
    third = bot.st["cur"]["cid"]
    msg_of_first = next(int(k) for k, v in bot.st["msgs"].items() if v == first)
    bot.handle(text("подписать стримера", reply=msg_of_first))  # note to an already approved clip
    assert "подписать" in (approved[0] / "note.txt").read_text()
    bot.handle(cb(f"u:{second}"))  # undo the reject: shown again, the current one goes back
    assert bot.st["cur"]["cid"] == second
    assert (
        store.conn.execute(
            "SELECT COUNT(*) FROM labels WHERE candidate_id=?", (second,)
        ).fetchone()[0]
        == 0
    )
    assert {lanes.candidate_id(d) for d in lanes.clip_dirs(root, "to_review")} == {second, third}
    bot.handle(cb(f"e:{second}"))
    bot.handle(cb(f"er:{second}:5"))  # webcam moved
    assert json.loads(next(iter(lanes.clip_dirs(root, "edit"))).joinpath("edit.json").read_text())[
        "reasons"] == ["webcam moved / wrong crop"]  # fmt: skip


def test_bot_lets_go_of_a_clip_decided_on_the_web(tmp_path):
    store, root, now = Store(tmp_path / "x.db"), tmp_path / "clips", time.time()
    seed(store, 1)
    clip(root, "to_review", "zloyn", 1, 50, now)
    bot = ReviewBot(store, FakeTG(), "42", root)
    bot.next()
    queue.decide(store, "ru/to_review/zloyn/00001_x", "rejected", "meh", root)
    bot.check_current()
    assert bot.st["cur"] is None and not bot.next()
