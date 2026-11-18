"""SQLite storage (WAL). One connection, used from the asyncio thread only."""

import json
import sqlite3
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS recordings (
    id            INTEGER PRIMARY KEY,
    login         TEXT NOT NULL,
    dir           TEXT NOT NULL,
    started_epoch REAL NOT NULL,
    ended_epoch   REAL,
    exit_reason   TEXT,
    twitch_stream_id  TEXT,
    title             TEXT,
    game_name         TEXT,
    stream_started_at TEXT
);
CREATE TABLE IF NOT EXISTS segments (
    id            INTEGER PRIMARY KEY,
    recording_id  INTEGER NOT NULL REFERENCES recordings(id),
    login         TEXT NOT NULL,
    path          TEXT NOT NULL UNIQUE,
    start_epoch   REAL NOT NULL,   -- wall-clock UTC when ffmpeg opened the file
    duration      REAL,            -- NULL while the segment is still being written
    size_bytes    INTEGER,
    pinned        INTEGER NOT NULL DEFAULT 0,
    deleted_epoch REAL
);
CREATE INDEX IF NOT EXISTS segments_login_start ON segments(login, start_epoch);
CREATE TABLE IF NOT EXISTS chat (
    id           INTEGER PRIMARY KEY,
    channel      TEXT NOT NULL,
    sent_ms      INTEGER NOT NULL,  -- tmi-sent-ts (Twitch server time)
    recv_ms      INTEGER NOT NULL,  -- local receive time
    kind         TEXT NOT NULL,     -- msg | usernotice | clearchat | clearmsg
    msg_id       TEXT,              -- USERNOTICE msg-id: sub, resub, subgift, raid, ...
    user_id      TEXT,
    login        TEXT,
    display_name TEXT,
    text         TEXT,
    emotes       TEXT,
    badges       TEXT,
    bits         INTEGER,
    tags         TEXT               -- full tag dict as JSON
);
CREATE INDEX IF NOT EXISTS chat_channel_sent ON chat(channel, sent_ms);
CREATE TABLE IF NOT EXISTS events (
    id     INTEGER PRIMARY KEY,
    login  TEXT NOT NULL,
    epoch  REAL NOT NULL,
    kind   TEXT NOT NULL,           -- ad_break | stall | error | ...
    data   TEXT
);
CREATE INDEX IF NOT EXISTS events_login_epoch ON events(login, epoch);
"""

MIGRATIONS: dict[str, list[tuple[str, str]]] = {
    "recordings": [
        ("twitch_stream_id", "TEXT"),
        ("title", "TEXT"),
        ("game_name", "TEXT"),
        ("stream_started_at", "TEXT"),
    ],
}

CHAT_COLUMNS = (
    "channel",
    "sent_ms",
    "recv_ms",
    "kind",
    "msg_id",
    "user_id",
    "login",
    "display_name",
    "text",
    "emotes",
    "badges",
    "bits",
    "tags",
)


class Store:
    def __init__(self, path: str | Path):
        path = Path(path)
        if str(path) != ":memory:":
            path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(str(path), isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self._migrate()

    def _migrate(self):
        """Add columns introduced after a DB was created (idempotent)."""
        for table, cols in MIGRATIONS.items():
            have = {r[1] for r in self.conn.execute(f"PRAGMA table_info({table})")}
            for name, decl in cols:
                if name not in have:
                    self.conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")

    def close(self):
        self.conn.close()

    # --- recordings ---
    def start_recording(self, login: str, dir_: Path, started_epoch: float) -> int:
        cur = self.conn.execute(
            "INSERT INTO recordings(login, dir, started_epoch) VALUES (?,?,?)",
            (login, str(dir_), started_epoch),
        )
        return cur.lastrowid

    def end_recording(self, rec_id: int, ended_epoch: float, reason: str):
        self.conn.execute(
            "UPDATE recordings SET ended_epoch=?, exit_reason=? WHERE id=?",
            (ended_epoch, reason, rec_id),
        )

    def update_recording_meta(
        self, rec_id: int, stream_id: str, title: str, game_name: str, started_at: str
    ):
        self.conn.execute(
            "UPDATE recordings SET twitch_stream_id=?, title=?, game_name=?, stream_started_at=?"
            " WHERE id=?",
            (stream_id, title, game_name, started_at, rec_id),
        )

    # --- segments ---
    def add_segment(self, rec_id: int, login: str, path: Path, start_epoch: float) -> int:
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO segments(recording_id, login, path, start_epoch)"
            " VALUES (?,?,?,?)",
            (rec_id, login, str(path), start_epoch),
        )
        return cur.lastrowid

    def finish_segment(self, path: Path, duration: float | None, size_bytes: int):
        self.conn.execute(
            "UPDATE segments SET duration=?, size_bytes=? WHERE path=?",
            (duration if duration is not None else -1.0, size_bytes, str(path)),
        )

    def live_segments(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT * FROM segments WHERE deleted_epoch IS NULL ORDER BY start_epoch"
        ).fetchall()

    def mark_deleted(self, seg_id: int, epoch: float):
        self.conn.execute("UPDATE segments SET deleted_epoch=? WHERE id=?", (epoch, seg_id))

    # --- chat / events ---
    def insert_chat(self, rows: list[dict]):
        if not rows:
            return
        placeholders = ",".join("?" * len(CHAT_COLUMNS))
        self.conn.execute("BEGIN")
        self.conn.executemany(
            f"INSERT INTO chat({','.join(CHAT_COLUMNS)}) VALUES ({placeholders})",
            [tuple(r.get(c) for c in CHAT_COLUMNS) for r in rows],
        )
        self.conn.execute("COMMIT")

    def add_event(self, login: str, epoch: float, kind: str, data: dict | str | None = None):
        if isinstance(data, dict):
            data = json.dumps(data, ensure_ascii=False)
        self.conn.execute(
            "INSERT INTO events(login, epoch, kind, data) VALUES (?,?,?,?)",
            (login, epoch, kind, data),
        )

    def recover_after_crash(self, now: float) -> int:
        """Close rows left open by a previous process. Call before recorders start."""
        self.conn.execute(
            "UPDATE recordings SET ended_epoch=?, exit_reason='crash' WHERE ended_epoch IS NULL",
            (now,),
        )
        cur = self.conn.execute("UPDATE segments SET duration=-1 WHERE duration IS NULL")
        return cur.rowcount
