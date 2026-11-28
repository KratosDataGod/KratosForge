"""WR-218: the warehouse live check, dry-run against fakes. Nothing here connects to Fabric.

The check runs each documented local T-SQL case (WR-206 to WR-217, and WR-229) in a test warehouse and records the local,
documented and warehouse answers. These tests prove its guards: every case's SQL reads, or writes only in the
scratch schema; an unmarked or production-looking workspace or warehouse is refused before connecting; each case
is rolled back; and the scratch schema is dropped and checked gone at the end.
"""

from __future__ import annotations

import json
import re

import pytest

from fabric_desktop import smoketest, warehouse_livecheck as w

RUN = "abc123def456"  # pragma: allowlist secret (a made-up run id, not a credential)
SCRATCH = f"fdlt_{RUN}"
TEST_WS = {"id": "ws1", "displayName": "kf test", "description": "fabric-desktop-live-test"}
WAREHOUSE = {"id": "wh1", "type": "Warehouse", "displayName": "WH_Test", "workspaceId": "ws1"}


class Fabric:
    def __init__(self, workspace=None, warehouse=None):
        self.workspace = workspace or TEST_WS
        self.warehouse = warehouse or WAREHOUSE

    def get_workspace(self, workspace_id):
        return dict(self.workspace, id=workspace_id)

    def get_warehouse(self, workspace_id, warehouse_id):
        return dict(self.warehouse)


class Cursor:
    def __init__(self, con):
        self.con = con
        self.row = None

    def execute(self, sql, params=()):
        self.con.log.append(sql)
        self.row = None
        if sql.startswith("SELECT TABLE_NAME, TABLE_TYPE FROM INFORMATION_SCHEMA.TABLES"):
            self.rows = list(self.con.left)
            return
        if sql.startswith("SELECT SCHEMA_ID("):
            self.row = (1 if self.con.schema_stays else None,)
            return
        answer = self.con.answers(sql)
        if isinstance(answer, Exception):
            raise answer
        self.row = answer

    def nextset(self):
        pending = self.con.pending
        if pending:
            item = pending.pop(0)
            if isinstance(item, Exception):
                raise item
            return True
        return False

    def fetchone(self):
        return self.row

    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self, answers=lambda sql: (1,), left=(), schema_stays=False):
        self.answers, self.left, self.schema_stays = answers, left, schema_stays
        self.pending: list = []  # what nextset gives after the next statement: True, or an error to raise
        self.log: list[str] = []
        self.closed = False

    def cursor(self):
        return Cursor(self)

    def commit(self):
        self.log.append("COMMIT")

    def rollback(self):
        self.log.append("ROLLBACK")

    def close(self):
        self.closed = True


def check(tmp_path, con=None, fabric=None, cases=w.CASES):
    return w.WarehouseCheck("ws1", "wh1", fabric or Fabric(), (lambda: con) if con else None, tmp_path / "wh",
                            run_id=RUN, cases=cases)


# ---- the guard

def test_every_case_reads_or_writes_only_in_the_scratch_schema():
    assert w.problems(w.CASES, SCRATCH) == []
    assert len({c.id for c in w.CASES}) == len(w.CASES)
    assert {c.wr for c in w.CASES} <= {f"WR-2{n:02d}" for n in range(6, 18)} | {"WR-229"}


@pytest.mark.parametrize(("sql", "words"), [
    ("SELECT 1 INTO dbo.x", "Select isn't allowed"),
    ("INSERT INTO dbo.t VALUES (1)", "names dbo.t, outside the scratch schema"),
    ("DELETE FROM t", "names t, outside the scratch schema"),
    ("UPDATE t SET v = 1 FROM dbo.x AS t", "outside the scratch schema"),
    ("DROP SCHEMA dbo", "DROP SCHEMA isn't allowed"),
    ("CREATE SCHEMA other", "CREATE SCHEMA isn't allowed"),
    ("DROP TABLE dbo.t", "names dbo.t"),
    ("CREATE TABLE otherdb.{s}.t (a INT)", "outside the scratch schema"),
    ("GRANT SELECT ON dbo.t TO alice", "isn't allowed"),
    ("EXEC dbo.p", "isn't allowed"),
    ("TRUNCATE TABLE {s}.t", "isn't allowed"),
    ("SELECT 1; DELETE FROM dbo.t", "names dbo.t"),
    # only an UPDATE or DELETE target binds to a FROM alias; any other bare name means dbo
    ("INSERT INTO orders SELECT 1 FROM {s}.x AS orders", "names orders, outside"),
    ("INSERT INTO t SELECT * FROM {s}.x AS t", "names t, outside"),
    ("CREATE TABLE orders AS SELECT 1 AS a FROM {s}.x AS orders", "names orders, outside"),
    ("CREATE VIEW v AS SELECT a FROM {s}.x AS v", "names v, outside"),
    ("UPDATE t SET v = 1 FROM {s}.x AS c JOIN dbo.y AS t ON c.id = t.id", "outside the scratch schema"),
    ("DELETE t FROM dbo.x AS t", "outside the scratch schema"),
    ("UPDATE t SET v = 1 FROM {s}.x AS c", "names t, outside"),
    # an alias inside a subquery isn't visible to the target, which then means dbo
    ("UPDATE orders SET v = 1 WHERE EXISTS (SELECT 1 FROM {s}.y AS orders)", "names orders, outside"),
    ("DELETE FROM orders WHERE id IN (SELECT id FROM {s}.y AS orders)", "names orders, outside"),
    ("UPDATE orders SET v = 1 FROM {s}.x AS z WHERE EXISTS (SELECT 1 FROM {s}.y AS orders)", "names orders, outside"),
    ("DELETE orders FROM {s}.x AS z WHERE z.id IN (SELECT id FROM {s}.y AS orders)", "names orders, outside"),
    ("UPDATE orders SET v = (SELECT 1 FROM {s}.y AS orders) FROM {s}.x AS z", "names orders, outside"),
])
def test_anything_else_is_refused_before_connecting(sql, words):
    assert words in (w.statement_problem(sql.replace("{s}", SCRATCH), SCRATCH) or "")


@pytest.mark.parametrize("sql", [
    "UPDATE t SET v = b.v FROM {s}.ua AS t JOIN {s}.ub AS b ON t.id = b.id",
    "DELETE t FROM {s}.x AS t",
    "DELETE t FROM {s}.x AS t JOIN {s}.y AS u ON t.id = u.id",
    "UPDATE {s}.x SET v = 1",
])
def test_an_update_or_delete_through_a_scratch_alias_is_allowed(sql):
    assert w.statement_problem(sql.replace("{s}", SCRATCH), SCRATCH) is None


def test_a_scratch_name_must_be_the_runs_own():
    assert "isn't a scratch schema name" in w.statement_problem("SELECT 1", "dbo")


def test_a_case_that_breaks_the_guard_stops_the_whole_check(tmp_path):
    bad = (w.Case("BAD", "WR-206", "x", "x", "DELETE FROM dbo.t", None),)
    con = Connection()
    result = check(tmp_path, con, cases=w.CASES[:1] + bad).run()
    assert result["outcome"] == "refused" and "BAD: it names dbo.t" in result["refused"][0]
    assert con.log == []


# ---- the workspace and warehouse

@pytest.mark.parametrize(("workspace", "warehouse", "words"), [
    ({"displayName": "kf test", "description": ""}, WAREHOUSE, "doesn't contain fabric-desktop-live-test"),
    ({"displayName": "Sales prod", "description": "fabric-desktop-live-test"}, WAREHOUSE, "looks like production"),
    (TEST_WS, dict(WAREHOUSE, displayName="WH_PRD"), "warehouse name 'WH_PRD' looks like production"),
    (TEST_WS, dict(WAREHOUSE, workspaceId="other"), "isn't in the test workspace"),
    (TEST_WS, dict(WAREHOUSE, type="SQLEndpoint"), "not a Warehouse"),
])
def test_a_workspace_or_warehouse_that_isnt_for_tests_is_refused(tmp_path, workspace, warehouse, words):
    con = Connection()
    result = check(tmp_path, con, Fabric(workspace, warehouse)).run()
    assert result["outcome"] == "refused" and any(words in r for r in result["refused"])
    assert con.log == []


# ---- the dry run

def test_the_dry_run_gives_local_answers_and_connects_nowhere(tmp_path):
    result = check(tmp_path).dry_run()
    assert result["outcome"] == "dry run" and result["refused"] == []
    assert not (tmp_path / "wh").exists()
    by = {c["id"]: c for c in result["cases"]}
    assert all(c["warehouse"] is None and c["verdict"] == "not run" for c in by.values())
    assert all(SCRATCH in " ".join(c["sql"]) or "{s}" not in "".join(w.CASES[i].setup + (w.CASES[i].query,))
               for i, c in enumerate(by.values()))
    # where the docs say local matches the warehouse, it gives the documented answer
    for id in ("DEC-01", "DEC-02", "DEC-03", "DT-01", "DT-02", "DT-03", "DT-04", "DT-05", "ORD-01", "ORD-03",
               "TXT-02", "TXT-03", "TXT-04", "TXT-05", "AR-01", "AR-02", "AR-03", "AR-04", "DEC-04", "SET-02",
               "FMT-01", "FMT-02", "FMT-03"):
        assert w.same(by[id]["local"], by[id]["documented"]), (id, by[id]["local"])
    # where the docs say local differs, it gives the documented local answer or stops by name
    assert {id: by[id]["local"] for id in ("TXT-06", "TBL-01", "TBL-02", "TBL-03", "VW-01")} == {
        "TXT-06": "2", "TBL-01": "YES", "TBL-02": "2", "TBL-03": "5", "VW-01": "2"}
    for id in ("ORD-02", "TXT-01", "CHG-01", "CHG-02", "SCR-01", "SET-01"):
        assert by[id]["local"].startswith("error: "), id
    # live run 11 settled every case the reference doesn't; WR-229's .NET splits wait for the next live run
    assert [c.id for c in w.CASES if c.documented is None] == ["FMT-04", "FMT-05", "FMT-06", "FMT-07", "FMT-08"]
    for id in ("FMT-04", "FMT-05", "FMT-06", "FMT-07", "FMT-08"):
        assert by[id]["local"].startswith("error: "), id


# ---- the run, against a fake warehouse

def _answers(table):
    def answer(sql):
        for pattern, value in table:
            if re.search(pattern, sql):
                return value
        return (1,)
    return answer


def test_a_run_rolls_back_each_case_and_drops_the_scratch_schema(tmp_path):
    cases = tuple(c for c in w.CASES if c.id in ("DEC-01", "TXT-03", "TXT-06", "VW-01"))
    con = Connection(_answers([(r"^SELECT 1\.0 / 3$", ("0.333333",)), (r"LIKE 'abc_'", (0,)),
                               (r"GROUP BY v", (1,)), (r"SELECT b FROM", RuntimeError("Invalid column name 'b'."))]),
                     left=[("vt", "BASE TABLE"), ("vv", "VIEW")])
    result = check(tmp_path, con, cases=cases).run()
    log = con.log
    assert log[:2] == [f"CREATE SCHEMA [{SCRATCH}]", "COMMIT"]
    assert log.count("ROLLBACK") == len(cases)
    assert log.index(f"DROP VIEW [{SCRATCH}].[vv]") < log.index(f"DROP TABLE [{SCRATCH}].[vt]")
    assert log[-3:-1] == [f"DROP SCHEMA [{SCRATCH}]", "COMMIT"] and log[-1].startswith("SELECT SCHEMA_ID(")
    assert con.closed
    assert all(SCRATCH in s or not re.match(r"(CREATE|INSERT|UPDATE|DELETE|DROP|ALTER)", s) for s in log)
    by = {c["id"]: c for c in result["cases"]}
    assert by["DEC-01"]["verdict"] == "as documented"
    assert by["TXT-03"]["verdict"] == "docs are wrong"  # this fake warehouse says 0, where live run 11 said 1
    assert by["TXT-06"]["verdict"] == "documented difference"
    assert by["VW-01"]["warehouse"] == "error: Invalid column name 'b'." and by["VW-01"]["verdict"] == \
        "documented difference"
    assert result["outcome"] == "findings" and result["cleanup"] == f"Dropped {SCRATCH} and 2 object(s) left in it."
    saved = json.loads((tmp_path / "wh" / "warehouse-check.json").read_text(encoding="utf-8"))
    assert saved["counts"] == result["counts"]
    report = (tmp_path / "wh" / "warehouse-check.md").read_text(encoding="utf-8")
    assert "| TXT-03 (WR-210) |" in report and "docs are wrong" in report


def test_an_error_part_way_through_a_batch_is_noted_and_the_rest_is_read(tmp_path):
    case = next(c for c in w.CASES if c.id == "SCR-01")

    def answers(sql):
        if sql.startswith("INSERT INTO") and "VALUES (1)" in sql:
            con.pending = [True, RuntimeError("Divide by zero error encountered."), True]
        return (2,) if sql.startswith("SELECT COUNT(*)") else None

    con = Connection(answers)
    switched = []
    Connection.autocommit = property(lambda self: switched[-1] if switched else False,
                                     lambda self, on: switched.append(on))
    try:
        result = check(tmp_path, con, cases=(case,)).run()
    finally:
        del Connection.autocommit
    got = result["cases"][0]
    assert got["warehouse"] == "2" and got["verdict"] == "documented difference"
    assert got["notes"] == ["error: Divide by zero error encountered."] and con.pending == []
    # outside a transaction: an error in one dooms a warehouse's whole transaction, and took the table with it
    assert switched == [True, False]


def test_a_setup_the_warehouse_refuses_is_the_cases_answer(tmp_path):
    case = next(c for c in w.CASES if c.id == "TBL-02")
    con = Connection(lambda sql: RuntimeError("The data type 'char' isn't supported.") if "CHAR(5)" in sql else (5,))
    got = check(tmp_path, con, cases=(case,)).run()["cases"][0]
    assert got["warehouse"] == "error: The data type 'char' isn't supported." and got["verdict"] == "docs are wrong"
    assert not any(s.startswith("INSERT") for s in con.log)


def test_a_bad_scratch_name_is_refused_even_with_no_cases():
    assert w.problems((), "dbo") == ["'dbo' isn't a scratch schema name"]


def test_docs_that_the_warehouse_contradicts_are_a_finding(tmp_path):
    cases = tuple(c for c in w.CASES if c.id == "DEC-01")
    result = check(tmp_path, Connection(lambda sql: ("0.3333333",)), cases=cases).run()
    assert result["cases"][0]["verdict"] == "docs are wrong" and result["outcome"] == "findings"


def test_a_scratch_schema_that_stays_is_an_error(tmp_path):
    with pytest.raises(RuntimeError, match=f"The scratch schema {SCRATCH} is still there"):
        check(tmp_path, Connection(schema_stays=True), cases=w.CASES[:1]).run()


def test_a_token_in_an_error_never_reaches_the_results(tmp_path):
    token = "eyJ0eXAiOiJKV1QiLCJhbGciOi.eyJhdWQiOiJodHRwczovL2RhdGFiYXNl.sig"
    con = Connection(lambda sql: RuntimeError(f"failed for {token}") if sql.startswith("SELECT 1.0") else (1,))
    result = check(tmp_path, con, cases=w.CASES[:1]).run()
    assert result["cases"][0]["warehouse"].startswith("error: failed for")
    assert token not in (tmp_path / "wh" / "warehouse-check.json").read_text(encoding="utf-8")


@pytest.mark.parametrize(("a", "b", "same"), [
    ("1.50", "1.5", True), ("0.333333", "0.3333333", False), ("error: overflow", "error: Arithmetic overflow error", True),
    ("error: overflow", "1", False), ("NULL", "NULL", True), ("ab", "ab", True), (None, "1", False),
])
def test_answers_compare_by_value_and_errors_by_their_words(a, b, same):
    assert w.same(a, b) is same


# ---- flow 17 in the smoke test

class Credential:
    def fabric_token(self):
        return "t"

    storage_token = warehouse_token = kusto_token = fabric_token

    def signed_in_user(self):
        return ""

    def signed_in_tenant(self):
        return ""


class SmokeFabric(Fabric):
    def list_workspaces(self):
        return [{"id": "ws1"}]

    def list_items(self, workspace_id):
        return []


def test_flow_17_runs_only_with_warehouse_and_its_findings_dont_fail_it(tmp_path):
    con = Connection(lambda sql: ("0.3333333",))
    made = []

    def warehouse(out):
        made.append(out)
        return w.WarehouseCheck("ws1", "wh1", SmokeFabric(), lambda: con, out, run_id=RUN, cases=w.CASES[:1])

    bundle = lambda out: _bundle(out)  # noqa: E731
    without = smoketest.SmokeTest("ws1", Credential(), SmokeFabric(), tmp_path / "a", support_bundle=bundle).run()
    assert 17 not in [f["n"] for f in without["flows"]] and without["warehouse_check"] is None
    result = smoketest.SmokeTest("ws1", Credential(), SmokeFabric(), tmp_path / "b", support_bundle=bundle,
                                 warehouse_check=warehouse, run_id="r1").run()
    assert made == [tmp_path / "b" / "r1" / "warehouse"]
    flow = next(f for f in result["flows"] if f["n"] == 17)
    assert flow["automated"] == "passed" and flow["features"] == ["FEAT-FAB-11", "FEAT-SQL-01"]
    steps = {s["id"]: s for s in result["steps"] if s["flow"] == 17}
    assert steps["warehouse.check"]["status"] == "passed"
    assert steps["warehouse.findings"]["status"] == "failed" and not steps["warehouse.findings"]["required"]
    assert result["warehouse_check"]["outcome"] == "findings"


def test_a_refused_warehouse_check_fails_flow_17(tmp_path):
    def warehouse(out):
        return w.WarehouseCheck("ws1", "wh1", SmokeFabric(dict(TEST_WS, description="")), lambda: Connection(), out,
                                run_id=RUN, cases=w.CASES[:1])

    result = smoketest.SmokeTest("ws1", Credential(), SmokeFabric(), tmp_path, support_bundle=_bundle,
                                 warehouse_check=warehouse).run()
    assert next(f for f in result["flows"] if f["n"] == 17)["automated"] == "failed"
    assert "fabric-desktop-live-test" in next(s for s in result["steps"] if s["id"] == "warehouse.check")["detail"]


def test_the_smoke_test_plan_names_flow_17_only_with_warehouse(tmp_path):
    plan = smoketest.SmokeTest("ws1", None, None, tmp_path, warehouse_check=lambda out: None).plan()
    assert any(p.startswith("Flow 17 (--warehouse)") for p in plan)
    assert not any("Flow 17" in p for p in smoketest.SmokeTest("ws1", None, None, tmp_path).plan())
    assert all(f.features and f.by_hand for f in smoketest.WAREHOUSE_FLOWS)


def _bundle(out):
    import zipfile
    path = out / "bundle.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("a.txt", "nothing personal")
    return path
