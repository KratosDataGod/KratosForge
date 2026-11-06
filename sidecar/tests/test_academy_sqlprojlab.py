"""sqlproj_lab exercises: a warehouse database project changed by the learner's change set, read as text
and built in an in-memory DuckDB with no file or network access. A file holds one object in Fabric's Git
layout, every table and view builds, and a deploy from the warehouse drops nothing and rebuilds no table."""

from __future__ import annotations

import functools
import json
import shutil

import pytest

from fabric_desktop.academy import exercises, lab, registry, sqlprojlab
from fabric_desktop.academy_service import AcademyMixin

PACK, EID = "dp-700", "E36"
FILES = {
    "dimcustomer.sql": "CREATE TABLE [dbo].[DimCustomer]\n(\n    [CustomerKey] INT NOT NULL,\n    [Name] VARCHAR(100) NULL,\n"
                       "    [Segment] VARCHAR(20) NULL\n);\n",
    "vw_seg.sql": "CREATE VIEW [dbo].[vw_CustomersBySegment]\nAS\nSELECT [Segment], COUNT(*) AS [Customers]\n"
                  "FROM [dbo].[DimCustomer]\nGROUP BY [Segment];\n",
    "usp.sql": "CREATE PROCEDURE dbo.usp_Refresh AS BEGIN SELECT 1 AS one; END\n",
    "seed.sql": "INSERT INTO dbo.DimCustomer VALUES (1, 'Ana', 'Retail'), (2, 'Ben', 'Retail'), (3, 'Cy', 'Corporate');\n"
                "INSERT INTO dbo.FactOrders VALUES (10, 1, 5.00), (11, 3, 7.50), (12, 1, 2.50);\n",
}
PROJECT = {"dbo/Tables/DimCustomer.sql": "dimcustomer.sql", "dbo/Views/vw_CustomersBySegment.sql": "vw_seg.sql",
           "dbo/StoredProcedures/usp_Refresh.sql": "usp.sql"}
VALIDATION = [
    {"kind": "project_builds", "fail": "Every file defines one object in its place, and every table and view builds."},
    {"kind": "objects_present", "objects": [
        {"kind": "table", "schema": "dbo", "name": "FactOrders", "columns": ["OrderKey", "CustomerKey", "Amount"]},
        {"kind": "view", "schema": "dbo", "name": "vw_SalesBySegment"}]},
    {"kind": "deploy_safe", "fail": "A deploy must not break what the warehouse already has."},
    {"kind": "result_matches", "query": "SELECT Segment, Sales FROM dbo.vw_SalesBySegment"},
]
EX = {"id": EID, "starter": "changes.sql", "files": sorted(FILES), "project": PROJECT, "seed": "seed.sql",
      "validation": VALIDATION}
TABLE = ("-- file: dbo/Tables/FactOrders.sql\n"
         "CREATE TABLE dbo.FactOrders (OrderKey INT NOT NULL, CustomerKey INT NOT NULL, Amount DECIMAL(18, 2) NULL);\n")
VIEW = ("-- file: dbo/Views/vw_SalesBySegment.sql\nCREATE VIEW dbo.vw_SalesBySegment AS\n"
        "SELECT c.Segment, SUM(o.Amount) AS Sales FROM dbo.FactOrders AS o\n"
        "JOIN dbo.DimCustomer AS c ON c.CustomerKey = o.CustomerKey GROUP BY c.Segment;\n")
# The teammate's change the learner must catch: Segment renamed, which breaks a view the warehouse has.
BREAKING = ("-- file: dbo/Tables/DimCustomer.sql\nCREATE TABLE [dbo].[DimCustomer] ([CustomerKey] INT NOT NULL, "
            "[Name] VARCHAR(100) NULL, [CustomerSegment] VARCHAR(20) NULL);\n")
STARTER = "-- Add dbo.FactOrders and dbo.vw_SalesBySegment. A teammate's change is below: check it.\n" + BREAKING
SOLUTION = TABLE + VIEW
WRONG = {"wrong-kept-rename.sql": SOLUTION + BREAKING,
         "wrong-view-in-tables.sql": TABLE + VIEW.replace("dbo/Views/vw_SalesBySegment.sql", "dbo/Tables/vw_SalesBySegment.sql"),
         "wrong-deleted-view.sql": SOLUTION + "-- delete: dbo/Views/vw_CustomersBySegment.sql\n",
         "wrong-sum-of-count.sql": TABLE + VIEW.replace("SUM(o.Amount)", "COUNT(o.Amount)"),
         "wrong-extra-statement.sql": TABLE.replace("NULL);", "NULL);\nDROP TABLE dbo.DimCustomer;") + VIEW}


@pytest.fixture
def folder(tmp_path):
    ex_dir, grading = tmp_path / "ex", tmp_path / "grading"
    ex_dir.mkdir()
    grading.mkdir()
    for name, text in FILES.items():
        (ex_dir / name).write_text(text, encoding="utf-8")
    (grading / "solution.sql").write_text(SOLUTION, encoding="utf-8")
    for name, text in WRONG.items():
        (grading / name).write_text(text, encoding="utf-8")
    return ex_dir, grading


def failures(result):
    return [r["detail"] for r in result["results"] if not r["passed"]]


# ---- the adapter

def test_reference_passes_and_wrong_answers_fail(folder):
    result = sqlprojlab.self_test(*folder, EX)
    assert result["solution"]["passed"], result["solution"]
    assert set(result["wrong"]) == set(WRONG) and not any(r["passed"] for r in result["wrong"].values())
    said = {k: failures(v) for k, v in result["wrong"].items()}
    assert any('vw_CustomersBySegment.sql doesn\'t build: Binder Error: Referenced column "Segment"' in d
               for d in said["wrong-kept-rename.sql"])
    assert any("would rebuild table dbo.DimCustomer" in d for d in said["wrong-kept-rename.sql"])
    assert any("Fabric's Git layout keeps at dbo/Views/vw_SalesBySegment.sql" in d for d in said["wrong-view-in-tables.sql"])
    assert any("would drop view dbo.vw_CustomersBySegment" in d for d in said["wrong-deleted-view.sql"])
    assert said["wrong-sum-of-count.sql"] == ["The query returns 2 rows that differ from the expected 2."]
    assert any("does more than define table dbo.FactOrders" in d for d in said["wrong-extra-statement.sql"])


def test_the_starter_fails(folder):
    assert not sqlprojlab.grade(STARTER, *folder, EX)["passed"]


def test_run_lists_each_file_its_build_and_its_deploy(folder):
    got = sqlprojlab.run(folder[0], EX, STARTER)
    assert got["columns"] == ["file", "object", "build", "deploy"]
    rows = {r[0]: r for r in got["rows"]}
    assert rows["dbo/Tables/DimCustomer.sql"][2] == "built" and rows["dbo/Tables/DimCustomer.sql"][3].startswith("not offered")
    assert rows["dbo/Views/vw_CustomersBySegment.sql"][2].startswith("failed: Binder Error")
    assert rows["dbo/StoredProcedures/usp_Refresh.sql"][2] == "read, not run on this PC"
    got = sqlprojlab.run(folder[0], EX, SOLUTION + "-- delete: dbo/Views/vw_CustomersBySegment.sql\n")
    assert ["(deleted)", "view dbo.vw_CustomersBySegment", "", got["rows"][-1][3]] == got["rows"][-1]
    assert got["rows"][-1][3].startswith("drop:")


def test_views_build_in_the_order_they_depend_on_each_other(folder):
    later = ("-- file: dbo/Views/a_Top.sql\nCREATE VIEW dbo.a_Top AS SELECT Segment FROM dbo.z_Base;\n"
             "-- file: dbo/Views/z_Base.sql\nCREATE VIEW dbo.z_Base AS SELECT Segment FROM dbo.DimCustomer;\n")
    project, seed = sqlprojlab.load_project(folder[0], EX)
    con, report, problems = sqlprojlab.build(sqlprojlab.apply(project, sqlprojlab.parse(later)))
    con.close()
    assert problems == [] and all(r["build"] for r in report)


def test_the_build_has_no_file_or_network_access(folder):
    sneaky = TABLE + ("-- file: dbo/Views/vw_Files.sql\n"
                      "CREATE VIEW dbo.vw_Files AS SELECT * FROM read_csv('/etc/passwd');\n")
    got = sqlprojlab.grade(sneaky, *folder, EX)
    assert any("vw_Files.sql doesn't build" in d for d in failures(got)), failures(got)


@pytest.mark.parametrize(("text", "said"), [
    ("CREATE TABLE dbo.X (a INT);", "start each file with a line like -- file:"),
    ("-- file: ../outside.sql\nSELECT 1;", "isn't a .sql file path in the project"),
    ("-- file: /abs/x.sql\nSELECT 1;", "isn't a .sql file path in the project"),
    ("-- file: dbo/Tables/X.txt\nSELECT 1;", "isn't a .sql file path in the project"),
    ("-- file: dbo/Tables/CON.sql\nSELECT 1;", "isn't a .sql file path in the project"),
    ("-- file: a.sql\nx\n-- file: a.sql\ny", "is in the change set twice"),
    (" " * (sqlprojlab.MAX_BYTES + 1), "over 1 MB"),
])
def test_a_change_set_that_cant_be_read_fails_every_check_in_words(folder, text, said):
    with pytest.raises(sqlprojlab.Refused, match=None) as err:
        sqlprojlab.parse(text)
    assert said in str(err.value)
    res = sqlprojlab.grade(text, *folder, EX)
    assert not res["passed"] and all(said in r["detail"] for r in res["results"])


def test_parse_keeps_each_file_and_deletes():
    got = sqlprojlab.parse("-- notes\n\n-- file: dbo/Tables/A.sql\nCREATE TABLE dbo.A (x INT);\n--  DELETE : dbo/Views/B.sql\n")
    assert got == {"dbo/Tables/A.sql": "CREATE TABLE dbo.A (x INT);\n", "dbo/Views/B.sql": None}


def test_names_are_case_sensitive_as_in_a_fabric_warehouse(folder):
    lower = SOLUTION.replace("vw_SalesBySegment AS", "vw_salesbysegment AS").replace(
        "dbo/Views/vw_SalesBySegment.sql", "dbo/Views/vw_salesbysegment.sql")
    got = sqlprojlab.grade(lower, *folder, EX)
    assert "The project has no view dbo.vw_SalesBySegment." in failures(got)


@pytest.mark.parametrize(("check", "said"), [
    ({"kind": "objects_present"}, "needs objects"),
    ({"kind": "objects_present", "objects": [{"kind": "index", "schema": "dbo", "name": "x"}]}, "needs kind"),
    ({"kind": "objects_present", "objects": [{"kind": "table", "schema": "dbo", "name": "x", "columns": "a"}]},
     "columns is a list"),
    ({"kind": "result_matches"}, "needs a query"),
    ({"kind": "deploy_safe", "allow_drop": "dbo.x"}, "allow_drop is a list"),
])
def test_check_problems(check, said):
    assert any(said in p for p in sqlprojlab.check_problems(check)), sqlprojlab.check_problems(check)


def test_well_formed_checks_have_no_problems():
    assert [p for c in VALIDATION for p in sqlprojlab.check_problems(c)] == []
    assert sqlprojlab.spec_problems(EID, EX) == []


# ---- in a pack: problems() and self_test()

@pytest.fixture
def proj_pack(tmp_path):
    root = tmp_path / "packs"
    shutil.copytree(registry.ROOT, root)
    folder, grading = root / PACK / "exercises" / EID, root / PACK / "grading" / EID
    shutil.rmtree(grading)
    grading.mkdir()
    for p in folder.iterdir():
        if p.suffix == ".sql":
            p.unlink()
    path = folder / "exercise.json"
    ex = json.loads(path.read_text(encoding="utf-8"))
    ex.update({"adapter": "sqlproj_lab", "class": "local_verified", "engine_label": "DuckDB on this PC",
               "starter": "changes.sql", "files": sorted(FILES), "project": PROJECT, "seed": "seed.sql",
               "validation": VALIDATION, "parity": []})
    ex.pop("setup", None)
    path.write_text(json.dumps(ex), encoding="utf-8")
    (folder / "changes.sql").write_text(STARTER, encoding="utf-8")
    for name, text in FILES.items():
        (folder / name).write_text(text, encoding="utf-8")
    (grading / "solution.sql").write_text(SOLUTION, encoding="utf-8")
    for name, text in WRONG.items():
        (grading / name).write_text(text, encoding="utf-8")
    return root


def test_a_project_exercise_is_well_formed_and_self_tests(proj_pack):
    assert exercises.problems(proj_pack) == []
    result = exercises.self_test(PACK, EID, proj_pack)
    assert result["solution"]["passed"], result["solution"]
    assert set(result["wrong"]) == set(WRONG) and not any(r["passed"] for r in result["wrong"].values())


@pytest.mark.parametrize(("change", "remove", "message"), [
    ({}, "grading/E36/solution.sql", "reference change set at grading/E36/solution.sql"),
    ({"starter": "settings.json"}, None, "starts from a change set"),
    ({"project": {}}, None, "needs project"),
    ({"project": {"dbo/Tables/A.sql": "other.sql"}}, None, "takes 'other.sql', which isn't listed in files"),
    ({"project": {"../A.sql": "usp.sql"}}, None, "isn't a .sql file path"),
    ({"seed": "nope.sql"}, None, "seed 'nope.sql' isn't listed in files"),
    ({"validation": [{"kind": "repo_matches"}]}, None, "sqlproj_lab can't check repo_matches"),
    ({"uses": "LAB-04/rebuild"}, None, "only for duckdb_lab exercises"),
])
def test_a_project_exercise_that_cant_be_graded_is_caught(proj_pack, change, remove, message):
    path = proj_pack / PACK / "exercises" / EID / "exercise.json"
    ex = json.loads(path.read_text(encoding="utf-8"))
    ex.update(change)
    path.write_text(json.dumps(ex), encoding="utf-8")
    if remove:
        (proj_pack / PACK / remove).unlink()
    if change.get("starter") == "settings.json":
        (proj_pack / PACK / "exercises" / EID / "settings.json").write_text("{}")
    found = exercises.problems(proj_pack)
    assert any(message in p for p in found), found


def test_fewer_than_two_wrong_answers_is_caught(proj_pack):
    for name in list(WRONG)[1:]:
        (proj_pack / PACK / "grading" / EID / name).unlink()
    assert any("at least two planted wrong answers (grading/E36/wrong-*.sql)" in p for p in exercises.problems(proj_pack))


# ---- through the workspace and the academy_* methods

class _Svc(AcademyMixin):
    def __init__(self, root):
        self.root = root
        self.rows = []

    def _progress_record(self, row):
        self.rows.append(row)
        return []

    def _profile_path(self):
        return self.root / "profile.json"


@pytest.fixture
def svc(proj_pack, tmp_path, monkeypatch):
    monkeypatch.setattr(exercises, "get", functools.partial(exercises.get, root=proj_pack))
    monkeypatch.setattr(exercises, "file_text", functools.partial(exercises.file_text, root=proj_pack))
    monkeypatch.setattr(lab, "_exercise_folder", lambda pid, eid: proj_pack / pid / "exercises" / eid)
    monkeypatch.setattr(lab, "_grading_folder", lambda pid, eid: proj_pack / pid / "grading" / eid)
    data = tmp_path / "data"
    data.mkdir()
    return _Svc(data)


def test_start_save_run_check_and_reset(svc):
    st = svc.academy_start(PACK, EID)
    assert st["started"] and st["answer"] == STARTER
    folder = lab.workspace(svc.root, PACK, EID)
    assert sorted(p.name for p in folder.iterdir()) == ["changes.sql", "state.json"]
    shown = svc.academy_exercise(PACK, EID)
    assert shown["checks"] == ["project_builds", "objects_present", "deploy_safe", "result_matches"]
    assert "Segment" not in json.dumps({k: v for k, v in shown.items() if k != "workspace"})   # no query or objects

    got = svc.academy_run(PACK, EID, STARTER)
    assert got["columns"] == ["file", "object", "build", "deploy"] and "ms" in got
    assert sorted(p.name for p in folder.iterdir()) == ["changes.sql", "state.json"]   # nothing written

    res = svc.academy_validate(PACK, EID)
    assert not res["passed"] and not svc.rows
    assert res["verification"]["label"] == "LOCAL VERIFIED"
    assert res["results"][0]["message"] == VALIDATION[0]["fail"]

    svc.academy_save(PACK, EID, SOLUTION)
    res = svc.academy_validate(PACK, EID)
    assert res["passed"], res
    assert svc.rows and svc.rows[0]["detail"]["class"] == "local_verified"

    svc.academy_reset(PACK, EID)
    assert lab.state(svc.root, PACK, EID)["answer"] == STARTER
