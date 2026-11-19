"""Pi feed + Mac mirror end to end on localhost (real HTTP, temp DBs)."""

import threading
import time
from http.server import ThreadingHTTPServer

import pytest
import requests

from clipper.feed import _Handler
from clipper.mirror import Mirror
from clipper.store import Store

TOKEN = "t" * 20


@pytest.fixture
def pi(tmp_path):
    data = tmp_path / "pi/recordings"
    db = tmp_path / "pi/clipper.db"
    store = Store(db)
    handler = type("H", (_Handler,), {"db": db, "data_dir": data, "token": TOKEN})
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield store, data, f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()
    httpd.server_close()


def test_feed_rejects_wrong_token(pi):
    _, _, url = pi
    assert requests.get(f"{url}/sync", timeout=5).status_code == 401
    ok = requests.get(f"{url}/health", headers={"Authorization": f"Bearer {TOKEN}"}, timeout=5)
    assert ok.status_code == 200


def test_mirror_copies_rows_chat_and_growing_segment(pi, tmp_path):
    pstore, pdata, url = pi
    now = time.time()
    rdir = pdata / "zloyn/20261119T100000Z"
    rdir.mkdir(parents=True)
    rid = pstore.start_recording("zloyn", rdir, now - 400)
    pstore.update_recording_meta(rid, "s1", "GTA VI day 2", "Grand Theft Auto VI", "x")
    old, cur = rdir / "a.ts", rdir / "b.ts"
    old.write_bytes(b"A" * 3000)
    pstore.add_segment(rid, "zloyn", old, now - 400)
    pstore.finish_segment(old, 300.0, 3000)
    cur.write_bytes(b"B" * 1000)  # still being written
    pstore.add_segment(rid, "zloyn", cur, now - 100)
    pstore.insert_chat(
        [
            {
                "channel": "zloyn",
                "sent_ms": int(now * 1000),
                "recv_ms": int(now * 1000),
                "kind": "msg",
                "login": "v",
                "text": "ахахах",
            }
        ]
    )

    mac = Store(tmp_path / "mac.db")
    mdata = tmp_path / "mac/recordings"
    m = Mirror(mac, mdata, url, TOKEN)
    res = m.tick()
    assert res["chat"] == 1 and res["bytes"] == 4000

    rec = mac.conn.execute("SELECT * FROM recordings").fetchone()
    assert (
        rec["origin"] == f"pi:{rid}"
        and rec["title"] == "GTA VI day 2"
        and rec["ended_epoch"] is None
    )
    segs = {r["path"]: r for r in mac.conn.execute("SELECT * FROM segments")}
    a, b = mdata / "zloyn/20261119T100000Z/a.ts", mdata / "zloyn/20261119T100000Z/b.ts"
    assert segs[str(a)]["duration"] == 300.0 and a.read_bytes() == b"A" * 3000
    assert segs[str(b)]["duration"] is None and b.stat().st_size == 1000

    # the open segment grows and then closes; the recording ends; more chat arrives
    with cur.open("ab") as fh:
        fh.write(b"C" * 500)
    pstore.finish_segment(cur, 120.0, 1500)
    pstore.end_recording(rid, now, "offline")
    pstore.insert_chat(
        [
            {
                "channel": "zloyn",
                "sent_ms": int(now * 1000) + 1,
                "recv_ms": 0,
                "kind": "msg",
                "login": "w",
                "text": "W",
            }
        ]
    )
    res = m.tick()
    assert res["chat"] == 1 and res["bytes"] == 500
    assert b.read_bytes() == b"B" * 1000 + b"C" * 500
    assert (
        mac.conn.execute("SELECT duration FROM segments WHERE path=?", (str(b),)).fetchone()[0]
        == 120.0
    )
    assert mac.conn.execute("SELECT ended_epoch FROM recordings").fetchone()[0] == now
    assert mac.conn.execute("SELECT COUNT(*) FROM chat").fetchone()[0] == 2
    assert m.tick()["bytes"] == 0  # nothing new -> idempotent


def test_mirror_skips_segments_older_than_backfill(pi, tmp_path):
    pstore, pdata, url = pi
    now = time.time()
    rdir = pdata / "buster/r"
    rdir.mkdir(parents=True)
    rid = pstore.start_recording("buster", rdir, now - 4 * 3600)
    old = rdir / "old.ts"
    old.write_bytes(b"x" * 10)
    pstore.add_segment(rid, "buster", old, now - 4 * 3600)
    pstore.finish_segment(old, 300.0, 10)
    mac = Store(tmp_path / "mac.db")
    m = Mirror(mac, tmp_path / "mac", url, TOKEN, backfill_hours=1)
    assert m.tick()["bytes"] == 0
    assert mac.conn.execute("SELECT COUNT(*) FROM segments").fetchone()[0] == 0


def test_config_env_overrides_for_the_pi(tmp_path, monkeypatch):
    from clipper.config import load_config

    p = tmp_path / "s.yaml"
    p.write_text(
        "defaults: {ttl_hours: 6}\nrecorder: {min_free_gb: 12}\nstreamers: [{login: buster, ttl_hours: 8}, zloyn]\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CLIPPER_TTL_HOURS", "2")
    monkeypatch.setenv("CLIPPER_MIN_FREE_GB", "5")
    monkeypatch.setenv("CLIPPER_DATA_DIR", "/mnt/hdd/clipper/recordings")
    cfg = load_config(p, base=tmp_path)
    assert cfg.recorder.min_free_gb == 5 and {s.ttl_hours for s in cfg.streamers} == {2}
    assert str(cfg.recorder.data_dir) == "/mnt/hdd/clipper/recordings"
