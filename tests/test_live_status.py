import asyncio

from clipper.config import RecorderSettings, Streamer
from clipper.record.__main__ import keep_chat_row
from clipper.record.live_status import HelixWatcher
from clipper.record.recorder import StreamRecorder
from clipper.store import Store
from clipper.twitch.helix import StreamInfo


def info(login, game="GTA VI"):
    return StreamInfo("1", login, "s1", "title", game, "2026-11-19T00:00:00Z", 10)


def test_apply_sets_event_only_on_offline_to_live():
    w = HelixWatcher(None, ["buster", "zloyn"], 15)
    w.healthy = True
    assert w.apply({"1": info("buster")}) == ["buster"]
    assert w.went_live["buster"].is_set() and not w.went_live["zloyn"].is_set()
    w.went_live["buster"].clear()
    assert w.apply({"1": info("buster")}) == []  # still live: no new event
    assert w.is_live("buster") is True and w.is_live("zloyn") is False
    w.apply({})
    assert w.is_live("buster") is False


def test_unhealthy_watcher_reports_unknown():
    w = HelixWatcher(None, ["a_b"], 15)
    assert w.is_live("a_b") is None and not w.enabled


def settings(tmp_path):
    return RecorderSettings(
        tmp_path,
        tmp_path / "db",
        300,
        poll_seconds=3600,
        helix_poll_seconds=15,
        min_free_gb=1,
        stall_seconds=90,
        ttl_check_seconds=600,
    )


async def test_recorder_wakes_on_helix_event(tmp_path):
    w = HelixWatcher(None, ["buster"], 15)
    rec = StreamRecorder(Streamer("buster", "best", 6), settings(tmp_path), Store(":memory:"), w)
    stop = asyncio.Event()
    sleeper = asyncio.create_task(rec._sleep(stop, 3600))
    await asyncio.sleep(0.01)
    w.went_live["buster"].set()
    await asyncio.wait_for(sleeper, timeout=1)  # woke long before 3600 s
    assert not w.went_live["buster"].is_set()


def test_metadata_written_and_changes_logged(tmp_path):
    store = Store(tmp_path / "x.db")
    w = HelixWatcher(None, ["buster"], 15)
    rec = StreamRecorder(Streamer("buster", "best", 6), settings(tmp_path), store, w)
    rid = store.start_recording("buster", tmp_path, 0)
    w.apply({"1": info("buster", game="Just Chatting")})
    rec._sync_meta(rid)
    w.apply({"1": info("buster", game="Grand Theft Auto VI")})
    rec._sync_meta(rid)
    row = store.conn.execute("SELECT game_name, twitch_stream_id FROM recordings").fetchone()
    assert tuple(row) == ("Grand Theft Auto VI", "s1")
    kinds = [r[0] for r in store.conn.execute("SELECT kind FROM events")]
    assert kinds == ["game_change"]


def test_store_migrates_old_schema(tmp_path):
    import sqlite3

    db = tmp_path / "old.db"
    con = sqlite3.connect(db)
    con.execute(
        "CREATE TABLE recordings (id INTEGER PRIMARY KEY, login TEXT NOT NULL, dir TEXT NOT NULL,"
        " started_epoch REAL NOT NULL, ended_epoch REAL, exit_reason TEXT)"
    )
    con.commit()
    con.close()
    st = Store(db)
    cols = {r[1] for r in st.conn.execute("PRAGMA table_info(recordings)")}
    assert {"title", "game_name", "twitch_stream_id", "stream_started_at"} <= cols
    Store(db)  # idempotent


def test_chat_kept_only_while_recording():
    class R:
        live = False

    recs, buf = {"buster": R()}, []
    keep_chat_row({"channel": "buster"}, recs, buf)
    keep_chat_row({"channel": "other"}, recs, buf)
    recs["buster"].live = True
    keep_chat_row({"channel": "buster"}, recs, buf)
    assert buf == [{"channel": "buster"}]
