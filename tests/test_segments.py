import os
import time
from datetime import UTC, datetime

from clipper.record.segments import (
    SegmentSpan,
    epoch_from_name,
    locate,
    parse_ad_line,
    segment_start_epoch,
)


def test_epoch_from_name_is_utc(tmp_path):
    p = tmp_path / "20261119T183005Z.ts"
    assert epoch_from_name(p) == datetime(2026, 11, 19, 18, 30, 5, tzinfo=UTC).timestamp()
    assert epoch_from_name(tmp_path / "other.ts") is None


def test_segment_start_prefers_birthtime_when_consistent(tmp_path):
    name = datetime.fromtimestamp(time.time(), UTC).strftime("%Y%m%dT%H%M%SZ.ts")
    p = tmp_path / name
    p.write_bytes(b"x")
    start = segment_start_epoch(p)
    assert abs(start - epoch_from_name(p)) < 2
    if hasattr(os.stat(p), "st_birthtime"):
        assert start == os.stat(p).st_birthtime  # sub-second precision on macOS


def test_segment_start_falls_back_to_name_when_file_copied(tmp_path):
    p = tmp_path / "20200101T000000Z.ts"  # birthtime "now" is far from the name -> trust name
    p.write_bytes(b"x")
    assert segment_start_epoch(p) == epoch_from_name(p)


def test_locate_maps_epoch_into_segment_and_reports_gaps():
    spans = [SegmentSpan("a.ts", 1000.0, 300.0), SegmentSpan("b.ts", 1300.5, 300.0)]
    assert locate(spans, 1000.0) == ("a.ts", 0.0)
    assert locate(spans, 1299.9)[0] == "a.ts"
    assert locate(spans, 1300.2) is None  # gap between segments (e.g. ad break / restart)
    path, off = locate(spans, 1450.5)
    assert path == "b.ts" and abs(off - 150.0) < 1e-9
    assert locate(spans, 999.0) is None


def test_parse_ad_line():
    ad = parse_ad_line("[plugins.twitch][info] Detected advertisement break of 90 seconds")
    assert ad["duration"] == 90.0
    hint = parse_ad_line("[plugins.twitch][info] Waiting for pre-roll ads to finish, be patient")
    assert hint["duration"] is None
    assert parse_ad_line("[cli][info] Opening stream: 1080p60 (hls)") is None
    assert parse_ad_line("[plugins.twitch][info] Will skip ad segments") is None
