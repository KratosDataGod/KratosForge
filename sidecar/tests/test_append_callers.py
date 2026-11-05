"""Every caller that appends through lakehouse.write_table or publish stops an append that would
change values to fit the table's types (EA-001), and leaves the table at its version; a widening
(int into bigint) still writes. The check itself and lakehouse.write_table called from a notebook
are tested in test_append_conversions and test_ea_write_integrity; the metadata framework and the
published-notebook shim in test_conversions_everywhere. These cover the callers left: Spark's
saveAsTable and insertInto, the SQL page's Save as table, wizard-made notebooks, a notebook's
background (local first) write, a job, and a proof run's sandbox write. An append written again
after a dropped upload adds its rows once."""

from __future__ import annotations

import datetime as dt

import pyarrow as pa
import pytest
from deltalake import DeltaTable, write_deltalake
from test_onelake_writes import DROPPED, LH, service  # noqa: F401 - service is a fixture
from test_spark_write_types import lake, run  # noqa: F401 - lake is a fixture

from fabric_desktop import jobs, lakehouse, publish, sql, wizard
from fabric_desktop.service import Service

LATER = dt.datetime(2026, 1, 2, 23, 30, tzinfo=dt.timezone.utc)

# (case, the table's column, the appended column, the same in Spark SQL, the same in DuckDB SQL)
CASES = [
    ("float into int", pa.array([3], pa.int32()), pa.array([2.75]), "2.75D", "2.75::DOUBLE"),
    ("timestamp into date", pa.array([dt.date(2026, 1, 1)]), pa.array([LATER], pa.timestamp("us", "UTC")),
     "TIMESTAMP'2026-01-02 23:30:00'", "TIMESTAMPTZ '2026-01-02 23:30:00+00'"),
    ("text into int", pa.array([1], pa.int64()), pa.array(["12"]), "'12'", "'12'"),
]
REFUSED = pytest.mark.parametrize("have, new, spark_sql, duck_sql", [c[1:] for c in CASES], ids=[c[0] for c in CASES])
WIDER = (pa.array([1], pa.int64()), pa.array([2], pa.int32()), "CAST(2 AS INT)", "2::INTEGER")
CHANGES = "would change values"


def seed(path, have):
    write_deltalake(str(path), pa.table({"id": pa.array([1], pa.int64()), "v": have}))


def appended(id_, column):
    return pa.table({"id": pa.array([id_], pa.int64()), "v": column})


def version(path):
    return DeltaTable(str(path)).version()


def ids(path):
    return sorted(DeltaTable(str(path)).to_pyarrow_table().column("id").to_pylist())


# Spark in a notebook: saveAsTable and insertInto in append mode (spark_local._write_table).
@REFUSED
@pytest.mark.parametrize("how", ["saveAsTable", "insertInto"])
def test_spark_appends_stop(lake, have, new, spark_sql, duck_sql, how):
    seed(lake / "dbo" / "t", have)
    status, text = run(f"spark.sql(\"SELECT 2L AS id, {spark_sql} AS v\").write.mode('append').{how}('dbo.t')")
    assert status == "error" and "Nothing was written" in text, text
    assert version(lake / "dbo" / "t") == 0


@pytest.mark.parametrize("how", ["saveAsTable", "insertInto"])
def test_spark_appends_that_widen_write(lake, how):
    seed(lake / "dbo" / "t", WIDER[0])
    status, text = run(f"spark.sql('SELECT 2L AS id, {WIDER[2]} AS v').write.mode('append').{how}('dbo.t')")
    assert status == "ok", text
    assert ids(lake / "dbo" / "t") == [1, 2]


# The SQL page's Save as table, append (Service.save_sql).
def save_sql(monkeypatch, tmp_path, data):
    svc = Service(client_id="x", root=tmp_path / "app")
    monkeypatch.setattr(svc, "_attach", lambda ref: None)
    monkeypatch.setattr(sql, "run", lambda query, dialect: data)
    return svc.save_sql("SELECT ...", LH, "dbo.t", "append")


@REFUSED
def test_save_as_table_append_stops(lake, monkeypatch, tmp_path, have, new, spark_sql, duck_sql):
    seed(lake / "dbo" / "t", have)
    with pytest.raises(ValueError, match=CHANGES):
        save_sql(monkeypatch, tmp_path, appended(2, new))
    assert version(lake / "dbo" / "t") == 0


def test_save_as_table_append_that_widens_writes(lake, monkeypatch, tmp_path):
    seed(lake / "dbo" / "t", WIDER[0])
    assert save_sql(monkeypatch, tmp_path, appended(2, WIDER[1]))["rows"] == 1
    assert ids(lake / "dbo" / "t") == [1, 2]


# A notebook the new notebook wizard made: its incremental load appends with lakehouse.write_table.
def run_wizard(lake, have, new):
    seed(lake / "silver" / "orders", have)
    write_deltalake(str(lake / "dbo" / "orders"), pa.table({"id": pa.array([1, 2], pa.int64()),
                                                            "v": pa.concat_arrays([new, new])}))
    built = wizard.build("incremental", {"source": "dbo.orders", "watermark": "id", "keys": "id",
                                         "output": "silver.orders"})
    scope: dict = {}
    for cell in built["cells"]:
        if cell["type"] == "code":
            exec(compile(cell["source"], "<cell>", "exec"), scope)  # noqa: S102 - the wizard's own fixed cells


@REFUSED
def test_wizard_append_stops(lake, have, new, spark_sql, duck_sql):
    with pytest.raises(ValueError, match=CHANGES):
        run_wizard(lake, have, new)
    assert version(lake / "silver" / "orders") == 0


def test_wizard_append_that_widens_writes(lake):
    run_wizard(lake, WIDER[0], WIDER[1])
    assert ids(lake / "silver" / "orders") == [1, 2]


# A notebook's background write (OneLake writes: local first), which uploads after the line ran.
@REFUSED
def test_background_append_stops_and_fails_its_cell(service, have, new, spark_sql, duck_sql):  # noqa: F811
    seed(service.onelake / "sales", have)
    code = ("import pyarrow as pa, datetime as dt\nfrom fabric_desktop import lakehouse\n"
            f"lakehouse.write_table(pa.table({{'id': pa.array([2], pa.int64()), 'v': {_literal(new)}}}), 'sales', mode='append')")
    out = service.run_cell(code, LH)
    assert out["status"] == "error" and CHANGES in out["outputs"][-1]["evalue"], out
    assert version(service.onelake / "sales") == 0


def test_background_append_that_widens_writes(service):  # noqa: F811
    seed(service.onelake / "sales", WIDER[0])
    code = ("import pyarrow as pa\nfrom fabric_desktop import lakehouse\n"
            "lakehouse.write_table(pa.table({'id': pa.array([2], pa.int64()), 'v': pa.array([2], pa.int32())}), 'sales', mode='append')")
    assert service.run_cell(code, LH)["status"] == "ok"
    assert ids(service.onelake / "sales") == [1, 2]


def _literal(column: pa.Array) -> str:
    if pa.types.is_timestamp(column.type):
        return "pa.array([dt.datetime(2026, 1, 2, 23, 30, tzinfo=dt.timezone.utc)], pa.timestamp('us', 'UTC'))"
    return repr(column.to_pylist())


# A job with an append target (publish.run_job).
class Fabric:
    def find_lakehouse(self, workspace_id, name):
        return {"id": "lh1"}


def run_job(tmp_path, duck_sql):
    job = jobs.Job("j", "duckdb", tmp_path, jobs.Target("ws1", "LH", "t", "dbo", "append"),
                   sql=f"SELECT 2::BIGINT AS id, {duck_sql} AS v")
    return publish.run_job(job, Fabric(), lambda: "tok", tmp_path)


@REFUSED
def test_job_append_stops(lake, tmp_path, have, new, spark_sql, duck_sql):
    seed(lake / "dbo" / "t", have)
    with pytest.raises(ValueError, match=CHANGES):
        run_job(tmp_path, duck_sql)
    assert version(lake / "dbo" / "t") == 0


def test_job_append_that_widens_writes(lake, tmp_path):
    seed(lake / "dbo" / "t", WIDER[0])
    run_job(tmp_path, WIDER[3])
    assert ids(lake / "dbo" / "t") == [1, 2]


# A proof run's write, which goes to the run's sandbox copy of the table (lakehouse._write_proof).
class Proof:
    def __init__(self, uri):
        self.uri = uri

    def options(self):
        return {}

    def write_location(self, schema, table, mode, write_delta):
        return self.uri


@pytest.fixture
def proof(lake, tmp_path, monkeypatch):
    sandbox = tmp_path / "sandbox" / "t"
    monkeypatch.setattr(lakehouse, "proof", Proof(sandbox.as_posix()))
    monkeypatch.setattr(lakehouse, "proof_exempt_thread", None)  # the test runs on the main thread, as the RPC loop does
    return sandbox


@REFUSED
def test_proof_append_stops(proof, have, new, spark_sql, duck_sql):
    seed(proof, have)
    with pytest.raises(ValueError, match=CHANGES):
        lakehouse.write_table(appended(2, new), "dbo.t", mode="append")
    assert version(proof) == 0


def test_proof_append_that_widens_writes(proof):
    seed(proof, WIDER[0])
    lakehouse.write_table(appended(2, WIDER[1]), "dbo.t", mode="append")
    assert ids(proof) == [1, 2]


# A background append whose data file upload dropped is written again, and its rows land once.
def test_an_append_tried_again_after_a_dropped_upload_adds_its_rows_once(service, monkeypatch):  # noqa: F811
    monkeypatch.setattr(lakehouse, "UPLOAD_RETRY_WAIT", 0.0)
    seed(service.onelake / "sales", pa.array([1], pa.int64()))
    upload, tries = lakehouse.publish, []

    def drops_once(data, *args, **kwargs):
        tries.append(1)
        if len(tries) == 1:
            raise OSError(DROPPED)
        return upload(data, *args, **kwargs)
    monkeypatch.setattr(lakehouse, "publish", drops_once)
    code = ("import pyarrow as pa\nfrom fabric_desktop import lakehouse\n"
            "lakehouse.write_table(pa.table({'id': pa.array([2, 3], pa.int64()), 'v': pa.array([2, 3], pa.int64())}), "
            "'sales', mode='append')")
    assert service.run_cell(code, LH)["status"] == "ok"
    assert len(tries) == 2 and ids(service.onelake / "sales") == [1, 2, 3] and version(service.onelake / "sales") == 1
