import pytest

from clipper.judge.gates import final_score, gate, suppress_overlaps
from clipper.judge.llm import BudgetExceeded, LLMError, LLMJudge, parse_json, validate
from clipper.store import Store
from tests.fakes import FakeResponse, FakeSession

GOOD = {
    "humor": 8,
    "intensity": 7,
    "standalone_clarity": 8,
    "setup_payoff": 6,
    "streamer_is_main": True,
    "banned_topic": False,
    "category": "funny",
    "best_start": 10,
    "best_end": 40,
    "hook_line": "Он не ожидал",
    "title": "Бустер в шоке",
}


def test_parse_json_variants():
    assert parse_json('```json\n{"a": 1}\n```') == {"a": 1}
    assert parse_json('ok: {"a": 1, // comment\n "b": 2}') == {"a": 1, "b": 2}
    with pytest.raises(LLMError):
        parse_json("no json here")


def test_validate_types_and_clamps():
    d = validate({**GOOD, "humor": 14})
    assert d["humor"] == 10.0 and d["reason"] == ""
    with pytest.raises(LLMError):
        validate({**GOOD, "streamer_is_main": "yes"})
    with pytest.raises(LLMError):
        validate({k: v for k, v in GOOD.items() if k != "title"})


@pytest.mark.parametrize(
    "patch,wps,reason",
    [
        ({"banned_topic": True, "banned_reason": "политика"}, 2.0, "banned_topic: политика"),
        ({"streamer_is_main": False}, 2.0, "streamer_not_main"),
        ({"intensity": 3}, 0.2, "dead_air (0.20 words/s)"),
        ({"standalone_clarity": 2}, 2.0, "low_clarity (2/10)"),
    ],
)
def test_gates(patch, wps, reason):
    g = gate({**GOOD, **patch}, wps)
    assert not g.passed and g.reason == reason


def test_gate_passes_and_final_score_agreement():
    assert gate(GOOD, 2.0).passed
    strong = final_score({"chat": 5, "laugh": 6, "audio": 3}, GOOD)
    lonely = final_score({"chat": 0, "laugh": 0, "audio": 0}, {**GOOD, "humor": 2, "intensity": 2})
    assert strong > 60 and lonely < 20
    assert final_score({"audio": 4}, GOOD, penalties=0.3) < final_score({"audio": 4}, GOOD)


def test_suppress_overlaps_keeps_higher():
    assert suppress_overlaps([(100, 50), (150, 80), (400, 10)]) == {0}


def _judge(store, replies, budget=1.0):
    return LLMJudge(
        store,
        budget,
        api_key="k",
        model="kimi-k3",
        base_url="https://x/v1",
        session=FakeSession(replies),
    )


def _ok(content, tokens=(1000, 200)):
    return FakeResponse(
        200,
        {
            "choices": [{"message": {"content": content}}],
            "usage": {"prompt_tokens": tokens[0], "completion_tokens": tokens[1]},
        },
    )


def test_llm_judge_retries_bad_json_then_caches(tmp_path):
    import json

    store = Store(tmp_path / "x.db")
    j = _judge(store, {("POST", "/chat/completions"): [_ok("not json"), _ok(json.dumps(GOOD))]})
    r = j.judge("prompt")
    assert r.data["title"] == "Бустер в шоке" and not r.cached and r.cost_usd > 0
    assert j.judge("prompt").cached  # second call: no HTTP
    assert store.conn.execute("SELECT COUNT(*) FROM llm_calls").fetchone()[0] == 2


def test_llm_budget_and_auth(tmp_path):
    store = Store(tmp_path / "x.db")
    with pytest.raises(LLMError, match="auth"):
        _judge(store, {("POST", "/chat/completions"): [FakeResponse(401, {})]}).judge("p")
    store.conn.execute("INSERT INTO llm_calls(epoch, model, cost_usd, ok) VALUES (0, 'm', 5.0, 1)")
    with pytest.raises(BudgetExceeded):
        _judge(store, {}).judge("other prompt")


def test_whisper_hallucination_filter():
    from clipper.judge.transcribe import keep_segment

    assert not keep_segment({"text": " Субтитры сделал DimaTorzok"})
    assert not keep_segment({"text": "что", "no_speech_prob": 0.9, "avg_logprob": -1.2})
    assert not keep_segment({"text": "да " * 40, "compression_ratio": 4.0})
    assert keep_segment(
        {
            "text": " ну это просто бесконечно",
            "no_speech_prob": 0.1,
            "avg_logprob": -0.3,
            "compression_ratio": 1.3,
        }
    )
