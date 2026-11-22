"""Health check for the recorder (owner 2026-11-21: the Pi's HDD dropped off at 16:09 and nothing
was recorded all night without anyone noticing).

  uv run python -m clipper.health            # print status, exit 1 on problems
  uv run python -m clipper.health --notify   # + Telegram when problems start / repeat / clear

On the Pi a systemd timer runs `--notify` every 5 min (deploy/pi/clipper-health.timer). It is
deliberately NOT tied to the HDD mount, so it still runs when the disk is gone. The feed's /health
returns the same checks so the Mac (mirror + review page) can show them too.
"""

import argparse
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from clipper import notify
from clipper.config import REPO_ROOT, load_config

log = logging.getLogger("clipper.health")
STATE = REPO_ROOT / "data/health_state.json"  # alert bookkeeping
STATUS = REPO_ROOT / "data/health_status.json"  # last check, served by the feed
UNITS = ("clipper-recorder", "clipper-feed")
STALL_S = 600  # an open recording whose newest segment hasn't grown for 10 min is stuck
LIVE_GRACE_S = 300  # a stream live this long on Twitch must have an open recording
REPEAT_S = 2 * 3600  # remind while still broken


def _writable(d: Path) -> str | None:
    if not d.is_dir():
        return f"video folder missing: {d} (HDD unplugged / not mounted?)"
    probe = d / ".health_probe"
    try:
        probe.write_bytes(b"ok")
        probe.unlink()
    except OSError as e:
        return f"video folder not writable: {d} ({e.strerror or e})"
    return None


def _units(units) -> list[str]:
    if not shutil.which("systemctl"):
        return []
    out = []
    for u in units:
        r = subprocess.run(["systemctl", "is-active", u], capture_output=True, text=True)
        if r.stdout.strip() != "active":
            out.append(f"service {u} is {r.stdout.strip() or 'unknown'}")
    return out


def _power() -> list[str]:
    """Pi under-voltage (vcgencmd get_throttled). A bus-powered HDD + weak PSU drops off USB."""
    if not shutil.which("vcgencmd"):
        return []
    r = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True)
    try:
        bits = int(r.stdout.strip().split("=")[1], 16)
    except (IndexError, ValueError):
        return []
    if bits & 0x1:
        return ["Pi under-voltage NOW (power supply / HDD drawing too much)"]
    if bits & 0x10000:
        return ["Pi had under-voltage since boot (power supply / HDD drawing too much)"]
    return []


def _open_recordings(db: Path) -> dict[str, dict]:
    """login -> newest open recording with the mtime of its newest segment file."""
    if not db.exists():
        return {}
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT r.id, r.login, r.started_epoch, "
            " (SELECT path FROM segments s WHERE s.recording_id=r.id"
            "  ORDER BY start_epoch DESC LIMIT 1) AS seg "
            "FROM recordings r WHERE r.ended_epoch IS NULL ORDER BY r.started_epoch"
        ).fetchall()
    finally:
        conn.close()
    out = {}
    for r in rows:
        seg = Path(r["seg"]) if r["seg"] else None
        mtime = seg.stat().st_mtime if seg and seg.exists() else None
        out[r["login"]] = {"id": r["id"], "started": r["started_epoch"], "seg_mtime": mtime}
    return out


def _twitch_live(logins: list[str]) -> dict[str, float] | None:
    """login -> stream start epoch for streamers live on Twitch; None if Helix isn't usable."""
    from clipper.twitch.helix import HelixClient, HelixError
    from clipper.twitch.vod import parse_rfc3339

    try:
        h = HelixClient.from_env()
        users = h.get_users(logins)
        live = h.get_streams([u["id"] for u in users.values()])
    except (HelixError, OSError, KeyError, ValueError) as e:
        log.warning("helix unavailable: %s", e)
        return None
    return {
        s.login.lower(): parse_rfc3339(s.started_at) if s.started_at else time.time()
        for s in live.values()
    }


def check(config: str | Path, now: float | None = None, units=UNITS, twitch=_twitch_live) -> dict:
    now = now or time.time()
    cfg = load_config(config)
    rec = cfg.recorder
    problems = []
    if p := _writable(rec.data_dir):
        problems.append(p)
    else:
        free = shutil.disk_usage(rec.data_dir).free / 1e9
        if free < rec.min_free_gb:
            problems.append(f"low disk: {free:.0f} GB free in {rec.data_dir}")
    problems += _units(units) + (_power() if units else [])
    open_recs = _open_recordings(rec.db_path)
    for login, r in open_recs.items():
        last = r["seg_mtime"] or r["started"]
        if now - last > STALL_S:
            problems.append(
                f"{login}: recording stalled, no new video for {(now - last) / 60:.0f} min"
            )
    logins = [s.login for s in cfg.streamers]
    live = twitch(logins) if twitch else None
    if live is not None:
        for login, since in live.items():
            if login in logins and login not in open_recs and now - since > LIVE_GRACE_S:
                problems.append(
                    f"{login}: live on Twitch for {(now - since) / 60:.0f} min but NOT recorded"
                )
    return {
        "ok": not problems,
        "problems": problems,
        "recording": sorted(open_recs),
        "live": sorted(live) if live is not None else None,
        "checked_at": now,
    }


def alert(status: dict, state_path: Path = STATE, send=notify.send) -> str | None:
    """Telegram on change: problems appear, change, repeat every 2 h, or clear. Returns the text."""
    try:
        st = json.loads(state_path.read_text())
    except (OSError, ValueError):
        st = {}
    now, probs = status["checked_at"], status["problems"]
    prev = st.get("problems", [])
    msg = None
    if probs and (probs != prev or now - st.get("alerted", 0) > REPEAT_S):
        since = st.get("since") if prev else now
        mins = (now - (since or now)) / 60
        head = "⚠️ Recorder problem" + (f" (for {mins:.0f} min)" if mins >= 1 else "")
        msg = head + ":\n" + "\n".join(f"• {p}" for p in probs)
        st = {"problems": probs, "since": since or now, "alerted": now}
    elif not probs and prev:
        mins = (now - st.get("since", now)) / 60
        msg = f"✅ Recorder OK again (was broken {mins:.0f} min). Recording: " + (
            ", ".join(status["recording"]) or "nobody live"
        )
        st = {}
    if msg:
        send(msg, silent=False)
        state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(st))
        os.replace(tmp, state_path)
    return msg


def write_status(status: dict, path: Path = STATUS) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(status, ensure_ascii=False))
    os.replace(tmp, path)


def read_status(path: Path = STATUS) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("--config", default=str(REPO_ROOT / "config/streamers.yaml"))
    ap.add_argument("--notify", action="store_true", help="Telegram on problems / recovery")
    args = ap.parse_args()
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    status = check(args.config)
    if args.notify:
        alert(status)
    write_status(status)
    print(json.dumps(status, ensure_ascii=False, indent=2))
    sys.exit(0 if status["ok"] else 1)


if __name__ == "__main__":
    main()


# --- Mac side: the mirror records what it sees of the Pi; the review page shows it -----------
PI_DOWN_S = 600  # Pi feed unreachable this long -> Telegram from the Mac


def mac_watch(store, reachable: bool, health: dict | None, now: float, send=notify.send):
    """Called by the mirror ~every minute. Keeps kv 'pi.health' for the review page and alerts
    when the Pi itself is gone (powered off, network, feed dead because the HDD dropped)."""
    try:
        prev = json.loads(store.kv_get("pi.health", "{}"))
    except ValueError:
        prev = {}
    st = {
        "reachable": reachable,
        "last_ok": now if reachable else prev.get("last_ok"),
        "down_since": None if reachable else (prev.get("down_since") or now),
        "alerted": prev.get("alerted", False) and not reachable,
        "health": health if reachable else prev.get("health"),
        "seen_at": now,
    }
    if not reachable and not st["alerted"] and now - st["down_since"] > PI_DOWN_S:
        send(
            f"⚠️ Pi feed unreachable for {(now - st['down_since']) / 60:.0f} min — Pi off, "
            "network, or HDD dropped (feed stops without it). Nothing reaches the Mac.",
            silent=False,
        )
        st["alerted"] = True
    elif reachable and prev.get("alerted"):
        send(f"✅ Pi feed reachable again (down {(now - prev['down_since']) / 60:.0f} min)")
    store.kv_set("pi.health", json.dumps(st, ensure_ascii=False))
    return st


def summary(store, now: float | None = None) -> dict:
    """For the review page: ok + human-readable problems."""
    now = now or time.time()
    try:
        st = json.loads(store.kv_get("pi.health", "{}"))
    except ValueError:
        st = {}
    problems = []
    if not st:
        problems.append("no health data yet (mirror not running?)")
    else:
        if now - st.get("seen_at", 0) > 600:
            problems.append(f"mirror silent for {(now - st['seen_at']) / 60:.0f} min (Mac asleep?)")
        if not st.get("reachable"):
            mins = (now - (st.get("down_since") or now)) / 60
            problems.append(f"Pi unreachable for {mins:.0f} min")
        h = st.get("health")
        if h is None and st.get("reachable"):
            problems.append("Pi health timer not installed (bash deploy/pi/update.sh)")
        elif h:
            if now - h.get("checked_at", 0) > 900:
                problems.append(f"Pi health check stale ({(now - h['checked_at']) / 60:.0f} min)")
            problems += h.get("problems", [])
    return {
        "ok": not problems,
        "problems": problems,
        "recording": (st.get("health") or {}).get("recording", []),
        "live": (st.get("health") or {}).get("live"),
    }
