"""integrations/http_server.py at its exact edges (mutation batch 165, a boundary re-score of batch 26,
docs/quality/mutation-testing.md): a refused request exactly REFUSED_EVERY seconds after the last line
is written again, and a parent process id of 0 means no parent to watch."""

from __future__ import annotations

import types

from fabric_desktop.integrations import http_server
from fabric_desktop.integrations.core import IntegrationError


def test_a_refusal_exactly_refused_every_seconds_later_gets_its_own_line(monkeypatch):
    lines = []
    registry = types.SimpleNamespace(audit=lambda *args: lines.append(args))
    guard = http_server.Guard(app=None, store=None, port=1, registry=registry, same_user=lambda a, b: True)
    clock = iter([100.0, 100.0 + http_server.REFUSED_EVERY - 0.5, 100.0 + http_server.REFUSED_EVERY])
    monkeypatch.setattr(http_server.time, "monotonic", lambda: next(clock))
    err = IntegrationError("permission_denied", "Not from this PC.")
    for _ in range(3):
        guard._audit({"path": "/api", "method": "GET"}, err, {"id": "cl_1"})
    assert len(lines) == 2  # the first, and the one exactly REFUSED_EVERY seconds after it
    assert lines[1][3]["refused_since_last_line"] == 1


def test_a_parent_process_id_of_0_is_no_parent_to_watch(monkeypatch):
    def gone(pid, signal):
        raise ProcessLookupError(pid)
    monkeypatch.setattr(http_server.os, "kill", gone)
    assert http_server._parent_alive(0) is True
    assert http_server._parent_alive(-1) is True
