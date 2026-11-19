"""integrations/executions.py at its exact edges (mutation batch 165, a boundary re-score of batch 23,
docs/quality/mutation-testing.md): an output of exactly the budget left is sent, a result of exactly
max_rows isn't truncated, and interrupting stops at its deadline."""

from __future__ import annotations

import json

from fabric_desktop.integrations import executions
from fabric_desktop.integrations.executions import shape_outputs

import pytest

from test_integrations_executions import call, no_policy, svc  # noqa: F401  (fixtures)


@pytest.fixture(autouse=True)
def _own_app_data(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


def test_an_output_of_exactly_the_budget_left_is_sent():
    out = {"type": "stream", "name": "stdout", "text": "hello"}
    [shaped] = shape_outputs([out], [10_000])
    size = len(json.dumps(shaped, default=str))
    budget = [size]
    assert shape_outputs([out], budget) == [shaped] and budget == [0]
    assert shape_outputs([out], [size - 1])[0]["text"].startswith("[output cut")


def test_a_result_of_exactly_max_rows_is_not_truncated(svc):  # noqa: F811
    out = call(svc, "fabric_desktop_execute_sql", workspace_id="ws1", lakehouse_id="lh1",
               query="SELECT Country FROM sales", max_rows=3)
    assert out["result"]["row_count"] == 3 and out["result"]["truncated"] is False


def test_interrupting_stops_at_exactly_its_deadline(monkeypatch):
    times = iter([0.0])
    monkeypatch.setattr(executions.time, "monotonic", lambda: next(times, 30.0))

    class Con:
        calls = 0

        def interrupt(self):
            Con.calls += 1

    class Stopped:
        def is_set(self):
            return False

        def wait(self, seconds):
            if Con.calls > 3:
                raise AssertionError("still interrupting at the deadline")

    executions.interrupt_until_stopped(Con(), Stopped(), seconds=30.0)
    assert Con.calls == 0
