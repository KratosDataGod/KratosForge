"""Jobs: loading a job file, the job name checks, the engine check, that jobs and targets can't be
changed once made, and which DuckDB method turns the result into Arrow. Mutation batch 61."""

import dataclasses
import types

import pyarrow as pa
import pytest

from fabric_desktop import jobs
from fabric_desktop.jobs import Job, Target

T = Target(workspace_id="ws", lakehouse="LH", table="orders")


def _job(tmp_path, **kw):
    return Job(**({"name": "j", "engine": "duckdb", "base_dir": tmp_path, "target": T, "sql": "SELECT 1"} | kw))


def test_load_gives_the_job_with_its_folder_and_target(tmp_path):
    path = tmp_path / "job.toml"
    path.write_text('name = "sales"\nengine = "duckdb"\nsql = "SELECT 1"\n\n[target]\nworkspace_id = "ws"\n'
                    'lakehouse = "LH"\ntable = "sales"\nmode = "append"\n', encoding="utf-8")
    job = Job.load(path)
    assert isinstance(job, Job) and job.name == "sales" and job.base_dir == tmp_path.resolve()
    assert job.target == Target("ws", "LH", "sales", "dbo", "append")


@pytest.mark.parametrize("name", ["", "   ", ".", "..", "_writes", "a/b", "a\\b", "c:x", "../x"])
def test_a_job_name_with_other_characters_or_a_reserved_name_is_refused(tmp_path, name):
    with pytest.raises(ValueError, match="Invalid job name"):
        _job(tmp_path, name=name).validate()


@pytest.mark.parametrize("name", ["sales", "sales_by_region", "a.b", "...", "_write"])
def test_a_plain_job_name_is_accepted(tmp_path, name):
    _job(tmp_path, name=name).validate()


def test_an_unknown_engine_is_refused(tmp_path):
    with pytest.raises(ValueError, match="engine must be one of"):
        _job(tmp_path, engine="spark").validate()


def test_jobs_and_targets_cant_be_changed_once_made(tmp_path):
    job = _job(tmp_path)
    with pytest.raises(dataclasses.FrozenInstanceError):
        job.name = "other"  # type: ignore[misc]
    with pytest.raises(dataclasses.FrozenInstanceError):
        T.table = "other"  # type: ignore[misc]


class _Con:
    def __init__(self, rel):
        self.rel, self.ran = rel, []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql):
        self.ran.append(sql)

    def sql(self, query):
        self.ran.append(query)
        return self.rel


@pytest.mark.parametrize("method", ["to_arrow_table", "fetch_arrow_table"])
def test_the_result_is_read_with_whichever_arrow_method_duckdb_has(tmp_path, monkeypatch, method):
    table = pa.table({"a": [1]})
    rel = types.SimpleNamespace(**{method: lambda: table})
    con = _Con(rel)
    monkeypatch.setattr(jobs, "duckdb", types.SimpleNamespace(connect=lambda: con))
    monkeypatch.setattr(jobs, "keep_try_cast", lambda c: c)
    assert jobs.run_local(_job(tmp_path)) is table
    assert con.ran == ["SET TimeZone = 'UTC'", "SELECT 1"]
