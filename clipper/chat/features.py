"""Per-second chat signals for a time range: message rate, laugh/hype counts, unique chatters,
copypasta share, sub/raid notices. Input: rows from the `chat` table."""

from collections import Counter
from dataclasses import dataclass

import numpy as np

from clipper.chat.lexicon import Lexicon, normalize


@dataclass
class ChatSignals:
    t0: int  # epoch second of index 0
    msgs: np.ndarray  # reaction messages per second (bots/commands excluded)
    laugh: np.ndarray
    hype: np.ndarray
    notices: np.ndarray  # USERNOTICE (sub/resub/gift/raid) per second
    users: list[list[str]]  # per second: chatter ids (for unique-ratio windows)
    texts: list[list[str]]  # per second: normalized texts (for copypasta windows)

    def window_stats(self, a: int, b: int) -> dict:
        """Stats over seconds [a, b) relative to t0."""
        a, b = max(a, 0), min(b, len(self.msgs))
        users = [u for s in self.users[a:b] for u in s]
        texts = [t for s in self.texts[a:b] for t in s]
        n = len(texts)
        top = Counter(texts).most_common(1)[0][1] if texts else 0
        return {
            "msgs": n,
            "unique_ratio": len(set(users)) / n if n else 0.0,
            "copypasta_share": top / n if n else 0.0,
            "laugh": int(self.laugh[a:b].sum()),
            "hype": int(self.hype[a:b].sum()),
            "notices": int(self.notices[a:b].sum()),
        }


def chat_signals(rows, t0: int, t1: int, lexicon: Lexicon) -> ChatSignals:
    """rows: iterable of mappings with sent_ms, kind, login, user_id, text."""
    n = max(int(t1 - t0), 0)
    sig = ChatSignals(
        t0=int(t0),
        msgs=np.zeros(n, np.float32),
        laugh=np.zeros(n, np.float32),
        hype=np.zeros(n, np.float32),
        notices=np.zeros(n, np.float32),
        users=[[] for _ in range(n)],
        texts=[[] for _ in range(n)],
    )
    for r in rows:
        i = int(r["sent_ms"] // 1000) - int(t0)
        if not 0 <= i < n:
            continue
        if r["kind"] == "usernotice":
            sig.notices[i] += 1
            continue
        if r["kind"] != "msg" or not r["text"] or lexicon.is_ignored(r["login"], r["text"]):
            continue
        is_laugh, is_hype = lexicon.classify(r["text"])
        sig.msgs[i] += 1
        sig.laugh[i] += is_laugh
        sig.hype[i] += is_hype
        sig.users[i].append(r["user_id"] or r["login"] or "?")
        sig.texts[i].append(normalize(r["text"]))
    return sig
