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
    near = [c for c in cands if abs(c.t - 900) <= 10]
    # message rate has weight 0 now: spam without laughter is not a moment at all; if a peak is
    # found anyway it must carry the copypasta penalty and not win
    if near:
        assert "copypasta" in near[0].penalties and cands.index(near[0]) > 0


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


def test_chat_laugh_beats_loud_but_quiet_chat():
    """Owner: chat decides what is funny — a loud moment chat ignores ranks below a laugh burst."""
    n = 1800
    chat = chat_signals(synthetic_chat(n, burst_at=600), 1000, 1000 + n, LEX)
    audio = np.full(n, -30.0, np.float32)
    audio[1198:1206] = -5.0  # very loud, but chat does not react
    cands = stage1(Signals(t0=1000, n=n, audio_db=audio, chat=chat), CFG, k=5)
    assert abs(cands[0].t - 600) <= 6
    loud = [c for c in cands if abs(c.t - 1200) <= 6]
    assert not loud or loud[0].score < cands[0].score / 2


def test_small_chat_laugh_burst_is_as_unusual_as_big_one():
    """3 laughs in a near-silent chat should score like 40 in a busy one (owner: zloyn)."""
    from clipper.scoring import timeline as tl

    small = np.zeros(1200, np.float32)
    small[::97] = 1  # ~1 laugh per 1.5 min
    small[1000] = 3
    big = np.random.default_rng(0).poisson(5, 1200).astype(np.float32)
    big[1000] = 40
    zs, zb = tl.robust_z(small, 600, counts=True)[1000], tl.robust_z(big, 600, counts=True)[1000]
    assert zs >= 4 and zb >= 4


def test_cc_chant_and_owner_swears_are_hype():
    for t in ("цц", "ЦЦЦ", "ну пизда", "нахуй"):
        assert LEX.classify(t)[1], t


def test_chat_speedup_counts_as_reaction():
    """Owner: chat suddenly getting faster marks a hype moment even without laugh words."""
    import copy

    n = 1800
    rng = np.random.default_rng(1)
    rows = [
        {"sent_ms": (1000 + t) * 1000 + j, "kind": "msg", "login": f"u{t}_{j}", "user_id": None,
         "text": f"сообщение {t} {j}"}
        for t in range(n) for j in range(rng.poisson(2))
    ]  # fmt: skip
    rows += [
        {"sent_ms": (1000 + 900 + j % 15) * 1000 + j, "kind": "msg", "login": f"s{j}", "user_id": None,
         "text": f"что происходит {j}"}
        for j in range(150)
    ]  # fmt: skip
    chat = chat_signals(rows, 1000, 1000 + n, LEX)
    clips = [(t - 30, t) for t in range(100, 1700, 200)] + [
        (t - 30, t) for t in (905, 915, 925, 940)
    ]
    sig = Signals(t0=1000, n=n, chat=chat, clip_spans=clips)
    base = copy.deepcopy(CFG)
    base["penalties"].pop("weak_chat", None)  # this chat never laughs; test the gate alone
    on = copy.deepcopy(base)
    on["reaction_gate"] = dict(on["reaction_gate"], speedup_is_reaction=True)
    off = copy.deepcopy(base)
    off["reaction_gate"] = dict(off["reaction_gate"], speedup_is_reaction=False)

    def score_at(cfg):
        return max((c.score for c in stage1(sig, cfg, k=20) if abs(c.t - 890) <= 30), default=0.0)

    assert score_at(on) > score_at(off)


def test_clip_grows_while_chat_keeps_laughing():
    from clipper.chat.features import ChatSignals
    from clipper.scoring.stage1 import clip_bounds

    cfg = load_scoring_config()
    n, t = 1200, 800

    def sig_with(laugh_from, laugh_to):
        laugh = np.full(n, 0.1, np.float32)
        laugh[laugh_from:laugh_to] = 4.0
        z = np.zeros(n, np.float32)
        chat = ChatSignals(0, z + 1, laugh, z, z, [[]] * n, [[]] * n)
        return Signals(t0=0, n=n, chat=chat)

    pk = cfg["peaks"]
    short = clip_bounds(sig_with(t + 4, t + 12), t, cfg)
    assert short == (t - pk["pre_roll_seconds"], t + pk["post_roll_seconds"])
    s, e = clip_bounds(sig_with(t + 4, t + 40), t, cfg)  # laughing for 36 s -> longer ending
    assert e > t + pk["post_roll_seconds"] + 10 and e <= t + pk["max_post_roll_seconds"]
    s, e = clip_bounds(sig_with(t - 30, t + 12), t, cfg)  # already laughing before -> earlier start
    assert s < t - pk["pre_roll_seconds"] and s >= t - pk["max_pre_roll_seconds"]


def test_weak_chat_is_penalised():
    n = 1800
    chat = chat_signals(synthetic_chat(n, laugh_burst=40), 1000, 1000 + n, LEX)
    top = stage1(Signals(t0=1000, n=n, chat=chat), CFG, k=5)[0]
    assert "weak_chat" not in top.penalties  # 40 laughs in the window: a real reaction
    quiet = chat_signals(synthetic_chat(n, base_rate=0.3, laugh_burst=4), 1000, 1000 + n, LEX)
    near = [c for c in stage1(Signals(t0=1000, n=n, chat=quiet), CFG, k=5) if abs(c.t - 900) <= 6]
    assert near and "weak_chat" in near[0].penalties  # a handful of messages is not a moment


def test_transcript_text_between_keeps_only_clip_words():
    from clipper.judge.transcribe import Transcript, Word

    tr = Transcript(
        "a b c", [Word("до", 1, 2, 1), Word("в", 15, 16, 1), Word("после", 50, 51, 1)], "ru"
    )
    assert tr.text_between(10, 40) == "в"


def test_gigaam_chunks_cut_at_quiet_points():
    from clipper.judge.transcribe import _cut_points

    sr = 16000
    a = np.random.default_rng(0).normal(0, 0.3, 65 * sr).astype(np.float32)
    a[int(22.0 * sr) : int(22.3 * sr)] = 0  # a pause inside the 20-25 s search zone
    cuts = _cut_points(a, sr, 25.0)
    assert cuts[0] == 0 and 22.0 <= cuts[1] / sr <= 22.3 and len(cuts) == 3
    assert all(b - a_ <= 25 * sr for a_, b in zip(cuts, cuts[1:] + [len(a)], strict=True))
