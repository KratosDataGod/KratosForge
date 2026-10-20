"""A fake HTTP transport for contract tests: no network, no tenant.

It plugs in below ``requests`` as a transport adapter, so the code under test builds its requests
with a real ``requests.Session`` (URL normalization, query encoding, headers) and the fake sees
exactly what would go on the wire. Each request is recorded and answered from a script; a request
the script didn't expect fails the test instead of reaching the network.
"""

from __future__ import annotations

import json as jsonlib
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import parse_qs, urlsplit

import requests
from requests.adapters import BaseAdapter
from requests.structures import CaseInsensitiveDict


@dataclass
class Sent:
    """One request as it would have gone on the wire."""

    method: str
    url: str
    headers: dict[str, str]
    body: bytes | None

    @property
    def path(self) -> str:
        return urlsplit(self.url).path

    @property
    def host(self) -> str:
        return urlsplit(self.url).netloc

    @property
    def base(self) -> str:
        """The URL without its query."""
        parts = urlsplit(self.url)
        return f"{parts.scheme}://{parts.netloc}{parts.path}"

    @property
    def query(self) -> dict[str, str]:
        return {k: v[-1] for k, v in parse_qs(urlsplit(self.url).query, keep_blank_values=True).items()}

    def json(self) -> Any:
        return jsonlib.loads(self.body) if self.body else None


@dataclass
class Reply:
    status: int = 200
    body: Any = None  # dict/list is sent as JSON, bytes/str as is, None as no content
    headers: dict[str, str] = field(default_factory=dict)


Script = Callable[[Sent], Reply]


class FakeTransport(BaseAdapter):
    """Answers requests in order from ``replies`` (a list of Reply, or one callable for all)."""

    def __init__(self, replies: list[Reply] | Script):
        super().__init__()
        self._replies = replies
        self.sent: list[Sent] = []

    def send(self, request: requests.PreparedRequest, stream=False, timeout=None, verify=True,
             cert=None, proxies=None) -> requests.Response:
        body = request.body
        if isinstance(body, str):
            body = body.encode()
        sent = Sent(request.method or "", request.url or "", dict(request.headers), body)
        self.sent.append(sent)
        if callable(self._replies):
            reply = self._replies(sent)
        else:
            if not self._replies:
                raise AssertionError(f"unexpected request: {sent.method} {sent.url}")
            reply = self._replies.pop(0)
        resp = requests.Response()
        resp.status_code = reply.status
        resp.headers = CaseInsensitiveDict(reply.headers)
        if isinstance(reply.body, (dict, list)):
            resp._content = jsonlib.dumps(reply.body).encode()
            resp.headers.setdefault("Content-Type", "application/json")
        elif isinstance(reply.body, str):
            resp._content = reply.body.encode()
        else:
            resp._content = reply.body or b""
        resp._content_consumed = True
        resp.url = sent.url
        resp.request = request
        resp.reason = "scripted"
        return resp

    def close(self) -> None:
        pass

    @property
    def remaining(self) -> int:
        return 0 if callable(self._replies) else len(self._replies)


def session(replies: list[Reply] | Script) -> tuple[requests.Session, FakeTransport]:
    """A real requests.Session whose every http(s) request goes to a FakeTransport."""
    transport = FakeTransport(replies)
    s = requests.Session()
    s.trust_env = False  # no proxy settings or netrc from the machine running the tests
    s.mount("https://", transport)
    s.mount("http://", transport)
    return s, transport


MUTATING = {"PUT", "PATCH", "POST", "DELETE"}
