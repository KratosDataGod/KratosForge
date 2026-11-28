"""Mutation testing batch 3: pins for the local SQL transformations (localdata/transform.py) that the
existing transform tests left open. See docs/quality/mutation-testing.md."""

import json
import re

import pytest

from fabric_desktop.localdata import LocalData
from fabric_desktop.localdata import transform as T
from fabric_desktop.localdata.transform import Transforms

STAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


@pytest.fixture
def local(tmp_path):
    local = LocalData(tmp_path / "app" / "local-data")
    yield local
    local.close_all()


@pytest.fixture
def engine(local):
    e = local.engine(local.create("T")["id"])
    e.execute("CREATE TABLE orders AS SELECT * FROM (VALUES (1, 'EU', 10.0), (2, 'US', 20.0)) t(id, region, amount)")
    return e


def step(engine, name, text, schema="dbo"):
    path = engine.project.root / "transforms" / schema / f"{name}.sql"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def _run_files(t, n):
    folder = t.root / ".runs"
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(n):
        (folder / f"2026010{i // 10}T0000{i % 10:02d}Z-{i:08x}.json").write_text(json.dumps({"run_id": f"{i:08x}"}),
                                                                             encoding="utf-8")
    return folder


# Reading a step


def test_a_comment_line_before_the_settings_doesnt_end_them(engine):
    step(engine, "s", "-- a note about this step\n-- dialect: spark\nSELECT 1 AS a")
    (s,) = Transforms(engine).steps()
    assert s.error == "" and s.dialect == "spark"


def test_a_cte_is_not_read_as_a_table(engine):
    step(engine, "s", "WITH eu AS (SELECT * FROM orders WHERE region = 'EU') SELECT * FROM eu")
    (s,) = Transforms(engine).steps()
    assert s.reads == ["dbo.orders"]


def test_a_bad_default_names_its_parameter(engine):
    step(engine, "s", "-- default: start = not-json\nSELECT 1")
    (s,) = Transforms(engine).steps()
    assert s.error.startswith("The default for start isn't a JSON value") and s.error.endswith("not-json")


def test_the_plan_lists_a_broken_step_after_the_ordered_ones(engine):
    step(engine, "good", "SELECT 1 AS a")
    step(engine, "bad", "-- dialect: cobol\nSELECT 1")
    plan = Transforms(engine).plan()
    assert plan["order"] == ["dbo.good"]
    assert [s["name"] for s in plan["steps"]] == ["dbo.good", "dbo.bad"]
    assert "dialect must be duckdb or spark" in plan["steps"][1]["error"]


# A run's record


def test_a_run_records_its_times_and_rounds_the_seconds(engine):
    step(engine, "s", "SELECT 1 AS a")
    run = Transforms(engine).run()
    assert STAMP.match(run["started_at"]) and STAMP.match(run["finished_at"])
    secs = run["steps"][0]["seconds"]
    assert 0 <= secs < 60 and secs == round(secs, 3)


def test_a_run_with_no_transforms_folder_is_still_kept(engine):
    t = Transforms(engine)
    assert t.runs() == []  # no .runs folder yet: an empty list, not None
    run = t.run()
    assert run["status"] == "ok" and run["steps"] == []
    (kept,) = t.runs()
    assert kept["run_id"] == run["run_id"]
    (f,) = (t.root / ".runs").glob("*.json")
    assert re.match(r"^\d{8}T\d{6}Z-[0-9a-f]{8}\.json$", f.name) and f.name.endswith(f"-{run['run_id'][:8]}.json")


def test_runs_lists_newest_first_and_clamps_the_limit(engine):
    t = Transforms(engine)
    _run_files(t, 30)
    assert len(t.runs()) == 20  # the default
    newest = t.runs(1)
    assert [r["run_id"] for r in newest] == [f"{29:08x}"]
    assert len(t.runs(0)) == 1  # at least one
    assert len(t.runs(1000)) == 30


def test_only_the_last_fifty_runs_are_kept(engine):
    t = Transforms(engine)
    folder = _run_files(t, 55)
    t._keep({"run_id": "f" * 32})
    names = sorted(p.name for p in folder.glob("*.json"))
    assert len(names) == 50 and len(t.runs(1000)) == 50
    assert names[0].endswith(f"-{6:08x}.json")  # 56 runs: the six oldest went
    assert names[-1].endswith("-ffffffff.json")  # the new one stays
    assert T._KEEP_RUNS == 50


def test_the_state_file_is_indented_json(engine):
    step(engine, "s", "SELECT 1 AS a")
    t = Transforms(engine)
    t.run()
    text = (t.root / ".state.json").read_text(encoding="utf-8")
    assert text.startswith('{\n  "tables": {\n    "dbo.s"')


# A dry run plans the query and reads no rows


@pytest.mark.parametrize("dialect, wrapped", [
    ("duckdb", "SELECT * FROM (-- dialect: duckdb\nSELECT 1 AS a) LIMIT 0"),
    ("spark", "SELECT * FROM (-- dialect: spark\nSELECT 1 AS a) AS q LIMIT 0"),  # Spark needs the alias
])
def test_a_dry_run_plans_each_query_with_no_rows_and_no_count(engine, monkeypatch, dialect, wrapped):
    step(engine, "s", f"-- dialect: {dialect}\nSELECT 1 AS a")
    seen = []
    real = engine.execute

    def spy(sql, *args, **kw):
        seen.append((sql, kw))
        return real(sql, *args, **kw)

    monkeypatch.setattr(engine, "execute", spy)
    run = Transforms(engine).run(dry_run=True)
    assert run["steps"][0]["status"] == "checked"
    checks = [(sql, kw) for sql, kw in seen if sql.endswith("LIMIT 0")]
    assert checks == [(wrapped, {"dialect": dialect, "limit": 0, "count": False, "params": {}})]


def test_marking_a_table_doesnt_count_rows(engine, monkeypatch):
    step(engine, "s", "SELECT 1 AS a")
    seen = []
    real = engine.execute

    def spy(sql, *args, **kw):
        seen.append((sql, kw))
        return real(sql, *args, **kw)

    monkeypatch.setattr(engine, "execute", spy)
    Transforms(engine).run()
    (comment,) = [kw for sql, kw in seen if sql.startswith("COMMENT ON TABLE")]
    assert comment == {"count": False}


# Delta steps are known by their version


def test_a_delta_step_is_marked_with_its_version(engine):
    step(engine, "lake", "-- materialize: delta\nSELECT * FROM orders")
    t = Transforms(engine)
    t.run()
    first = json.loads((t.root / ".state.json").read_text(encoding="utf-8"))["tables"]["dbo.lake"]["mark"]
    assert first == "version:0"
    t.run()
    second = json.loads((t.root / ".state.json").read_text(encoding="utf-8"))["tables"]["dbo.lake"]["mark"]
    assert second == "version:1"


def test_a_delta_step_written_another_way_since_isnt_replaced(engine):
    step(engine, "lake", "-- materialize: delta\nSELECT * FROM orders")
    t = Transforms(engine)
    assert t.run()["status"] == "ok"
    engine.write_delta("dbo.lake", "SELECT 9 AS id, 'X' AS region, 1.0 AS amount", mode="append")
    run = t.run()
    assert run["status"] == "failed"
    assert "has changed since a transformation made it" in run["steps"][0]["error"]


def test_a_dry_run_checks_a_step_reading_a_built_delta_step(engine):
    step(engine, "lake", "-- materialize: delta\nSELECT * FROM orders")
    step(engine, "report", "SELECT count(*) AS n FROM dbo.lake")
    t = Transforms(engine)
    assert t.run()["status"] == "ok"
    run = t.run(dry_run=True)
    assert {s["name"]: s["status"] for s in run["steps"]} == {"dbo.lake": "checked", "dbo.report": "checked"}
