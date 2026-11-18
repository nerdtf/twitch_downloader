import pytest

from clipper.twitch.helix import HelixClient, HelixError
from tests.fakes import FakeResponse, FakeSession

TOKEN = FakeResponse(200, {"access_token": "tok", "expires_in": 3600})
STREAM = {
    "id": "s1",
    "user_id": "1",
    "user_login": "buster",
    "title": "GTA VI",
    "game_name": "GTA VI",
    "started_at": "2026-11-19T00:00:00Z",
    "viewer_count": 50000,
}


def client(replies):
    session = FakeSession(replies)
    return HelixClient("cid", "secret", session=session), session


def test_token_is_cached_and_streams_parsed():
    c, s = client(
        {("POST", "/token"): [TOKEN], ("GET", "/streams"): [FakeResponse(200, {"data": [STREAM]})]}
    )
    out = c.get_streams(["1", "2"])
    c.get_streams(["1"])
    assert list(out) == ["1"] and out["1"].game_name == "GTA VI" and out["1"].viewer_count == 50000
    assert sum(1 for m, *_ in s.calls if m == "POST") == 1
    get = next(call for call in s.calls if call[0] == "GET")
    assert get[3]["Authorization"] == "Bearer tok" and get[3]["Client-Id"] == "cid"


def test_401_refreshes_token_once():
    c, s = client(
        {
            ("POST", "/token"): [TOKEN],
            ("GET", "/users"): [
                FakeResponse(401, {}),
                FakeResponse(200, {"data": [{"login": "a", "id": "9"}]}),
            ],
        }
    )
    assert c.get_users(["a"]) == {"a": {"login": "a", "id": "9"}}
    assert sum(1 for m, *_ in s.calls if m == "POST") == 2


def test_errors_raise_instead_of_reporting_offline():
    c, _ = client({("POST", "/token"): [TOKEN], ("GET", "/streams"): [FakeResponse(503, {})]})
    with pytest.raises(HelixError):
        c.get_streams(["1"])


def test_missing_credentials():
    with pytest.raises(HelixError):
        HelixClient("", "")


def test_clips_pagination():
    page1 = {
        "data": [
            {
                "id": "c1",
                "broadcaster_id": "1",
                "video_id": "v",
                "vod_offset": 120,
                "duration": 30,
                "view_count": 900,
                "created_at": "x",
            }
        ],
        "pagination": {"cursor": "next"},
    }
    page2 = {
        "data": [
            {
                "id": "c2",
                "broadcaster_id": "1",
                "video_id": "",
                "vod_offset": None,
                "duration": 20,
                "view_count": 5,
                "created_at": "y",
            }
        ],
        "pagination": {},
    }
    c, s = client(
        {
            ("POST", "/token"): [TOKEN],
            ("GET", "/clips"): [FakeResponse(200, page1), FakeResponse(200, page2)],
        }
    )
    clips = c.get_clips("1", "2026-11-01T00:00:00Z")
    assert [(x.id, x.vod_offset, x.video_id) for x in clips] == [
        ("c1", 120, "v"),
        ("c2", None, None),
    ]
