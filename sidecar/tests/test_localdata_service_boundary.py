"""The local data service's old query calls at their boundaries (mutation batch 208, a boundary re-score of
batch 6, docs/quality/mutation-testing.md): a result that has all its rows isn't truncated, and the first
rows stop being asked for once there are enough."""

from __future__ import annotations

import types

from fabric_desktop.localdata_service import LocalDataMixin

_LEGACY = {"project": "p", "seconds": 0.1, "columns": [{"name": "n"}], "dialect": "duckdb", "error": None,
           "refused": None}


class _Runs:
    def __init__(self, total: int, page_size: int = 2):
        self.total, self.page_size, self.asked = total, page_size, []

    def status(self, query_id):
        return dict(_LEGACY, state="completed", rows=self.total)

    def page(self, query_id, offset, count):
        self.asked.append((offset, count))
        end = min(self.total, offset + min(count, self.page_size))
        return {"rows": [[i] for i in range(offset, end)]}


def _service(runs, limit=None):
    svc = types.SimpleNamespace(sql_runs=runs, _local_query_limits={} if limit is None else {"q1": limit})
    svc._first_rows = lambda qid, n: LocalDataMixin._first_rows(svc, qid, n)
    return svc


def test_a_result_with_all_its_rows_isnt_truncated():
    svc = _service(_Runs(3))
    out = LocalDataMixin.local_query_status(svc, "q1")["result"]
    assert out["rows"] == [[0], [1], [2]] and out["truncated"] is False and "note" not in out
    svc = _service(_Runs(3), limit=2)
    out = LocalDataMixin.local_query_status(svc, "q1")["result"]
    assert out["rows"] == [[0], [1]] and out["truncated"] is True


def test_the_first_rows_stop_being_asked_for_once_there_are_enough():
    runs = _Runs(10)
    assert LocalDataMixin._first_rows(types.SimpleNamespace(sql_runs=runs), "q1", 4) == [[0], [1], [2], [3]]
    assert runs.asked == [(0, 4), (2, 2)]
    runs = _Runs(10)
    assert LocalDataMixin._first_rows(types.SimpleNamespace(sql_runs=runs), "q1", 0) == []
    assert runs.asked == []
