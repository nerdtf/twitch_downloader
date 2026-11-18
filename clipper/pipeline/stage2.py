"""Stage-2 for stage-1 candidates of one source: transcript -> LLM judge -> gates -> final score.

Works without an LLM (key missing/invalid/budget): candidates are then marked `unjudged` and keep
their stage-1 ranking, so the pipeline still produces clips for review.
"""

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

from clipper.judge.gates import final_score, gate, suppress_overlaps
from clipper.judge.llm import LLMError, LLMJudge
from clipper.judge.transcribe import transcribe_window
from clipper.render.caption import program_for
from clipper.scoring.stage1 import Candidate
from clipper.sources import Source
from clipper.store import Store

log = logging.getLogger(__name__)
PROMPT = (Path(__file__).resolve().parent.parent / "judge/prompt.md").read_text(encoding="utf-8")
CONTEXT_PRE, CONTEXT_POST = 40.0, 25.0  # seconds of context around the event for the judge
DEFAULT_PRE, DEFAULT_POST = 25.0, 12.0  # clip window when the judge gives no boundaries
MIN_LEN, MAX_LEN = 12.0, 60.0


@dataclass
class Judged:
    cand_id: int
    event: float  # epoch
    start: float  # epoch, final clip window
    end: float
    score: float
    passed: bool
    reason: str | None
    judgement: dict | None = None
    transcript: str = ""
    families: dict = field(default_factory=dict)


def _mmss(s: float) -> str:
    return f"{int(s // 60):02d}:{int(s % 60):02d}"


def insert_candidates(store: Store, source: Source, cands: list[Candidate]) -> list[int]:
    ids = []
    for c in cands:
        ev = source.t0 + c.t
        store.conn.execute(
            "INSERT OR IGNORE INTO candidates(source, login, event_epoch, start_epoch, end_epoch, stage1,"
            " features, created_epoch) VALUES (?,?,?,?,?,?,?,?)",
            (
                source.key,
                source.login,
                ev,
                source.t0 + c.start,
                source.t0 + c.end,
                c.score,
                json.dumps(c.to_features(), ensure_ascii=False),
                time.time(),
            ),
        )
        row = store.conn.execute(
            "SELECT id FROM candidates WHERE source=? AND event_epoch=?", (source.key, ev)
        ).fetchone()
        ids.append(row[0])
    return ids


def build_prompt(
    source: Source, transcript_text: str, chat: list[str], window: float, event_at: float
) -> str:
    program = program_for(source.login)
    rules = "\n".join(f"- {r}" for r in program.get("extra_rules") or [])
    return PROMPT.format(
        login=source.login,
        title=source.title or "—",
        game=source.game or "—",
        window_end=_mmss(window),
        event_at=_mmss(event_at),
        transcript=transcript_text or "(речи нет)",
        chat="\n".join(f"- {m}" for m in chat) or "(нет данных чата)",
        extra_rules=rules,
    )


def judge_candidates(
    store: Store,
    source: Source,
    cands: list[Candidate],
    ids: list[int],
    judge: LLMJudge | None,
    language: str = "ru",
) -> list[Judged]:
    out: list[Judged] = []
    llm_ok = judge is not None
    for c, cid in zip(cands, ids, strict=True):
        event = source.t0 + c.t
        a, b = event - CONTEXT_PRE, event + CONTEXT_POST
        audio, off = source.audio_for(a, b)
        tr = transcribe_window(audio, off, b - a, language=language)
        wps = tr.words_per_second(b - a)
        chat = source.chat_sample(event, event + 20)
        j, reason, passed = None, None, True
        start, end = event - DEFAULT_PRE, event + DEFAULT_POST
        if llm_ok:
            try:
                prompt = build_prompt(source, tr.timed_text(), chat, b - a, CONTEXT_PRE)
                j = judge.judge(prompt, candidate_id=cid).data
            except LLMError as e:
                log.warning("LLM unavailable, continuing unjudged: %s", e)
                llm_ok = False
        if j is not None:
            g = gate(j, wps)
            passed, reason = g.passed, g.reason
            bs, be = float(j["best_start"]), float(j["best_end"])
            if 0 <= bs < be <= (b - a) + 5:
                start, end = a + bs, a + be
            score = final_score(c.families, j, sum(c.penalties.values()))
        else:
            if wps < 0.3:
                passed, reason = False, f"dead_air ({wps:.2f} words/s)"
            score = round(100 * c.score, 2)
        dur = min(max(end - start, MIN_LEN), MAX_LEN)
        end = start + dur
        out.append(Judged(cid, event, start, end, score, passed, reason, j, tr.text, c.families))
        store.conn.execute(
            "UPDATE candidates SET stage2=?, final_score=?, status=?, gate_reason=? WHERE id=?",
            (
                json.dumps(
                    {
                        "judgement": j,
                        "transcript": tr.text,
                        "words_per_second": round(wps, 2),
                        "llm": "ok" if j else "unjudged",
                    },
                    ensure_ascii=False,
                ),
                score,
                "judged" if passed else "dropped",
                reason,
                cid,
            ),
        )
    # overlap gate among survivors
    alive = [x for x in out if x.passed]
    for i in suppress_overlaps([(x.event, x.score) for x in alive]):
        alive[i].passed, alive[i].reason = False, "overlap"
        store.conn.execute(
            "UPDATE candidates SET status='dropped', gate_reason='overlap' WHERE id=?",
            (alive[i].cand_id,),
        )
    return sorted(out, key=lambda x: -x.score)
