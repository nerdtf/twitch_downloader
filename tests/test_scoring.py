import numpy as np

from clipper.chat.features import chat_signals
from clipper.chat.lexicon import Lexicon
from clipper.scoring import timeline as tl
from clipper.scoring.stage1 import Signals, load_scoring_config, stage1

LEX = Lexicon.load()
CFG = load_scoring_config()


def test_lexicon_laugh_hype_ignore():
    assert LEX.classify("ахахахах") == (True, False)
    assert LEX.classify("KEKW KEKW")[0]
    assert LEX.classify("ору с него)))")[0]
    assert LEX.classify("ОГО вот это клип")[1]
    assert LEX.classify("за что?") == (False, False)  # the old regex matched «за»
    assert LEX.is_ignored("Nightbot", "hi") and LEX.is_ignored("vasya", "!GTA6")
    assert not LEX.is_ignored("vasya", "GTA6 when")


def test_timeline_helpers():
    x = np.array([0, 1, 2, 3, 4], np.float32)
    assert tl.forward_sum(x, 2).tolist() == [1, 3, 5, 7, 4]
    assert tl.forward_max(x, 2).tolist() == [1, 2, 3, 4, 4]
    assert tl.shift(x, 2).tolist() == [2, 3, 4, 0, 0]
    z = tl.robust_z(np.r_[np.full(100, 10.0), 40.0].astype(np.float32), 60, floor=1.0)
    assert z[-1] > 20 and abs(z[50]) < 1e-6
    assert tl.pick_peaks(np.array([0, 5, 0, 0, 4, 0, 9, 0], np.float32), min_gap=3, k=5) == [6, 1]


def synthetic_chat(n=1800, burst_at=900, base_rate=2, laugh_burst=40, seed=0):
    rng = np.random.default_rng(seed)
    rows = []
    for t in range(n):
        for j in range(rng.poisson(base_rate)):
            rows.append(
                {
                    "sent_ms": (1000 + t) * 1000 + j,
                    "kind": "msg",
                    "login": f"u{rng.integers(500)}",
                    "user_id": None,
                    "text": rng.choice(["привет", "что за игра", "go go"]),
                }
            )
    for j in range(laugh_burst):  # laughter 6-10 s after the event (chat lag)
        t = burst_at + 6 + j % 5
        rows.append(
            {
                "sent_ms": (1000 + t) * 1000 + 500 + j,
                "kind": "msg",
                "login": f"l{j}",
                "user_id": None,
                "text": "АХАХАХАХ KEKW",
            }
        )
    return rows


def test_stage1_finds_chat_laugh_burst_and_agreement():
    n = 1800
    chat = chat_signals(synthetic_chat(n), 1000, 1000 + n, LEX)
    audio = np.full(n, -30.0, np.float32)
    audio[898:903] = -8.0  # loud moment at the event
    cands = stage1(Signals(t0=1000, n=n, audio_db=audio, chat=chat), CFG, k=5)
    top = cands[0]
    assert abs(top.t - 900) <= 6
    assert {"laugh", "audio"} <= set(top.fired)
    assert top.chat_stats["laugh"] >= 30


def test_copypasta_is_penalised():
    n = 1800
    rows = synthetic_chat(n, laugh_burst=0)
    rows += [
        {
            "sent_ms": (1000 + 906) * 1000 + j,
            "kind": "msg",
            "login": f"c{j}",
            "user_id": None,
            "text": "ПРИЗ ПРИЗ ПРИЗ",
        }
        for j in range(80)
    ]
    chat = chat_signals(rows, 1000, 1000 + n, LEX)
    cands = stage1(Signals(t0=1000, n=n, chat=chat), CFG, k=40)
    burst = min(cands, key=lambda c: abs(c.t - 900))
    assert abs(burst.t - 900) <= 10 and "copypasta" in burst.penalties
    assert cands.index(burst) > 0  # spam burst does not win


def test_dead_air_never_scores():
    n = 1200
    audio = np.full(n, -25.0, np.float32)
    audio[600:700] = -85.0  # silence
    cands = stage1(Signals(t0=0, n=n, audio_db=audio), CFG, k=10)
    assert all(not 600 <= c.t < 700 for c in cands if c.score > 0)


def test_lexicon_ignore_regex_and_reaction_swears():
    assert LEX.is_ignored("viewer", "хочу плойку")
    assert LEX.is_ignored("viewer", "ку")
    assert not LEX.is_ignored("viewer", "ку ахахах это жесть")
    assert LEX.classify("ахуеть")[1] and LEX.classify("ЕБААААТЬ")[1]


def test_lexicon_w_chant_and_tech_talk():
    assert LEX.classify("WW ͏")[1] and LEX.classify("wwww")[1]
    assert not LEX.classify("wow ww nice")[0]
    assert LEX.is_ignored("viewer", "сделай звук громче")
