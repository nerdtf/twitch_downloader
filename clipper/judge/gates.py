"""Hard gates (drop + reason) and the final score. Pure functions: easy to test and to tune."""

from dataclasses import dataclass

# Final score weights (CLAUDE.md). Visual is not implemented yet -> renormalised away.
WEIGHTS = {"chat": 0.30, "audio": 0.25, "content": 0.35}
MIN_CLARITY = 4.0
MIN_WORDS_PER_SECOND = 0.6  # below this the streamer is barely talking -> dead air
MIN_GAP_SECONDS = 90.0


@dataclass
class GateResult:
    passed: bool
    reason: str | None = None


def content_score(j: dict) -> float:
    """0..1 from the judge's 0..10 scores. Humor dominates: it is what crosses 100k."""
    return (
        0.4 * j["humor"]
        + 0.25 * j["intensity"]
        + 0.2 * j["setup_payoff"]
        + 0.15 * j["standalone_clarity"]
    ) / 10.0


def gate(j: dict, words_per_second: float, program_expired: bool = False) -> GateResult:
    if j.get("banned_topic"):
        return GateResult(
            False, f"banned_topic: {j.get('banned_reason') or 'politics/conflict/bait'}"
        )
    if not j.get("streamer_is_main", True):
        return GateResult(False, "streamer_not_main")
    if words_per_second < MIN_WORDS_PER_SECOND and j["intensity"] < 6:
        return GateResult(False, f"dead_air ({words_per_second:.2f} words/s)")
    if j["standalone_clarity"] < MIN_CLARITY:
        return GateResult(False, f"low_clarity ({j['standalone_clarity']:.0f}/10)")
    if program_expired:
        return GateResult(False, "program_window_expired")
    return GateResult(True)


def final_score(
    families: dict[str, float],
    judgement: dict,
    penalties: float = 0.0,
    z_cap: float = 6.0,
    fire_z: float = 2.0,
) -> float:
    """100 × weighted(chat, audio, content) × agreement − penalties (×100)."""
    chat_z = max(families.get("chat", 0.0), families.get("laugh", 0.0))
    parts = {"content": content_score(judgement)}
    if "chat" in families or "laugh" in families:
        parts["chat"] = min(max(chat_z, 0.0), z_cap) / z_cap
    if "audio" in families:
        parts["audio"] = min(max(families["audio"], 0.0), z_cap) / z_cap
    total_w = sum(WEIGHTS[k] for k in parts)
    base = sum(WEIGHTS[k] * v for k, v in parts.items()) / total_w
    fired = sum(
        [
            parts["content"] >= 0.6,
            chat_z >= fire_z,
            families.get("audio", 0) >= fire_z,
            families.get("clips", 0) >= 1,
        ]
    )
    agreement = 1.0 if fired >= 2 else 0.6
    return round(100 * base * agreement - 100 * penalties, 2)


def suppress_overlaps(
    items: list[tuple[float, float]], min_gap: float = MIN_GAP_SECONDS
) -> set[int]:
    """items: (event_epoch, score). Returns indices dropped because a higher-scored item is closer
    than min_gap."""
    order = sorted(range(len(items)), key=lambda i: -items[i][1])
    kept: list[int] = []
    dropped: set[int] = set()
    for i in order:
        if any(abs(items[i][0] - items[k][0]) < min_gap for k in kept):
            dropped.add(i)
        else:
            kept.append(i)
    return dropped
