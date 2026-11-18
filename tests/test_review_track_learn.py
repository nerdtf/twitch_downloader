import json

from clipper.learn.__main__ import rows
from clipper.review.__main__ import sync
from clipper.store import Store
from clipper.track.__main__ import due, post, views


def seed(store):
    store.conn.execute(
        "INSERT INTO candidates(id, source, login, event_epoch, start_epoch, end_epoch, stage1,"
        " features, created_epoch) VALUES (7,'vod:1','buster',100,90,120,0.5,?,0)",
        (json.dumps({"families": {"audio": 2.5}}),),
    )


def test_review_sync_records_labels(tmp_path):
    store = Store(tmp_path / "x.db")
    seed(store)
    d = tmp_path / "clips/rejected/buster/00007_20261119-2100"
    d.mkdir(parents=True)
    (d / "note.txt").write_text("политика", encoding="utf-8")
    assert sync(store, tmp_path / "clips") == {"approved": 0, "rejected": 1}
    lab = store.conn.execute("SELECT decision, reason FROM labels WHERE candidate_id=7").fetchone()
    assert tuple(lab) == ("rejected", "политика")
    assert (tmp_path / "clips/index.html").exists()


def test_tracker_due_and_learning_rows(tmp_path):
    store = Store(tmp_path / "x.db")
    seed(store)
    post(store, 7, "https://tiktok.com/@me/video/1", "tiktok", now=0)
    assert any("@24h" in x for x in due(store, now=25 * 3600))
    views(store, "https://tiktok.com/@me/video/1", 120_000, now=24 * 3600)
    assert not any("@24h" in x for x in due(store, now=25 * 3600))
    r = rows(store)[0]
    assert r["audio"] == 2.5 and r["max_views"] == 120_000
