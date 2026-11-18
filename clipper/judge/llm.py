"""LLM judge over an OpenAI-compatible chat API (Kimi / DeepSeek). Strict JSON, cached, metered.

Env: LLM_API_KEY, LLM_MODEL, optional LLM_BASE_URL (default Moonshot international).
"""

import hashlib
import json
import logging
import os
import re
import time
from dataclasses import dataclass

import requests

from clipper.env import load_env
from clipper.store import Store

log = logging.getLogger(__name__)
DEFAULT_BASE_URL = "https://api.moonshot.ai/v1"
# USD per 1M tokens (input, output). Unknown models fall back to a conservative guess.
PRICES = {"kimi-k3": (1.0, 4.0), "deepseek-chat": (0.3, 1.2)}
DEFAULT_PRICE = (1.5, 6.0)

SCHEMA_KEYS = {
    "humor": (int, float),
    "intensity": (int, float),
    "standalone_clarity": (int, float),
    "setup_payoff": (int, float),
    "streamer_is_main": bool,
    "banned_topic": bool,
    "category": str,
    "best_start": (int, float),
    "best_end": (int, float),
    "hook_line": str,
    "title": str,
}


class LLMError(RuntimeError):
    pass


class BudgetExceeded(LLMError):
    pass


@dataclass
class Judgement:
    data: dict
    cached: bool
    cost_usd: float


def validate(data: dict) -> dict:
    missing = [k for k in SCHEMA_KEYS if k not in data]
    if missing:
        raise LLMError(f"missing keys {missing}")
    for k, t in SCHEMA_KEYS.items():
        if not isinstance(data[k], t) or (t is not bool and isinstance(data[k], bool)):
            raise LLMError(f"{k} has wrong type {type(data[k]).__name__}")
    for k in ("humor", "intensity", "standalone_clarity", "setup_payoff"):
        data[k] = float(min(max(data[k], 0), 10))
    data.setdefault("banned_reason", "")
    data.setdefault("reason", "")
    return data


def parse_json(text: str) -> dict:
    """Accept bare JSON, ```json fences, or JSON embedded in text; strip // comments."""
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        raise LLMError("no JSON object in response")
    body = re.sub(r"//[^\n\"]*(?=\n)", "", m.group(0))
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise LLMError(f"invalid JSON: {e}") from e


class LLMJudge:
    def __init__(
        self,
        store: Store,
        budget_usd: float,
        api_key: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        session=None,
    ):
        load_env()
        self.store = store
        self.budget_usd = budget_usd
        self.api_key = api_key or os.environ.get("LLM_API_KEY", "")
        self.model = model or os.environ.get("LLM_MODEL", "kimi-k3")
        self.base_url = (base_url or os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.http = session or requests.Session()

    def spent_usd(self) -> float:
        row = self.store.conn.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM llm_calls").fetchone()
        return float(row[0])

    def _cost(self, usage: dict) -> float:
        pin, pout = PRICES.get(self.model, DEFAULT_PRICE)
        return (
            usage.get("prompt_tokens", 0) * pin + usage.get("completion_tokens", 0) * pout
        ) / 1e6

    def _cached(self, key: str) -> dict | None:
        row = self.store.conn.execute(
            "SELECT response FROM llm_calls WHERE cache_key=? AND ok=1 ORDER BY id DESC LIMIT 1",
            (key,),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def judge(self, prompt: str, candidate_id: int | None = None) -> Judgement:
        key = hashlib.sha1(f"{self.model}|{prompt}".encode()).hexdigest()
        if (hit := self._cached(key)) is not None:
            return Judgement(hit, cached=True, cost_usd=0.0)
        if not self.api_key:
            raise LLMError("LLM_API_KEY not set")
        if self.spent_usd() >= self.budget_usd:
            raise BudgetExceeded(f"LLM budget ${self.budget_usd:.2f} reached")
        messages = [{"role": "user", "content": prompt}]
        last_err: Exception | None = None
        total = 0.0
        for _attempt in range(2):  # one retry with the validation error fed back
            r = self.http.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self.api_key}"},
                json={
                    "model": self.model,
                    "messages": messages,
                    "temperature": 0.3,
                    "response_format": {"type": "json_object"},
                    "max_tokens": 800,
                },
                timeout=120,
            )
            if r.status_code in (401, 403):
                raise LLMError(
                    f"LLM auth failed (HTTP {r.status_code}) at {self.base_url}: {r.text[:200]}"
                )
            if not r.ok:
                last_err = LLMError(f"HTTP {r.status_code}: {r.text[:200]}")
                time.sleep(2)
                continue
            body = r.json()
            usage = body.get("usage") or {}
            cost = self._cost(usage)
            total += cost
            content = body["choices"][0]["message"]["content"]
            try:
                data = validate(parse_json(content))
                ok, err = True, None
            except LLMError as e:
                ok, err, data = False, e, None
            self.store.conn.execute(
                "INSERT INTO llm_calls(epoch, model, candidate_id, prompt_tokens, completion_tokens, cost_usd,"
                " ok, cache_key, response) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    time.time(),
                    self.model,
                    candidate_id,
                    usage.get("prompt_tokens"),
                    usage.get("completion_tokens"),
                    cost,
                    int(ok),
                    key if ok else None,
                    json.dumps(data, ensure_ascii=False) if ok else content[:2000],
                ),
            )
            if ok:
                return Judgement(data, cached=False, cost_usd=total)
            last_err = err
            messages += [
                {"role": "assistant", "content": content},
                {
                    "role": "user",
                    "content": f"Ошибка формата: {err}. Верни только корректный JSON по схеме.",
                },
            ]
        raise LLMError(f"judge failed: {last_err}")


def check() -> int:
    """`python -m clipper.judge.llm --check`: one tiny request; prints the server's answer,
    never the key."""
    load_env()
    key = os.environ.get("LLM_API_KEY", "")
    base = (os.environ.get("LLM_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
    model = os.environ.get("LLM_MODEL", "kimi-k3")
    print(f"base_url={base} model={model} key: {len(key)} chars, starts with {key[:3]!r}, "
          f"quotes/spaces inside: {any(c in key for c in ' \t\"\'')}")  # fmt: skip
    r = requests.post(
        f"{base}/chat/completions",
        headers={"Authorization": f"Bearer {key}"},
        json={"model": model, "messages": [{"role": "user", "content": "ok"}], "max_tokens": 5},
        timeout=60,
    )
    print(f"HTTP {r.status_code}: {r.text[:300]}")
    return 0 if r.ok else 1


if __name__ == "__main__":
    import sys

    sys.exit(
        check() if "--check" in sys.argv else print("usage: python -m clipper.judge.llm --check")
    )
