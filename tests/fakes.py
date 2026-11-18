"""Shared test doubles."""


class FakeResponse:
    def __init__(self, status: int, body: dict):
        self.status_code, self._body = status, body
        self.ok = 200 <= status < 300
        self.text = str(body)

    def json(self):
        return self._body


class FakeSession:
    """Records requests; replies from queues keyed by (method, url-suffix)."""

    def __init__(self, replies: dict[tuple[str, str], list[FakeResponse]]):
        self.replies = replies
        self.calls: list[tuple[str, str, object, dict]] = []

    def _reply(self, method, url, params=None, headers=None, data=None, **_):
        self.calls.append((method, url, params if params is not None else data, headers or {}))
        for (m, suffix), queue in self.replies.items():
            if m == method and url.endswith(suffix) and queue:
                return queue.pop(0) if len(queue) > 1 else queue[0]
        raise AssertionError(f"unexpected {method} {url}")

    def get(self, url, **kw):
        return self._reply("GET", url, **kw)

    def post(self, url, **kw):
        return self._reply("POST", url, **kw)
