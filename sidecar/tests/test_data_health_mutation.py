"""Data health edges the main tests don't pin down (mutation batch 95, docs/quality/mutation-testing.md):
limits at their exact boundaries, odd log entries, and the wording of a schema alert."""

import datetime as dt
import json

import polars as pl
from deltalake import DeltaTable, Field

from fabric_desktop.meta import data_health as dh

NOW = dt.datetime(2026, 11, 17, 12, tzinfo=dt.timezone.utc)


def _log(rows, op="WRITE"):
    """A Delta history, newest first: one load per count, an hour apart, the last an hour ago."""
    out = []
    for v, n in enumerate(rows):
        out.append({"version": v, "timestamp": int((NOW - dt.timedelta(hours=len(rows) - v)).timestamp() * 1000),
                    "operation": op, "operationParameters": {"mode": "Append"},
                    "operationMetrics": {} if n is None else {"numOutputRows": str(n)}})
    return out[::-1]


def test_the_usual_comes_from_exactly_the_last_twenty_loads_and_the_list_shows_twenty_one():
    # 20 loads of 100 before the latest, and older ones of 1,000 that must not count.
    v = dh.volume(_log([1000] * 15 + [100] * dh.BASELINE + [100]))
    assert v["usual"]["have"] == dh.BASELINE == 20 and v["usual"]["median"] == 100
    assert len(v["loads"]) == dh.BASELINE + 1 and v["loads"][0]["rows"] == 100
    v = dh.volume(_log([1000] * 11 + [100] * 19 + [100]))  # one older load of 1,000 joins the 20
    assert v["usual"]["have"] == 20 and v["loads"][0]["rows"] == 1000


def test_exactly_the_minimum_readable_loads_make_a_usual():
    assert dh.usual([5] * (dh.MINIMUM - 1))["enough"] is False
    assert dh.usual([5] * dh.MINIMUM)["enough"] is True


def test_one_row_above_a_usual_of_none_is_high():
    v = dh.volume(_log([0] * 12 + [1]))
    assert v["state"] == "high" and v["distance"] == 1


def test_no_loads_says_so_with_nothing_unread():
    v = dh.volume([{"version": 0, "timestamp": 0, "operation": "OPTIMIZE"}])
    assert v["state"] == "no_loads" and v["unread"] == 0 and v["latest"] is None and v["usual"]["enough"] is False


def test_a_zero_count_is_a_count():
    assert dh._count(0) == 0 and dh._count("0") == 0 and dh._count(-1) is None


def test_metrics_that_arent_json_and_parameters_that_arent_an_object_are_left_out():
    entry = {"version": 1, "timestamp": 0, "operation": "WRITE", "operationMetrics": "{not json",
             "operationParameters": "Append"}
    assert dh.rows_written(entry) is None
    [load] = dh.loads([entry])
    assert load["mode"] is None and load["rows"] is None


def test_a_naive_time_is_read_as_utc():
    when = dh._when({"timestamp": dt.datetime(2026, 11, 17, 10)})
    assert when == dt.datetime(2026, 11, 17, 10, tzinfo=dt.timezone.utc)
    aware = dt.datetime(2026, 11, 17, 10, tzinfo=dt.timezone(dt.timedelta(hours=2)))
    assert dh._when({"timestamp": aware}) is aware


def test_age_is_rounded_to_two_places_and_exactly_the_promise_is_still_fresh():
    history = [{"version": 1, "operation": "WRITE",
                "timestamp": int((NOW - dt.timedelta(hours=2.346)).timestamp() * 1000)}]
    assert dh.freshness(history, None, NOW)["age_hours"] == 2.35
    history[0]["timestamp"] = int((NOW - dt.timedelta(hours=24)).timestamp() * 1000)
    assert dh.freshness(history, 24, NOW)["state"] == "fresh"


def test_a_schema_alert_names_what_changed():
    def title(**s):
        result = {"freshness": {"state": "fresh"}, "volume": {"state": "usual"},
                  "schema": {"state": "changed", "added": [], "removed": [], "retyped": [], "version": 3} | s}
        return dh.alerts(result)[0]["title"]

    assert title(added=["a"]) == "Schema changed: 1 column added"
    assert title(added=["a", "b"], removed=["c"], retyped=[{"name": "d"}]) == "Schema changed: 2 columns added, 1 removed, 1 retyped"
    assert title(removed=["c"]) == "Schema changed: 1 removed"
    assert title(retyped=[{"name": "d"}]) == "Schema changed: 1 retyped"


def test_a_struct_columns_type_is_kept_as_json_and_a_plain_type_as_is(tmp_path):
    uri = str(tmp_path / "t")
    pl.DataFrame({"a": [1], "s": [{"x": 1}]}).write_delta(uri)
    cols = {c["name"]: c["type"] for c in dh._columns(DeltaTable(uri).schema())}
    assert cols["a"] == "long" and json.loads(cols["s"])["type"] == "struct"


def test_a_table_with_one_load_compares_its_schema_with_the_version_before(tmp_path):
    uri = str(tmp_path / "t")
    pl.DataFrame({"a": [1]}).write_delta(uri)  # version 0: the first load
    out = dh.check(lambda v: DeltaTable(uri, version=v))
    assert out["schema"] == {"state": "no_earlier_version"}
    DeltaTable(uri).alter.add_columns([Field("b", "long", nullable=True)])  # version 1, not a load
    out = dh.check(lambda v: DeltaTable(uri, version=v))
    assert out["volume"]["latest"]["version"] == 0 and out["schema"] == {"state": "no_earlier_version"}


def test_a_single_load_after_other_commits_compares_with_the_version_just_before(tmp_path):
    uri = str(tmp_path / "t")
    pl.DataFrame({"a": [1]}).write_delta(uri)  # version 0
    DeltaTable(uri).alter.add_columns([Field("b", "long", nullable=True)])  # version 1
    pl.DataFrame({"a": [2], "b": [2]}).write_delta(uri, mode="append")  # version 2: a load

    class Table:
        def __init__(self, v):
            self.t = DeltaTable(uri, version=v)

        def history(self, n):
            # Only the newest load is in the log window, as when older commits were cleaned up.
            return [e for e in self.t.history(n) if e.get("version") != 0]

        def version(self):
            return self.t.version()

        def schema(self):
            return self.t.schema()

    out = dh.check(lambda v: Table(v))
    assert out["schema"]["since_version"] == 1 and out["schema"]["version"] == 2 and out["schema"]["state"] == "same"
