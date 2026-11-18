"""Twitch Helix API client (official API, app access token via client credentials).

Usage: `uv run python -m clipper.twitch.helix --check <login>` prints a health line, never secrets.
"""

import argparse
import logging
import os
import time
from dataclasses import dataclass

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from clipper.env import load_env

log = logging.getLogger(__name__)

API = "https://api.twitch.tv/helix"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"


class HelixError(RuntimeError):
    pass


@dataclass(frozen=True)
class StreamInfo:
    user_id: str
    login: str
    stream_id: str
    title: str
    game_name: str
    started_at: str
    viewer_count: int


@dataclass(frozen=True)
class Clip:
    id: str
    broadcaster_id: str
    video_id: str | None
    vod_offset: (
        int | None
    )  # seconds into the VOD where the clip ENDS (Twitch docs say start; verified)
    duration: float
    view_count: int
    created_at: str
    title: str
    url: str


def _session() -> requests.Session:
    s = requests.Session()
    retry = Retry(total=3, backoff_factor=1.5, status_forcelist=(500, 502, 503, 504))
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s


class HelixClient:
    def __init__(self, client_id: str, client_secret: str, session: requests.Session | None = None):
        if not client_id or not client_secret:
            raise HelixError("TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET not set")
        self._id, self._secret = client_id, client_secret
        self._http = session or _session()
        self._token: str | None = None
        self._token_expiry = 0.0

    @classmethod
    def from_env(cls) -> "HelixClient":
        load_env()
        return cls(
            os.environ.get("TWITCH_CLIENT_ID", ""), os.environ.get("TWITCH_CLIENT_SECRET", "")
        )

    # --- auth ---
    def _get_token(self, force: bool = False) -> str:
        if not force and self._token and time.time() < self._token_expiry:
            return self._token
        r = self._http.post(
            TOKEN_URL,
            data={
                "client_id": self._id,
                "client_secret": self._secret,
                "grant_type": "client_credentials",
            },
            timeout=20,
        )
        if not r.ok:
            raise HelixError(f"token request failed: HTTP {r.status_code}")
        body = r.json()
        self._token = body["access_token"]
        self._token_expiry = time.time() + int(body.get("expires_in", 3600)) - 60
        return self._token

    def _get(self, path: str, params) -> dict:
        for attempt in (0, 1):
            headers = {
                "Client-Id": self._id,
                "Authorization": "Bearer " + self._get_token(attempt == 1),
            }
            r = self._http.get(f"{API}/{path}", params=params, headers=headers, timeout=20)
            if r.status_code == 401 and attempt == 0:
                continue  # token revoked/expired early -> refresh once
            if not r.ok:
                raise HelixError(f"GET {path}: HTTP {r.status_code} {r.text[:200]}")
            return r.json()
        raise HelixError(f"GET {path}: unauthorized after token refresh")

    # --- endpoints ---
    def get_users(self, logins: list[str]) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for i in range(0, len(logins), 100):
            data = self._get("users", [("login", lg) for lg in logins[i : i + 100]])["data"]
            out.update({u["login"]: u for u in data})
        return out

    def get_streams(self, user_ids: list[str]) -> dict[str, StreamInfo]:
        """Live streams keyed by user_id. Users missing from the result are offline."""
        out: dict[str, StreamInfo] = {}
        for i in range(0, len(user_ids), 100):
            params = [("user_id", u) for u in user_ids[i : i + 100]] + [("first", "100")]
            for s in self._get("streams", params)["data"]:
                out[s["user_id"]] = StreamInfo(
                    user_id=s["user_id"],
                    login=s["user_login"],
                    stream_id=s["id"],
                    title=s.get("title", ""),
                    game_name=s.get("game_name", ""),
                    started_at=s.get("started_at", ""),
                    viewer_count=int(s.get("viewer_count", 0)),
                )
        return out

    def get_videos(self, user_id: str, first: int = 20) -> list[dict]:
        return self._get("videos", {"user_id": user_id, "type": "archive", "first": first})["data"]

    def get_video(self, video_id: str) -> dict | None:
        data = self._get("videos", {"id": video_id})["data"]
        return data[0] if data else None

    def get_clips(
        self, broadcaster_id: str, started_at: str, ended_at: str | None = None, max_pages: int = 10
    ) -> list[Clip]:
        """Viewer-made clips created in [started_at, ended_at] (RFC3339)."""
        clips: list[Clip] = []
        cursor = None
        for _ in range(max_pages):
            params = {"broadcaster_id": broadcaster_id, "started_at": started_at, "first": 100}
            if ended_at:
                params["ended_at"] = ended_at
            if cursor:
                params["after"] = cursor
            body = self._get("clips", params)
            clips.extend(_clip(c) for c in body["data"])
            cursor = (body.get("pagination") or {}).get("cursor")
            if not cursor:
                break
        return clips


def _clip(c: dict) -> Clip:
    return Clip(
        id=c["id"],
        broadcaster_id=c["broadcaster_id"],
        video_id=c.get("video_id") or None,
        vod_offset=c.get("vod_offset"),
        duration=float(c.get("duration", 0)),
        view_count=int(c.get("view_count", 0)),
        created_at=c["created_at"],
        title=c.get("title", ""),
        url=c.get("url", ""),
    )


def _cli() -> None:
    ap = argparse.ArgumentParser(description="Helix health check")
    ap.add_argument("--check", metavar="LOGIN", required=True)
    login = ap.parse_args().check.lower()
    client = HelixClient.from_env()
    user = client.get_users([login]).get(login)
    if not user:
        raise SystemExit(f"Helix OK, but user {login!r} not found")
    live = client.get_streams([user["id"]]).get(user["id"])
    print(
        f"Helix OK: {login} id={user['id']} live={bool(live)}"
        + (f" game={live.game_name!r}" if live else "")
    )


if __name__ == "__main__":
    _cli()
