"""Helix-based live status for all recorded streamers (one API call per poll).

Helix is the fast path; the recorder's own open-attempt every `poll_seconds` stays as fallback, so
a Helix outage or missing keys never stops recording.
"""

import asyncio
import contextlib
import logging

from clipper import notify
from clipper.twitch.helix import HelixClient, StreamInfo

log = logging.getLogger(__name__)


class HelixWatcher:
    def __init__(self, client: HelixClient | None, logins: list[str], poll_seconds: float):
        self.client = client
        self.logins = [lg.lower() for lg in logins]
        self.poll_seconds = poll_seconds
        self.went_live = {lg: asyncio.Event() for lg in self.logins}
        self._info: dict[str, StreamInfo | None] = dict.fromkeys(self.logins)
        self._user_ids: dict[str, str] = {}
        self.healthy = False
        self._error_reported = False

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def is_live(self, login: str) -> bool | None:
        """True/False from Helix, or None when Helix is disabled/unhealthy (unknown)."""
        if not self.healthy:
            return None
        return self._info.get(login) is not None

    def info(self, login: str) -> StreamInfo | None:
        return self._info.get(login)

    def user_id(self, login: str) -> str | None:
        return self._user_ids.get(login)

    def apply(self, streams: dict[str, StreamInfo]) -> list[str]:
        """Update state from a get_streams() result; return logins that just went live."""
        by_login = {s.login.lower(): s for s in streams.values()}
        went = []
        for lg in self.logins:
            new = by_login.get(lg)
            if new is not None and self._info.get(lg) is None:
                went.append(lg)
                self.went_live[lg].set()
            self._info[lg] = new
        return went

    async def poll_once(self) -> None:
        if not self._user_ids:
            users = await asyncio.to_thread(self.client.get_users, self.logins)
            self._user_ids = {lg: u["id"] for lg, u in users.items()}
            missing = set(self.logins) - set(self._user_ids)
            if missing:
                log.warning("Helix: unknown logins %s", sorted(missing))
        streams = await asyncio.to_thread(self.client.get_streams, list(self._user_ids.values()))
        for lg in self.apply(streams):
            log.info("%s: live via helix (%s)", lg, self._info[lg].game_name)

    async def run(self, stop: asyncio.Event) -> None:
        if not self.enabled:
            log.info("Helix disabled (no TWITCH_CLIENT_ID/SECRET); using open-attempt polling only")
            return
        while not stop.is_set():
            try:
                await self.poll_once()
                if not self.healthy:
                    log.info("Helix live check OK (every %ss)", self.poll_seconds)
                self.healthy, self._error_reported = True, False
            except Exception as e:  # network, HTTP, JSON: never crash the recorder
                self.healthy = False
                log.warning("Helix poll failed: %s", e)
                if not self._error_reported:
                    notify.send(f"⚠️ Helix poll failing, fallback polling active: {e}")
                    self._error_reported = True
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=self.poll_seconds)
