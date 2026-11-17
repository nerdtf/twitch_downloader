"""Anonymous Twitch IRC client with IRCv3 tags (no account or token needed)."""

import asyncio
import contextlib
import json
import logging
import random
import ssl
import time
from collections.abc import Callable
from dataclasses import dataclass, field

log = logging.getLogger(__name__)

HOST, PORT = "irc.chat.twitch.tv", 6697
_TAG_UNESCAPE = {"s": " ", ":": ";", "\\": "\\", "r": "\r", "n": "\n"}


@dataclass
class IrcLine:
    tags: dict[str, str] = field(default_factory=dict)
    prefix: str = ""
    command: str = ""
    params: list[str] = field(default_factory=list)

    @property
    def nick(self) -> str:
        return self.prefix.split("!", 1)[0] if self.prefix else ""


def _unescape(value: str) -> str:
    out, i = [], 0
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            out.append(_TAG_UNESCAPE.get(value[i + 1], value[i + 1]))
            i += 2
        elif ch == "\\":
            i += 1  # trailing lone backslash is dropped per spec
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def parse_tags(raw: str) -> dict[str, str]:
    tags = {}
    for part in raw.split(";"):
        if not part:
            continue
        key, _, value = part.partition("=")
        tags[key] = _unescape(value)
    return tags


def parse_line(line: str) -> IrcLine | None:
    line = line.rstrip("\r\n")
    if not line:
        return None
    msg = IrcLine()
    if line.startswith("@"):
        raw_tags, _, line = line[1:].partition(" ")
        msg.tags = parse_tags(raw_tags)
    if line.startswith(":"):
        msg.prefix, _, line = line[1:].partition(" ")
    trailing = None
    if " :" in line:
        line, _, trailing = line.partition(" :")
    elif line.startswith(":"):
        trailing, line = line[1:], ""
    parts = line.split()
    if not parts:
        return None
    msg.command, msg.params = parts[0].upper(), parts[1:]
    if trailing is not None:
        msg.params.append(trailing)
    return msg


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except ValueError:
        return None


KINDS = {
    "PRIVMSG": "msg",
    "USERNOTICE": "usernotice",
    "CLEARCHAT": "clearchat",
    "CLEARMSG": "clearmsg",
}


def to_chat_row(msg: IrcLine, recv_ms: int) -> dict | None:
    """Convert a parsed line into a `chat` table row, or None for non-chat lines."""
    kind = KINDS.get(msg.command)
    if kind is None or not msg.params or not msg.params[0].startswith("#"):
        return None
    t = msg.tags
    channel = msg.params[0][1:].lower()
    text = msg.params[1] if len(msg.params) > 1 else None
    login = t.get("login") or msg.nick or None
    if kind == "clearchat":  # params[1] is the banned/timed-out user, not a message
        login, text = text, None
    return {
        "channel": channel,
        "sent_ms": _int_or_none(t.get("tmi-sent-ts")) or recv_ms,
        "recv_ms": recv_ms,
        "kind": kind,
        "msg_id": t.get("msg-id") or None,
        "user_id": t.get("user-id") or None,
        "login": login,
        "display_name": t.get("display-name") or None,
        "text": text,
        "emotes": t.get("emotes") or None,
        "badges": t.get("badges") or None,
        "bits": _int_or_none(t.get("bits")),
        "tags": json.dumps(t, ensure_ascii=False) if t else None,
    }


class IrcClient:
    """Joins channels anonymously and calls `on_row` for every chat row. Reconnects forever."""

    def __init__(self, channels: list[str], on_row: Callable[[dict], None]):
        self.channels = [c.lower() for c in channels]
        self.on_row = on_row
        self.connected = asyncio.Event()

    async def run(self, stop: asyncio.Event):
        backoff = 1.0
        while not stop.is_set():
            try:
                await self._session(stop)
                backoff = 1.0
            except (OSError, asyncio.IncompleteReadError, TimeoutError) as e:
                log.warning("IRC connection error: %s", e)
            self.connected.clear()
            if stop.is_set():
                break
            delay = backoff + random.random()
            log.info("IRC reconnecting in %.1fs", delay)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=delay)
            backoff = min(backoff * 2, 60)

    async def _session(self, stop: asyncio.Event):
        reader, writer = await asyncio.open_connection(HOST, PORT, ssl=ssl.create_default_context())

        async def send(line: str):
            writer.write((line + "\r\n").encode())
            await writer.drain()

        try:
            await send("CAP REQ :twitch.tv/tags twitch.tv/commands")
            await send("PASS SCHMOOPIIE")
            await send(f"NICK justinfan{random.randint(10000, 99999)}")
            for i in range(0, len(self.channels), 20):
                await send("JOIN " + ",".join("#" + c for c in self.channels[i : i + 20]))
            self.connected.set()
            log.info("IRC joined %s", ", ".join(self.channels))
            while not stop.is_set():
                read = asyncio.ensure_future(reader.readline())
                stopper = asyncio.ensure_future(stop.wait())
                done, _ = await asyncio.wait(
                    {read, stopper}, timeout=360, return_when=asyncio.FIRST_COMPLETED
                )
                if stopper in done:
                    read.cancel()
                    return
                stopper.cancel()
                if not done:  # Twitch pings every ~5 min; silence means a dead socket
                    read.cancel()
                    raise TimeoutError("no data from IRC for 360s")
                raw = read.result()
                if not raw:
                    raise ConnectionResetError("IRC closed connection")
                msg = parse_line(raw.decode("utf-8", errors="replace"))
                if msg is None:
                    continue
                if msg.command == "PING":
                    await send("PONG :" + (msg.params[-1] if msg.params else "tmi.twitch.tv"))
                elif msg.command == "RECONNECT":
                    raise ConnectionResetError("server requested RECONNECT")
                elif (row := to_chat_row(msg, int(time.time() * 1000))) is not None:
                    self.on_row(row)
        finally:
            writer.close()
