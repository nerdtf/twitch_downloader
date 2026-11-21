"""Recorder health check: HDD gone, stalled recording, live-but-not-recorded, alerts, Mac watch."""

import json
import time

import pytest

from clipper import health
from clipper.store import Store


@pytest.fixture
def setup(tmp_path, monkeypatch):
    data, db = tmp_path / "hdd/recordings", tmp_path / "clipper.db"
    data.mkdir(parents=True)
    monkeypatch.setenv("CLIPPER_DATA_DIR", str(data))
    monkeypatch.setenv("CLIPPER_MIN_FREE_GB", "0")
    cfg = tmp_path / "streamers.yaml"
    cfg.write_text(f"recorder:\n  db_path: {db}\nstreamers:\n  - zloyn\n  - mokrivskyi\n")
    return cfg, data, Store(db)


def test_ok_when_disk_writable_and_recording_live_streamers(setup):
    cfg, data, store = setup
    now = time.time()
    rid = store.start_recording("zloyn", data / "zloyn", now - 1000)
    seg = data / "zloyn/a.ts"
    seg.parent.mkdir()
    seg.write_bytes(b"x")
    store.add_segment(rid, "zloyn", seg, now - 60)
    st = health.check(cfg, now, units=(), twitch=lambda _: {"zloyn": now - 1000})
    assert st["ok"], st["problems"]
    assert st["recording"] == ["zloyn"]


def test_reports_missing_hdd_stall_and_live_not_recorded(setup):
    cfg, data, store = setup
    now = time.time()
    rid = store.start_recording("zloyn", data / "zloyn", now - 3000)
    store.add_segment(rid, "zloyn", data / "zloyn/gone.ts", now - 2000)
    data.rmdir()  # HDD unplugged: mount point empty
    st = health.check(cfg, now, units=(), twitch=lambda _: {"mokrivskyi": now - 3600})
    text = " | ".join(st["problems"])
    assert not st["ok"]
    assert "video folder missing" in text
    assert "zloyn: recording stalled" in text
    assert "mokrivskyi: live on Twitch for 60 min but NOT recorded" in text


def test_just_went_live_gets_grace_and_helix_down_is_not_a_problem(setup):
    cfg, _, _ = setup
    now = time.time()
    assert health.check(cfg, now, units=(), twitch=lambda _: {"zloyn": now - 60})["ok"]
    assert health.check(cfg, now, units=(), twitch=lambda _: None)["ok"]


def test_alert_on_start_repeat_and_recovery_only(tmp_path):
    sent, path = [], tmp_path / "state.json"

    def send(m, silent=True):
        sent.append(m)

    bad = {"problems": ["video folder missing"], "recording": [], "checked_at": 1000.0}
    assert health.alert(bad, path, send)
    assert health.alert({**bad, "checked_at": 1300.0}, path, send) is None  # no spam
    assert "for 122 min" in health.alert({**bad, "checked_at": 1000.0 + 7300}, path, send)
    ok = {"problems": [], "recording": ["zloyn"], "checked_at": 9000.0}
    assert "OK again" in health.alert(ok, path, send) and "zloyn" in sent[-1]
    assert health.alert({**ok, "checked_at": 9300.0}, path, send) is None
    assert len(sent) == 3


def test_mac_watch_alerts_once_when_pi_is_gone_and_summary_shows_it(tmp_path):
    store, sent = Store(tmp_path / "m.db"), []

    def send(m, silent=True):
        sent.append(m)

    good = {"problems": [], "recording": ["zloyn"], "live": ["zloyn"], "checked_at": 100.0}
    health.mac_watch(store, True, good, 100.0, send)
    assert health.summary(store, 110.0)["ok"]
    for t in (200.0, 500.0, 800.0, 1100.0):
        health.mac_watch(store, False, None, t, send)
    assert len(sent) == 1 and "unreachable" in sent[0]
    s = health.summary(store, 1110.0)
    assert not s["ok"] and any("Pi unreachable" in p for p in s["problems"])
    health.mac_watch(store, True, {**good, "checked_at": 1200.0}, 1200.0, send)
    assert "reachable again" in sent[-1] and health.summary(store, 1210.0)["ok"]
    assert json.loads(store.kv_get("pi.health"))["reachable"]


def test_summary_flags_pi_problems_and_silent_mirror(tmp_path):
    store = Store(tmp_path / "m.db")
    bad = {"problems": ["service clipper-recorder is inactive"], "checked_at": 100.0}
    health.mac_watch(store, True, bad, 100.0, lambda *a, **k: None)
    assert health.summary(store, 110.0)["problems"] == ["service clipper-recorder is inactive"]
    assert any("mirror silent" in p for p in health.summary(store, 5000.0)["problems"])
