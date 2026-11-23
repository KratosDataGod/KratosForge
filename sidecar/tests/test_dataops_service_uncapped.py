"""The data engineering service (dataops_service.py) edges left after running every mutant (mutation
batch 256, docs/quality/mutation-testing.md): default page sizes, the last deployment, a quarantined
row's id, rows in an empty table, a named entity file, email alerts with no mail server, and the
Data health list's week and snooze to the second."""

import datetime as dt
import types

import polars as pl
import pyarrow as pa
import pytest

from fabric_desktop import dataops_service as ds
from fabric_desktop.service import Service

from tests.test_dataops_service_mutation import _alerting, bare  # noqa: F401 - bare is a fixture

UTC = dt.timezone.utc
T = dt.datetime(2026, 11, 20, 12, 0, 0, tzinfo=UTC)


class Store:
    """Stands in for a store: meta tables as data frames, filtered as asked."""

    def __init__(self, tables=None, quarantine=None):
        self.tables, self.quarantine, self.asked = tables or {}, quarantine, []

    def meta_where(self, table, expr):
        return self.tables[table].filter(expr)

    def history(self, lakehouse, schema, table, limit):
        self.asked.append(limit)
        return []

    def read_at(self, lakehouse, folder, table):
        return self.quarantine, 3


def entity(**kw):
    return types.SimpleNamespace(
        **(
            {
                "layer": "silver",
                "schema": "dbo",
                "table": "t",
                "spec": {},
                "columns": ["a"],
            }
            | kw
        )
    )


@pytest.fixture
def stub(tmp_path, monkeypatch):
    """A service whose project has one entity, ``t``, and whose store is ``s.store``."""
    s = Service(root=tmp_path / "app")
    project = types.SimpleNamespace(
        root=tmp_path / "proj",
        entities={"t": entity()},
        layer=lambda name: types.SimpleNamespace(lakehouse="LH"),
    )
    s.project, s.store = project, Store()
    monkeypatch.setattr(type(s), "_project", lambda self: self.project)
    monkeypatch.setattr(
        type(s), "_store", lambda self, project, env, create=False: self.store
    )
    monkeypatch.setattr(ds, "impact", lambda p, name: [])
    return s


def test_quarantine_shows_its_first_500_rows_by_default(stub, monkeypatch):
    q = pl.DataFrame({"_qid": [f"q{i:03}" for i in range(501)], "a": ["x"] * 501})
    monkeypatch.setattr(
        stub, "_quarantine_table", lambda p, name, env: (None, "", "", q)
    )
    out = stub.meta_quarantine("t")
    assert out["total"] == 501 and len(out["rows"]) == 500
    assert out["rows"][-1]["_qid"] == "q499"


def test_a_tables_versions_are_its_last_15_by_default(stub):
    assert stub.meta_versions("t") == [] and stub.store.asked == [15]


def test_drift_shows_that_entitys_last_ten_schema_changes_newest_first(
    stub, monkeypatch
):
    monkeypatch.setattr(ds.drift_mod, "detect", lambda project, e, source: {})
    stub.store.tables["schema_history"] = pl.DataFrame(
        {"entity": ["t"] * 12 + ["other"] * 3, "n": list(range(12)) + [90, 91, 92]}
    )
    out = stub.meta_drift("t")
    assert [r["n"] for r in out["history"]] == [11, 10, 9, 8, 7, 6, 5, 4, 3, 2]
    assert {r["entity"] for r in out["history"]} == {"t"}


def test_the_deployed_entities_are_those_of_the_last_promote_to_that_environment(stub):
    stub.store.tables = {
        "deployments": pl.DataFrame(
            {"env": ["test", "test", "prod"], "version": ["v1", "v2", "v3"]}
        ),
        "entities": pl.DataFrame(
            {
                "version": ["v1", "v2", "v3"],
                "name": ["a", "a", "a"],
                "config": ["one", "two", "three"],
            }
        ),
    }
    assert stub._deployed_entities(stub.project, "test") == {"a": "two"}


def test_freshness_reads_up_to_100000_runs(stub, monkeypatch):
    asked = []
    monkeypatch.setattr(
        stub,
        "meta_runs_all",
        lambda limit: asked.append(limit) or {"runs": [], "unavailable": {}},
        raising=False,
    )
    assert stub.meta_freshness()["items"] == [] and asked == [100000]


def test_a_quarantined_row_without_an_id_gets_old_and_16_hex_digits_of_its_values(stub):
    stub.store.quarantine = pl.DataFrame({"a": ["x", "y"]})
    _, _, table, q, version = stub._quarantine_at(stub.project, "t", "dev")
    ids = q["_qid"].to_list()
    assert table == "dbo_t" and version == 3 and len(set(ids)) == 2
    assert all(
        i.startswith("old-") and len(i) == 20 and int(i[4:], 16) >= 0 for i in ids
    )


def test_an_empty_table_has_no_rows(monkeypatch):
    monkeypatch.setattr("fabric_desktop.delta_read.deleted_rows", lambda table: 0)
    table = types.SimpleNamespace(
        get_add_actions=lambda flatten: pa.table(
            {"num_records": pa.array([0], pa.int64())}
        )
    )
    assert ds._rows_in(table) == 0
    table = types.SimpleNamespace(
        get_add_actions=lambda flatten: pa.table(
            {"num_records": pa.array([2, 3], pa.int64())}
        )
    )
    assert ds._rows_in(table) == 5


def test_a_committed_entity_file_is_named_by_the_name_inside_it(stub, monkeypatch):
    monkeypatch.setattr(
        ds.release,
        "committed",
        lambda root, folder, suffix: {
            "metadata/entities/file_name.toml": 'name = "inside"\nowner = "me"\n'
        },
    )
    stub.project.entities = {}
    assert [(d["entity"], d["state"]) for d in stub.meta_diff("head")["entities"]] == [
        ("inside", "removed")
    ]


def test_email_alerts_need_the_company_mail_server_first(tmp_path):
    s = Service(root=tmp_path / "app")
    with pytest.raises(ValueError, match="Set the company mail server first"):
        s.meta_set_alerts("mailto:a@example.com")
    assert s.meta_alerts()["set"] is False


class Frozen(dt.datetime):
    @classmethod
    def now(cls, tz=None):
        return T


def freeze(monkeypatch):
    monkeypatch.setattr(
        ds,
        "dt",
        types.SimpleNamespace(
            datetime=Frozen, timedelta=dt.timedelta, timezone=dt.timezone
        ),
    )


def test_a_reset_is_not_said_once_exactly_a_week_has_passed(bare, monkeypatch):  # noqa: F811
    freeze(monkeypatch)
    for at, said in (
        (T - dt.timedelta(days=7), False),
        (T - dt.timedelta(days=7) + dt.timedelta(seconds=1), True),
    ):
        bare._dh_write({"reset": {"at": at.isoformat(), "copy": "c"}})
        assert ("reset" in bare._dh_read()) is said


def test_a_snooze_that_ends_now_is_over(bare, monkeypatch):  # noqa: F811
    bare.meta_data_health_watch("silver.customers", "dev")
    _alerting(bare, "silver.customers")
    freeze(monkeypatch)
    for until, quiet in ((T, False), (T + dt.timedelta(seconds=1), True)):
        bare._dh_write(
            bare._dh_read()
            | {"quiet": {"dev|silver.customers|volume|5": {"until": until.isoformat()}}}
        )
        assert (
            "quiet" in bare.meta_data_health_list()["tables"][0]["alerts"][0]
        ) is quiet
