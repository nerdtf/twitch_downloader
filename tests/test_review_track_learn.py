import json

from clipper.learn.__main__ import rows
from clipper.review.queue import sync
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


def test_review_server_decide_requires_note_and_moves(tmp_path):
    import pytest

    from clipper.review.server import decide, list_pending

    store = Store(tmp_path / "x.db")
    seed(store)
    root = tmp_path / "clips"
    d = root / "pending/buster/00007_20261119-2100"
    d.mkdir(parents=True)
    meta = {"login": "buster", "score": 50, "duration": 30, "start_epoch": 90, "end_epoch": 120,
            "event_utc": "1970-01-01T00:01:40+00:00"}  # fmt: skip
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    store.conn.execute(
        "INSERT INTO chat(channel, sent_ms, recv_ms, kind, login, text) VALUES"
        " ('buster', 101000, 101000, 'msg', 'v', 'ахахах')"
    )
    [item] = list_pending(store, root)
    assert item["event_offset"] == 10.0 and item["chat"][0]["laugh"]
    with pytest.raises(ValueError):
        decide(store, item["dir"], "approved", " ", root)
    with pytest.raises(ValueError):
        decide(store, "../../etc", "approved", "ok", root)
    dst = decide(store, item["dir"], "approved", "смешно", root)
    assert dst == root / "approved/buster/00007_20261119-2100" and not d.exists()
    assert (dst / "note.txt").read_text(encoding="utf-8").strip() == "смешно"
    lab = store.conn.execute("SELECT decision, reason FROM labels WHERE candidate_id=7").fetchone()
    assert tuple(lab) == ("approved", "смешно")
    from clipper.review.server import undo

    with pytest.raises(ValueError):
        undo(store, item["dir"], root)  # still a pending path -> refused
    back = undo(store, "approved/buster/00007_20261119-2100", root)
    assert back == d and d.is_dir() and not (d / "note.txt").exists()
    assert store.conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0] == 0


def test_review_list_pending_limit_and_counts(tmp_path):
    from clipper.review.server import list_pending, pending_counts

    store = Store(tmp_path / "x.db")
    root = tmp_path / "clips"
    for i, (login, score) in enumerate([("a", 10), ("a", 30), ("b", 20)]):
        d = root / f"pending/{login}/0000{i}_20261119-2100"
        d.mkdir(parents=True)
        (d / "meta.json").write_text(json.dumps({"login": login, "score": score}), "utf-8")
    assert [c["score"] for c in list_pending(store, root, limit=2)] == [30, 20]
    assert [c["score"] for c in list_pending(store, root, login="a")] == [30, 10]
    assert pending_counts(root) == {"a": 2, "b": 1}
