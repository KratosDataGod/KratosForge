"""fabric_run.py paths that mutation testing (batch 17, docs/quality/mutation-testing.md) showed no test
checked: the compute check on cells Python can't read, Delta maintenance with Preview features off,
the order of the notebooks a cell runs, and the published notebook's schemas default."""

from __future__ import annotations

import pytest

from fabric_desktop import fabric_run, lakehouse
from fabric_desktop.fabric_run import compute_check

LH = {"name": "Sales", "workspace_id": "ws1", "lakehouse_id": "lh1", "workspace_name": "Sales"}
MERGE_KEYS = "MERGE INTO LH_Keys.keys.customer t USING s ON t.id = s.id WHEN MATCHED THEN DELETE"
IN_PLACE = ("changes table rows in place with spark.sql (MERGE, UPDATE or DELETE of another lakehouse, or with "
            "Preview features off), which runs in Fabric only")
NOT_PYTHON = "this is not ( python"


@pytest.fixture(autouse=True)
def _own_app_data(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))


@pytest.fixture
def preview_off(monkeypatch):
    real = lakehouse._setting
    monkeypatch.setattr(lakehouse, "_setting", lambda key, default: False if key == "show_preview" else real(key, default))


def check(*sources, load=None):
    return compute_check([{"type": "code", "source": s, "meta": {}} for s in sources], LH, load)


# ---- the published notebook

def test_a_published_lakehouse_notebook_has_schemas_on_unless_told_otherwise():
    cells = [{"type": "code", "source": "from fabric_desktop import lakehouse\nlakehouse.table('t')"}]
    default = fabric_run.fabric_ipynb(cells, LH)
    shim = "".join(default["cells"][0]["source"])
    assert "_fabric_desktop_module('ws1', 'lh1', 'Sales', True)" in shim
    off = "".join(fabric_run.fabric_ipynb(cells, LH, schemas_enabled=False)["cells"][0]["source"])
    assert "_fabric_desktop_module('ws1', 'lh1', 'Sales', False)" in off


def test_sql_that_changes_nothing_is_not_in_place():
    assert fabric_run._sql_in_place("SELECT 1") is False


# ---- a cell Python can't read: the literal spark.sql calls in its text

def test_an_unreadable_cell_with_a_literal_in_place_change_is_fabric_only():
    local = check(f"spark.sql('{MERGE_KEYS}')\n{NOT_PYTHON}")["local"]
    assert local["status"] == "blocked"
    assert local["reasons"][0]["text"] == IN_PLACE


def test_a_commented_out_change_after_a_leading_blank_line_is_skipped():
    out = check(f"\n# spark.sql('{MERGE_KEYS}')\n{NOT_PYTHON}")["local"]
    assert out["reasons"] == [] and out["status"] == "unknown"


def test_an_unreadable_cell_with_a_literal_refused_sql_gives_the_refusal_text():
    local = check(f"spark.sql('DROP TABLE dbo.old_sales')\n{NOT_PYTHON}")["local"]
    assert local["status"] == "blocked"
    assert local["reasons"][0]["text"].startswith("drops the lakehouse table dbo.old_sales (DROP TABLE)")


def test_a_cell_python_cant_read_names_the_line_it_stops_on():
    local = check("x = 1\ny = 2\nz = (\n")["local"]
    assert local["status"] == "unknown"
    first = local["unknown"][0]
    assert first["text"].startswith("isn't Python the check can read (line 3:")
    assert first["code"] == "z = ("


def test_a_sql_line_magic_this_pc_may_refuse_gives_its_reason():
    local = check("x = 1\n%sql DROP TABLE old_sales")["local"]
    assert local["status"] == "unknown"
    assert local["unknown"][0]["text"] == ("drops old_sales (DROP TABLE), which runs here for a temp view and in "
                                           "Fabric for a lakehouse table")


# ---- reading the Python

def test_a_fabric_only_utility_imported_from_notebookutils_mssparkutils_is_fabric_only():
    local = check("from notebookutils.mssparkutils import warehouse")["local"]
    assert local["status"] == "blocked"
    assert local["reasons"][0]["text"] == "uses Fabric notebook utilities that need Fabric"


def test_sql_built_from_many_added_parts_is_read_only_so_deep():
    parts = ["MERGE ", "INTO ", "LH_Keys.", "keys.", "customer t ", "USING s ", "ON t.id = s.id ",
             "WHEN MATCHED ", "THEN DELETE"]
    local = check("spark.sql(" + " + ".join(repr(p) for p in parts) + ")")["local"]
    assert local["status"] == "unknown" and local["reasons"] == []
    assert local["unknown"][0]["text"].startswith("runs SQL built at run time")


def test_delta_maintenance_on_a_delta_table_kept_in_a_name_needs_preview(preview_off):
    local = check("from delta.tables import DeltaTable\nt = DeltaTable.forName(spark, 'sales')\nt.optimize()")["local"]
    assert local["status"] == "blocked"
    assert local["reasons"][0]["text"] == "runs DeltaTable.optimize, which this PC runs only with Preview features on"


@pytest.mark.parametrize("source", [
    "class Job:\n    def optimize(self):\n        return 1\nJob().optimize()",
    "from delta.tables import DeltaTable\nDeltaTable.forName(spark, 'sales').toDF().show()",
])
def test_only_maintenance_on_a_delta_table_needs_preview(preview_off, source):
    assert check(source)["local"]["status"] == "ok"


# ---- notebooks a cell runs

def test_of_two_blocked_notebooks_a_cell_runs_the_first_line_is_named():
    notebooks = {"Zeta": [{"type": "code", "source": "sc = spark.sparkContext"}],
                 "Alpha": [{"type": "code", "source": "df.writeStream.trigger(continuous='1 second').start()"}]}
    local = check("%run Zeta\n%run Alpha", load=lambda name: notebooks[name])["local"]
    assert local["status"] == "blocked"
    assert local["reasons"][0]["text"].startswith("runs Zeta (%run), whose cell 1")
