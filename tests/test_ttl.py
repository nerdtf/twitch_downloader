from pathlib import Path

from clipper.record.ttl import SegRow, run_retention, select_for_deletion
from clipper.store import Store

H = 3600
GB = 10**9


def seg(i, login="a", start=0.0, dur=300.0, size=GB, pinned=False):
    return SegRow(i, login, f"/x/{i}.ts", start, dur, size, pinned)


def test_ttl_respects_per_streamer_hours_pinned_and_open_segments():
    now = 100 * H
    rows = [
        seg(1, "a", start=now - 13 * H),  # older than a's 12h -> delete
        seg(2, "b", start=now - 13 * H),  # b keeps 48h -> keep
        seg(3, "a", start=now - 20 * H, pinned=True),  # pinned -> keep
        seg(4, "a", start=now - 20 * H, dur=None),  # still writing -> keep
        seg(5, "a", start=now - 1 * H),  # fresh -> keep
    ]
    out = select_for_deletion(rows, now, {"a": 12, "b": 48}, 24, free_bytes=500 * GB,
                              min_free_bytes=25 * GB)  # fmt: skip
    assert [(r.id, why) for r, why in out] == [(1, "ttl")]


def test_disk_guard_deletes_oldest_until_enough_free():
    now = 100 * H
    rows = [seg(i, start=now - (10 - i) * 600) for i in range(1, 6)]  # all fresh, 1 GB each
    out = select_for_deletion(rows, now, {"a": 12}, 12, free_bytes=22 * GB,
                              min_free_bytes=25 * GB)  # fmt: skip
    assert [(r.id, why) for r, why in out] == [(1, "disk_guard"), (2, "disk_guard"),
                                                (3, "disk_guard")]  # fmt: skip


def test_run_retention_deletes_files_and_marks_rows(tmp_path: Path):
    data = tmp_path / "rec"
    (data / "a").mkdir(parents=True)
    store = Store(tmp_path / "t.db")
    rid = store.start_recording("a", data / "a", 0)
    old, new = data / "a" / "old.ts", data / "a" / "new.ts"
    for p, start in ((old, 0.0), (new, 99 * H)):
        p.write_bytes(b"x" * 10)
        store.add_segment(rid, "a", p, start)
        store.finish_segment(p, 300.0, 10)
    outside = tmp_path / "outside.ts"
    outside.write_bytes(b"x")
    store.add_segment(rid, "a", outside, 0.0)
    store.finish_segment(outside, 300.0, 1)

    deleted = run_retention(store, data, {"a": 12}, 12, min_free_gb=0, now=100 * H)
    assert {Path(r.path).name for r, _ in deleted} == {"old.ts", "outside.ts"}
    assert not old.exists() and new.exists()
    assert outside.exists()  # never deletes outside data_dir
    left = {Path(r["path"]).name for r in store.live_segments()}
    assert left == {"new.ts", "outside.ts"}
