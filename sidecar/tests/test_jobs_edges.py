"""Jobs: the refusals before a run and the polars script edges the job tests don't reach. Runs stay in
temporary folders; nothing is published."""

import polars as pl
import pyarrow as pa
import pytest

from fabric_desktop.jobs import Job, Target, run_local

T = Target(workspace_id="ws", lakehouse="LH", table="orders")


@pytest.mark.parametrize("kw, message", [
    ({"engine": "duckdb"}, "need a `sql` query"),
    ({"engine": "polars"}, "need a `script` path"),
    ({"engine": "duckdb", "sql": "SELECT 1", "target": Target("ws", "LH", "orders", mode="merge")}, "target.mode"),
])
def test_a_job_missing_what_its_engine_needs_or_with_an_unknown_mode_is_refused(tmp_path, kw, message):
    job = Job(**({"name": "j", "base_dir": tmp_path, "target": T} | kw))
    with pytest.raises(ValueError, match=message):
        job.validate()


def test_a_script_without_transform_is_refused(tmp_path):
    (tmp_path / "t.py").write_text("x = 1\n", encoding="utf-8")
    with pytest.raises(ValueError, match="must define transform"):
        run_local(Job(name="j", engine="polars", base_dir=tmp_path, target=T, script="t.py"))


def test_a_transform_returning_plain_columns_becomes_an_arrow_table(tmp_path):
    (tmp_path / "t.py").write_text("def transform():\n    return {'a': [1, 2]}\n", encoding="utf-8")
    out = run_local(Job(name="j", engine="polars", base_dir=tmp_path, target=T, script="t.py"))
    assert isinstance(out, pa.Table) and out.column("a").to_pylist() == [1, 2]


def test_a_polars_frame_is_returned_as_arrow(tmp_path):
    (tmp_path / "t.py").write_text("import polars as pl\ndef transform():\n    return pl.DataFrame({'a': [3]})\n",
                                   encoding="utf-8")
    out = run_local(Job(name="j", engine="polars", base_dir=tmp_path, target=T, script="t.py"))
    assert pl.from_arrow(out).to_dicts() == [{"a": 3}]
