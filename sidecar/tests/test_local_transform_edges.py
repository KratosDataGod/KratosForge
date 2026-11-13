"""SQL transformations in a local project: the edges the main tests don't reach. Each bad setting
or query a step file can hold, a dry run that reaches a step it can't check yet, a state entry this
build can't read, unreadable run records, and only the last runs being kept."""

from __future__ import annotations

import json

import pytest

from fabric_desktop.localdata import transform as tr
from fabric_desktop.localdata.transform import Transforms
from tests.test_local_transform import engine, local, step  # noqa: F401  (fixtures)


def test_a_project_without_steps_has_none(engine):
    assert Transforms(engine).steps() == []


def test_blank_lines_and_comments_before_the_query_are_read_past(engine):
    step(engine, "eu", "\n-- dialect: duckdb\n\n-- just a note\n-- materialize: table\nSELECT * FROM orders WHERE region = 'EU'")
    (s,) = Transforms(engine).steps()
    assert s.error == "" and s.sql.endswith("WHERE region = 'EU'")
    assert s.dialect == "duckdb" and s.materialize == "table"


@pytest.mark.parametrize("text, why", [
    ("-- dialect: tsql\nSELECT 1", "dialect must be duckdb or spark"),
    ("-- materialize: view\nSELECT 1", "materialize must be table or delta"),
    ("-- default: just words\nSELECT 1", "A default is written name = value"),
    ("-- default: start = 2026-01-01\nSELECT 1", "isn't a JSON value"),
    ("  ;  ", "The file has no query"),
    ("SELECT FROM WHERE (", "Could not read the query"),
])
def test_each_bad_step_file_says_what_is_wrong(engine, text, why):
    step(engine, "bad", text)
    (s,) = Transforms(engine).steps()
    assert why in s.error


def test_a_dry_run_doesnt_check_a_step_whose_input_this_run_makes(engine):
    step(engine, "base", "SELECT * FROM orders")
    step(engine, "top", "SELECT region FROM dbo.base")
    run = Transforms(engine).run(dry_run=True)
    top = next(s for s in run["steps"] if s["name"] == "dbo.top")
    assert top["status"] == "not checked" and "Reads dbo.base" in top["error"]
    assert run["status"] == "ok"


def test_a_state_entry_this_build_cant_read_is_treated_as_unknown(engine):
    step(engine, "eu", "SELECT * FROM orders WHERE region = 'EU'")
    t = Transforms(engine)
    t._save_state({"tables": {"dbo.eu": "made long ago"}})
    assert t.run()["status"] == "ok"  # nothing there yet, so it's made
    t._save_state({"tables": {"dbo.eu": ["not", "a", "dict"]}})
    run = t.run()  # the table is there now, and the state can't say a run made it
    assert run["status"] == "failed" and "wasn't made by a transformation" in run["steps"][0]["error"]


def test_an_unreadable_run_record_is_skipped(engine):
    step(engine, "eu", "SELECT * FROM orders WHERE region = 'EU'")
    t = Transforms(engine)
    t.run()
    (t.root / ".runs" / "99999999T999999Z-broken.json").write_text("{not json", encoding="utf-8")
    runs = t.runs()
    assert len(runs) == 1 and runs[0]["status"] == "ok"


def test_only_the_last_runs_are_kept(engine, monkeypatch):
    monkeypatch.setattr(tr, "_KEEP_RUNS", 2)
    step(engine, "eu", "SELECT * FROM orders WHERE region = 'EU'")
    t = Transforms(engine)
    folder = t.root / ".runs"
    folder.mkdir(parents=True)
    for n in range(3):
        (folder / f"2020010{n}T000000Z-old{n}.json").write_text(json.dumps({"run_id": f"old{n}"}), encoding="utf-8")
    t.run()
    kept = sorted(p.name for p in folder.glob("*.json"))
    assert len(kept) == 2 and kept[0] == "20200102T000000Z-old2.json"


def test_a_step_cant_switch_how_its_table_is_kept(engine):
    path = step(engine, "eu", "SELECT * FROM orders WHERE region = 'EU'")
    t = Transforms(engine)
    assert t.run()["status"] == "ok"
    path.write_text("-- materialize: delta\nSELECT * FROM orders WHERE region = 'EU'", encoding="utf-8")
    run = t.run()
    assert run["status"] == "failed"
    assert "was made as a table by an earlier run. Drop it first" in run["steps"][0]["error"]
