import pytest

from clipper.config import ConfigError, load_config
from clipper.store import Store


def write(tmp_path, text):
    p = tmp_path / "s.yaml"
    p.write_text(text, encoding="utf-8")
    return p


def test_repo_config_loads():
    cfg = load_config("config/streamers.yaml")
    assert cfg.recorder.segment_seconds == 300


def test_defaults_overrides_and_disabled(tmp_path):
    cfg = load_config(
        write(
            tmp_path,
            """
defaults: {quality: "720p60", ttl_hours: 6}
recorder: {data_dir: rec}
streamers:
  - login: Alpha
  - {login: beta, quality: best, ttl_hours: 48}
  - {login: gamma, enabled: false}
  - delta_1
""",
        ),
        base=tmp_path,
    )
    assert [s.login for s in cfg.streamers] == ["alpha", "beta", "delta_1"]
    assert cfg.streamers[0].quality == "720p60" and cfg.streamers[0].ttl_hours == 6
    assert cfg.streamers[1].quality == "best" and cfg.streamers[1].ttl_hours == 48
    assert cfg.recorder.data_dir == tmp_path / "rec"


@pytest.mark.parametrize(
    "body", ["streamers: [{login: 'bad login!'}]", "streamers: [a_b_c, A_B_C]"]
)
def test_invalid_config(tmp_path, body):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, body), base=tmp_path)


def test_store_chat_and_crash_recovery(tmp_path):
    st = Store(tmp_path / "x.db")
    st.insert_chat([{"channel": "c", "sent_ms": 1, "recv_ms": 2, "kind": "msg", "text": "hi"}])
    assert st.conn.execute("SELECT text FROM chat").fetchone()[0] == "hi"
    rid = st.start_recording("c", tmp_path, 0)
    st.add_segment(rid, "c", tmp_path / "s.ts", 0)
    assert st.recover_after_crash(10) == 1
    assert st.conn.execute("SELECT exit_reason FROM recordings").fetchone()[0] == "crash"
    assert st.live_segments()[0]["duration"] == -1


def test_telegram_errors_do_not_log_the_token(monkeypatch, caplog):
    import requests

    from clipper import notify

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123:SECRETSECRET")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")

    def boom(url, **kw):
        raise requests.ConnectionError(f"Max retries exceeded with url: {url}")

    monkeypatch.setattr(notify.requests, "post", boom)
    notify.send("x")
    assert "SECRETSECRET" not in caplog.text
