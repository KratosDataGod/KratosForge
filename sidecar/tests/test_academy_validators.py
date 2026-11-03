"""Academy validators (ACD-004): each passes the reference answer and fails planted wrong answers
with a message that names the problem; learner outputs are read as untrusted input."""

from __future__ import annotations

import json
import os
from pathlib import Path

import duckdb
import polars as pl
import pytest
from deltalake import write_deltalake

from fabric_desktop.academy import validators as v
from fabric_desktop.academy import workspace as ws


@pytest.fixture
def work(tmp_path: Path) -> Path:
    folder = ws.workspace_dir(tmp_path, "dp-700", "E37")
    folder.mkdir(parents=True)
    return folder


@pytest.fixture
def con(work: Path) -> duckdb.DuckDBPyConnection:
    c = ws.connect(work)
    c.execute("CREATE TABLE dim_customer (customer_id INTEGER NOT NULL, name VARCHAR, tier VARCHAR, "
              "amount DECIMAL(18,2))")
    c.execute("INSERT INTO dim_customer VALUES (1,'a','gold',10),(2,'b','silver',20),(3,'c','gold',30)")
    c.execute("CREATE TABLE orders (order_id INTEGER, customer_id INTEGER)")
    c.execute("INSERT INTO orders VALUES (10,1),(11,2),(12,3)")
    return c


COLS = [{"name": "customer_id", "type": "INTEGER", "nullable": False}, {"name": "name", "type": "VARCHAR"},
        {"name": "tier"}, {"name": "amount", "type": "DECIMAL"}]


# ---- workspace

def test_workspace_dir_refuses_names_that_arent_ids(tmp_path: Path) -> None:
    for pack, ex in (("..", "E37"), ("dp-700", "../x"), ("dp-700", "a/b"), ("", "E1"), ("dp-700", "E1\x00")):
        with pytest.raises(ws.Refused):
            ws.workspace_dir(tmp_path, pack, ex)


@pytest.mark.parametrize("rel", ["../grading/E37/reference.sql", "/etc/passwd", "C:/Windows/win.ini",
                                 "file:///etc/passwd", "a/../../x", "..\\x", ""])
def test_paths_outside_the_workspace_are_refused(work: Path, rel: str) -> None:
    with pytest.raises(ws.Refused):
        ws.inside(work, rel)


def test_a_symlink_in_the_workspace_is_refused(work: Path, tmp_path: Path) -> None:
    secret = tmp_path / "grading.json"
    secret.write_text('{"answer": 42}')
    try:
        os.symlink(secret, work / "out.json")
    except OSError:
        pytest.skip("this PC can't make symbolic links")
    with pytest.raises(ws.Refused, match="link"):
        ws.read_json(work, "out.json")
    res = v.config_file_matches(work, "out.json", [{"path": "answer", "equals": 42}])
    assert not res["passed"] and "link" in res["detail"]


def test_a_linked_folder_is_refused(work: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "x.json").write_text("{}")
    try:
        os.symlink(outside, work / "out", target_is_directory=True)
    except OSError:
        pytest.skip("this PC can't make symbolic links")
    with pytest.raises(ws.Refused, match="link"):
        ws.read_json(work, "out/x.json")


def test_a_hard_link_is_refused(work: Path, tmp_path: Path) -> None:
    secret = tmp_path / "token_cache.json"
    secret.write_text('{"refresh": "x"}')
    os.link(secret, work / "copy.json")
    with pytest.raises(ws.Refused, match="hard link"):
        ws.read_json(work, "copy.json")
    with pytest.raises(ws.Refused, match="hard link"):
        ws.files_under(work, ".")


def test_the_workspace_itself_may_not_be_a_link(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    try:
        os.symlink(real, link, target_is_directory=True)
    except OSError:
        pytest.skip("this PC can't make symbolic links")
    with pytest.raises(ws.Refused):
        ws.inside(link, "x", must_exist=False)


def test_json_is_size_capped_and_deep_nesting_refused(work: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (work / "deep.json").write_text("[" * 100_000 + "]" * 100_000)
    with pytest.raises(ws.Refused):
        ws.read_json(work, "deep.json")
    monkeypatch.setattr(ws, "MAX_JSON_BYTES", 10)
    (work / "big.json").write_text('{"a": "' + "x" * 50 + '"}')
    with pytest.raises(ws.Refused, match="bigger"):
        ws.read_json(work, "big.json")


def test_the_validator_connection_reads_only_the_workspace(work: Path, tmp_path: Path) -> None:
    (tmp_path / "outside.csv").write_text("a\n1\n")
    (work / "inside.csv").write_text("a\n1\n2\n")
    c = ws.connect(work)
    assert ws.load_table(c, work, "inside.csv", "t", fmt="csv") == 2
    with pytest.raises(duckdb.Error):
        c.execute(f"SELECT * FROM read_csv('{tmp_path / 'outside.csv'}')").fetchall()
    with pytest.raises(duckdb.Error):
        c.execute("SET enable_external_access = true")


def test_load_table_caps_rows_and_names(work: Path) -> None:
    pl.DataFrame({"a": list(range(100))}).write_parquet(work / "big.parquet")
    c = ws.connect(work)
    with pytest.raises(ws.Refused, match="more than 10 rows"):
        ws.load_table(c, work, "big.parquet", "t", max_rows=10)
    with pytest.raises(ws.Refused):
        ws.load_table(c, work, "big.parquet", 't"; DROP TABLE x; --')
    assert ws.load_table(c, work, "big.parquet", "t") == 100
    assert v.schema_matches(c, "t", [{"name": "a", "type": "BIGINT"}])["passed"]


# ---- schema_matches

def test_schema_matches_reference(con: duckdb.DuckDBPyConnection) -> None:
    res = v.schema_matches(con, "dim_customer", COLS, key=["customer_id"], exact=True, ordered=True)
    assert res == {"kind": "schema_matches", "passed": True, "detail": res["detail"]}


def test_schema_matches_wrong_answers(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("CREATE TABLE wrong_type AS SELECT customer_id::VARCHAR AS customer_id, name, tier, amount FROM dim_customer")
    con.execute("CREATE TABLE extra AS SELECT *, 1 AS junk FROM dim_customer")
    con.execute("CREATE TABLE dupes AS SELECT * FROM dim_customer UNION ALL SELECT * FROM dim_customer")
    con.execute("CREATE TABLE nulls AS SELECT NULL::INTEGER AS customer_id, name, tier, amount FROM dim_customer")
    assert "should be INTEGER" in v.schema_matches(con, "wrong_type", COLS)["detail"]
    assert "junk" in v.schema_matches(con, "extra", COLS, exact=True)["detail"]
    assert "more than once" in v.schema_matches(con, "dupes", COLS, key=["customer_id"])["detail"]
    assert "NULL" in v.schema_matches(con, "nulls", COLS)["detail"]
    assert "no table" in v.schema_matches(con, "missing", COLS)["detail"]
    assert "no column" in v.schema_matches(con, "orders", COLS)["detail"]
    reordered = list(reversed(COLS))
    assert "order" in v.schema_matches(con, "dim_customer", reordered, ordered=True)["detail"]


# ---- data_quality

RULES = [{"rule": "unique", "columns": ["customer_id"]}, {"rule": "not_null", "column": "name"},
         {"rule": "range", "column": "amount", "min": 0, "max": 100},
         {"rule": "accepted", "column": "tier", "values": ["gold", "silver"]},
         {"rule": "row_count", "equals": 3}]


def test_data_quality_reference(con: duckdb.DuckDBPyConnection) -> None:
    assert v.data_quality(con, "dim_customer", RULES)["passed"]
    assert v.data_quality(con, "orders", [{"rule": "references", "column": "customer_id", "parent": "dim_customer"}])["passed"]


def test_data_quality_wrong_answers(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("INSERT INTO dim_customer VALUES (3,'c','gold',30)")
    assert "appear more than once" in v.data_quality(con, "dim_customer", RULES)["detail"]
    con.execute("DELETE FROM dim_customer WHERE customer_id = 3")
    con.execute("INSERT INTO dim_customer VALUES (4,NULL,'bronze',-5)")
    out = v.data_quality(con, "dim_customer", RULES)
    assert not out["passed"] and "NULL" in out["detail"] and "name" in out["detail"]
    out = v.data_quality(con, "dim_customer", RULES[2:])
    assert "outside the range" in out["detail"] and "amount" in out["detail"]
    assert "isn't one of" in v.data_quality(con, "dim_customer", RULES[3:])["detail"]
    con.execute("INSERT INTO orders VALUES (13, 99)")
    out = v.data_quality(con, "orders", [{"rule": "references", "column": "customer_id", "parent": "dim_customer"}])
    assert "2 rows" in out["detail"] and "dim_customer" in out["detail"]
    assert "couldn't run" in v.data_quality(con, "orders", [{"rule": "unique", "column": "nope"}])["detail"]
    assert "rows" in v.data_quality(con, "orders", [{"rule": "row_count", "equals": 3}])["detail"]


def test_data_quality_messages_never_echo_cell_values(con: duckdb.DuckDBPyConnection) -> None:
    con.execute("INSERT INTO dim_customer VALUES (9,'p@ssw0rd-secret','platinum',1)")
    out = v.data_quality(con, "dim_customer", RULES[3:4])
    assert "p@ssw0rd" not in out["detail"] and "platinum" not in out["detail"]


# ---- config_matches

SETTINGS = {"workspace": {"spark": {"pool": "starter", "max_nodes": 4, "high_concurrency": True}},
            "roles": [{"principal": "eng", "role": "Contributor"}], "admins": ["lead"]}
CONFIG_RULES = [{"path": "workspace.spark.pool", "equals": "starter"},
                {"path": "workspace.spark.max_nodes", "max": 8, "min": 1},
                {"path": "roles.0.role", "one_of": ["Viewer", "Contributor"], "why": "Least privilege: not Admin."},
                {"path": "admins", "not_contains": "eng"}, {"path": "guest_access", "absent": True}]


def test_config_matches_reference(work: Path) -> None:
    assert v.config_matches(SETTINGS, CONFIG_RULES)["passed"]
    (work / "settings.json").write_text(json.dumps(SETTINGS))
    assert v.config_file_matches(work, "settings.json", CONFIG_RULES)["passed"]


def test_config_matches_wrong_answers() -> None:
    admin = json.loads(json.dumps(SETTINGS))
    admin["roles"][0]["role"] = "Admin"
    out = v.config_matches(admin, CONFIG_RULES)
    assert not out["passed"] and "roles.0.role" in out["detail"] and "Least privilege" in out["detail"]
    big = json.loads(json.dumps(SETTINGS))
    big["workspace"]["spark"]["max_nodes"] = 64
    assert "at most 8" in v.config_matches(big, CONFIG_RULES)["detail"]
    guest = {**SETTINGS, "guest_access": True}
    assert "shouldn't be there" in v.config_matches(guest, CONFIG_RULES)["detail"]
    assert "isn't set" in v.config_matches({}, CONFIG_RULES)["detail"]
    text = json.loads(json.dumps(SETTINGS))
    text["workspace"]["spark"]["max_nodes"] = "4"
    assert "should be a number" in v.config_matches(text, CONFIG_RULES)["detail"]


def test_config_messages_hide_secret_looking_values() -> None:
    doc = {"conn": {"password": "hunter2-super-secret-value-that-is-long-enough"}}
    out = v.config_matches(doc, [{"path": "conn.password", "absent": True}])
    assert "hunter2" not in out["detail"] and "a different value" in out["detail"]
    out = v.config_matches({"k": "token=abc"}, [{"path": "k", "equals": "x"}])
    assert "abc" not in out["detail"]


# ---- artifact_present

def test_artifact_present(work: Path) -> None:
    (work / "out").mkdir()
    (work / "out" / "report.json").write_text(json.dumps({"rows": 3, "checks": {"dupes": 0}}))
    write_deltalake(str(work / "tables" / "sales"), pl.DataFrame({"a": [1]}).to_arrow())
    items = [{"path": "out", "kind": "folder"}, {"path": "out/report.json", "kind": "json", "keys": ["checks.dupes"], "min_bytes": 5},
             {"path": "tables/sales", "kind": "delta"}]
    assert v.artifact_present(work, items)["passed"]
    assert "isn't there" in v.artifact_present(work, [{"path": "out/missing.csv"}])["detail"]
    assert "has no checks.rows" in v.artifact_present(work, [{"path": "out/report.json", "kind": "json", "keys": ["checks.rows"]}])["detail"]
    assert "isn't a Delta table" in v.artifact_present(work, [{"path": "out", "kind": "delta"}])["detail"]
    assert "at most 2" in v.artifact_present(work, [{"path": "out/report.json", "max_bytes": 2}])["detail"]


# ---- delta_table

def _delta(work: Path) -> Path:
    path = work / "tables" / "sales"
    df = pl.DataFrame({"id": [1, 2, 3], "region": ["n", "s", "n"], "amount": [1.0, 2.0, 3.0]})
    write_deltalake(str(path), df.to_arrow(), partition_by=["region"])
    write_deltalake(str(path), df.to_arrow(), mode="append", partition_by=["region"])
    return path


def test_delta_table_reference(work: Path) -> None:
    _delta(work)
    out = v.delta_table(work, "tables/sales", versions_min=2, operations=["WRITE", "WRITE"],
                        operations_in_order=True, partition_by=["region"], files_max=4, columns=["id", "amount"])
    assert out["passed"], out


def test_delta_table_wrong_answers(work: Path) -> None:
    _delta(work)
    assert "at least 3" in v.delta_table(work, "tables/sales", versions_min=3)["detail"]
    assert "no MERGE" in v.delta_table(work, "tables/sales", operations=["MERGE"])["detail"]
    assert "partitioned by region" in v.delta_table(work, "tables/sales", partition_by=[])["detail"]
    assert "at most 1" in v.delta_table(work, "tables/sales", files_max=1)["detail"]
    assert "no column customer" in v.delta_table(work, "tables/sales", columns=["customer"])["detail"]
    assert "isn't there" in v.delta_table(work, "tables/nope")["detail"]


def _add_commit(table: Path, action: dict) -> None:
    log = table / "_delta_log"
    n = len(list(log.glob("*.json")))
    (log / f"{n:020d}.json").write_text(json.dumps(action) + "\n")


@pytest.mark.parametrize("bad", [
    {"add": {"path": "/etc/passwd", "size": 1}},
    {"add": {"path": "C:/Users/chris/AppData/Local/FabricDesktop/tokens.bin", "size": 1}},
    {"add": {"path": "file:///etc/passwd", "size": 1}},
    {"add": {"path": "abfss://ws@onelake.dfs.fabric.microsoft.com/x", "size": 1}},
    {"add": {"path": "..%2F..%2Fgrading%2Fref.parquet", "size": 1}},
    {"remove": {"path": "../../x.parquet"}},
    {"cdc": {"path": "/tmp/x.parquet"}},
    {"add": {"path": "part-0.parquet", "size": 1, "deletionVector": {"storageType": "p", "pathOrInlineDv": "/etc/x.bin"}}},
])
def test_delta_log_entries_pointing_outside_are_refused(work: Path, bad: dict) -> None:
    table = _delta(work)
    _add_commit(table, bad)
    out = v.delta_table(work, "tables/sales")
    assert not out["passed"] and ("outside" in out["detail"] or "absolute" in out["detail"])
    assert "passwd" not in out["detail"] and "chris" not in out["detail"]


def test_delta_checkpoints_and_bad_log_json_are_refused(work: Path) -> None:
    table = _delta(work)
    (table / "_delta_log" / "_last_checkpoint").write_text('{"version": 1}')
    assert "checkpoint" in v.delta_table(work, "tables/sales")["detail"]
    (table / "_delta_log" / "_last_checkpoint").unlink()
    (table / "_delta_log" / f"{2:020d}.json").write_text("{not json")
    assert "isn't valid Delta JSON" in v.delta_table(work, "tables/sales")["detail"]


def test_delta_log_that_is_a_hard_link_is_refused(work: Path, tmp_path: Path) -> None:
    table = _delta(work)
    other = tmp_path / "elsewhere.json"
    other.write_text("{}\n")
    os.link(other, table / "_delta_log" / f"{2:020d}.json")
    assert "hard link" in v.delta_table(work, "tables/sales")["detail"]


# ---- pipeline_outcome

RUN = {"state": "Succeeded", "stop_reason": "", "return_value": {"rows": 3},
       "activities": [{"name": "Lookup watermark", "status": "Succeeded", "output": {"firstRow": {"wm": "2026-01-01"}}},
                      {"name": "Load", "status": "Succeeded", "output": {"rowsCopied": 3}},
                      {"name": "On failure", "status": "Skipped"}]}


def test_pipeline_outcome_reference() -> None:
    out = v.pipeline_outcome(RUN, activities={"Load": "Succeeded", "On failure": "Skipped"},
                             outputs={"Lookup watermark.firstRow.wm": "2026-01-01", "Load.rowsCopied": 3},
                             return_value={"rows": 3})
    assert out["passed"], out


def test_pipeline_outcome_wrong_answers() -> None:
    failed = {**RUN, "state": "Failed", "stop_reason": "Load failed"}
    assert "ended Failed (Load failed)" in v.pipeline_outcome(failed)["detail"]
    assert "On failure ended Skipped" in v.pipeline_outcome(RUN, activities={"On failure": "Succeeded"})["detail"]
    assert "didn't run" in v.pipeline_outcome(RUN, activities={"Notify": "Succeeded"})["detail"]
    assert "rowsCopied is 3" in v.pipeline_outcome(RUN, outputs={"Load.rowsCopied": 4})["detail"]
    assert "has no rowsRead" in v.pipeline_outcome(RUN, outputs={"Load.rowsRead": 4})["detail"]
    assert "returns rows = 3" in v.pipeline_outcome(RUN, return_value={"rows": 4})["detail"]


def test_validators_are_deterministic(con: duckdb.DuckDBPyConnection, work: Path) -> None:
    _delta(work)
    for _ in range(3):
        assert v.data_quality(con, "dim_customer", RULES) == v.data_quality(con, "dim_customer", RULES)
        assert v.delta_table(work, "tables/sales", versions_min=2) == v.delta_table(work, "tables/sales", versions_min=2)


@pytest.mark.parametrize("rel", ["NUL", "con.txt", "out/COM1", "aux.json", "CONIN$", "lpt9.csv", "nul. "])
def test_windows_device_names_are_refused(work: Path, rel: str) -> None:
    with pytest.raises(ws.Refused, match="device"):
        ws.inside(work, rel, must_exist=False)


def test_load_table_needs_a_regular_file(work: Path) -> None:
    if not hasattr(os, "mkfifo"):
        pytest.skip("no named pipes here")
    os.mkfifo(work / "pipe.csv")
    with pytest.raises(ws.Refused, match="isn't a file"):
        ws.load_table(ws.connect(work), work, "pipe.csv", "t", fmt="csv")


@pytest.mark.parametrize("bad", [{"commitInfo": "WRITE"}, {"metaData": ["x"]}, {"add": {"path": "p.parquet", "size": "a secret 123"}},
                                 {"add": "p.parquet"}])
def test_malformed_log_entries_are_refused_without_echo(work: Path, bad: dict) -> None:
    table = _delta(work)
    _add_commit(table, bad)
    out = v.delta_table(work, "tables/sales")
    assert not out["passed"] and "secret" not in out["detail"]


def test_long_operation_names_are_cut(work: Path) -> None:
    table = _delta(work)
    _add_commit(table, {"commitInfo": {"operation": "X" * 5000}})
    out = v.delta_table(work, "tables/sales", operations=["MERGE"])
    assert not out["passed"] and len(out["detail"]) < 300
