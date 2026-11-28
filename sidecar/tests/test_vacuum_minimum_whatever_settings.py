"""VACUUM never keeps less than 7 days of history, whatever a setting says (the owner's rule).

The settings that could ask for less: a table's own ``delta.deletedFileRetentionDuration`` (any engine can set
it, down to 0), a notebook's ``RETAIN n HOURS`` or ``DeltaTable.vacuum(n)``, Spark's
``spark.databricks.delta.retentionDurationCheck.enabled = false`` (which in Spark lets VACUUM go below the
minimum), a layer's Keep history days in the metadata project, and the days a caller passes to the engine's
VACUUM functions. For each way VACUUM runs on this PC or is sent to Fabric, a removal 2 days old is kept
whatever is set, and the 7-day minimum is what's kept. ``allow_shorter``, the one switch past the minimum, has
no caller in the engine. No network, no Fabric."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from deltalake import DeltaTable

from fabric_desktop import lakehouse, maintenance
from tests.test_spark_maint import run
from tests.test_spark_writer import lake  # noqa: F401 - a fixture
from tests.test_vacuum_hardening import _history

PACKAGE = Path(__file__).resolve().parents[1] / "fabric_desktop"
# A table's own retention asking for less than 7 days: as Spark writes it, in hours, and nothing at all.
SHORT = ["interval 0 days", "interval 0 hours", "interval 1 hours", "interval 1 days", "interval 6 days",
         "interval 167 hours"]


@pytest.fixture
def sales(lake, monkeypatch):  # noqa: F811
    """dbo.sales, versions 0 to 3: version 1, 2 and 3 removed the file before them 20, 10 and 2 days ago."""
    monkeypatch.setattr(lakehouse, "maintenance_guard", lambda op, ws, t: None)
    uri = str(lake / "dbo" / "sales")
    named = _history(uri)
    return uri, named


def short(uri: str, setting: str) -> None:
    DeltaTable(uri).alter.set_table_properties({"delta.deletedFileRetentionDuration": setting},
                                               raise_if_not_exists=False)


def kept(uri: str, named: dict[int, str]) -> dict[int, bool]:
    return {v: (Path(uri) / f).exists() for v, f in named.items()}


SEVEN_DAYS = {0: False, 1: False, 2: True, 3: True}  # removed 20 and 10 days ago go; 2 days ago stays


@pytest.mark.parametrize("setting", SHORT)
@pytest.mark.parametrize("how", ["sql", "sql retain 168", "deltatable", "deltatable 168", "maintain_table"])
def test_a_notebooks_vacuum_keeps_seven_days_whatever_the_tables_own_setting(sales, setting, how):
    uri, named = sales
    short(uri, setting)
    code = {"sql": "spark.sql('VACUUM dbo.sales')",
            "sql retain 168": "spark.sql('VACUUM dbo.sales RETAIN 168 HOURS')",
            "deltatable": "from delta.tables import DeltaTable\nDeltaTable.forName(spark, 'dbo.sales').vacuum()",
            "deltatable 168": "from delta.tables import DeltaTable\nDeltaTable.forName(spark, 'dbo.sales').vacuum(168)"}
    if how == "maintain_table":
        lakehouse.maintain_table("dbo.sales", "vacuum")
    else:
        status, text = run(code[how])
        assert status == "ok", text
    assert kept(uri, named) == SEVEN_DAYS


@pytest.mark.parametrize("code", [
    "spark.sql('VACUUM dbo.sales RETAIN 0 HOURS')",
    "spark.sql('VACUUM dbo.sales RETAIN 47 HOURS')",
    "from delta.tables import DeltaTable\nDeltaTable.forName(spark, 'dbo.sales').vacuum(0)",
    "from delta.tables import DeltaTable\nDeltaTable.forName(spark, 'dbo.sales').vacuum(167.9)",
])
@pytest.mark.parametrize("check", [None, "false", "False"])
def test_less_than_seven_days_is_refused_even_with_spark_s_retention_check_turned_off(sales, code, check):
    """In Spark, retentionDurationCheck.enabled = false lets VACUUM go below the minimum. Here it changes nothing."""
    uri, named = sales
    before = DeltaTable(uri).version()
    off = f"spark.conf.set('spark.databricks.delta.retentionDurationCheck.enabled', '{check}')\n" if check else ""
    status, text = run(off + code)
    assert status == "error" and "at least 7 days" in text, text
    assert kept(uri, named) == {v: True for v in named} and DeltaTable(uri).version() == before


@pytest.mark.parametrize("setting", SHORT)
def test_compact_and_its_preview_keep_seven_days_whatever_the_tables_own_setting(sales, setting):
    uri, named = sales
    short(uri, setting)
    doomed = set(maintenance.vacuum_preview(uri, maintenance.MIN_RETENTION_DAYS))
    assert doomed == {named[0], named[1]}
    maintenance.compact_local(uri, maintenance.MIN_RETENTION_DAYS)
    assert {v: ok for v, ok in kept(uri, named).items()} == SEVEN_DAYS
    # and the table's setting is raised to the 7 days kept, so every engine's VACUUM keeps them too
    assert maintenance.kept_days(DeltaTable(uri).metadata().configuration) == maintenance.MIN_RETENTION_DAYS


@pytest.mark.parametrize("days", [-1, 0, 1, 6, 6.99, float("nan"), float("inf"), True, "7"])
def test_every_engine_vacuum_refuses_fewer_days_before_anything_changes(sales, days):
    uri, named = sales
    before = DeltaTable(uri).version()
    for call in (lambda: maintenance.compact_local(uri, days), lambda: maintenance.vacuum_preview(uri, days),
                 lambda: maintenance.set_retention(uri, days), lambda: maintenance.fabric_retention(days),
                 lambda: maintenance.check_retention(days)):
        with pytest.raises(ValueError, match="at least 7 days"):
            call()
    assert kept(uri, named) == {v: True for v in named} and DeltaTable(uri).version() == before


# A layer's Keep history below 7 days is refused when the project is saved
# (test_meta_project_mutation.test_a_layer_keeps_versions_for_seven_days_to_ten_years), and were one hand-edited
# in, Maintain refuses it before it sets retention or starts a job
# (test_cloud_service_more_edges.test_onelake_maintenance_refuses_less_than_seven_days_...), and so does the
# Fabric job after a run (fabric_retention, above).


def test_xray_keeps_at_least_fabric_s_seven_days_whatever_the_table_says():
    from fabric_desktop.xray_service import NoMetadataProject, XRayMixin

    class Svc(XRayMixin):
        def _project(self):
            raise NoMetadataProject("none")
    for table_days in (None, 0, 1, 6):
        days, _why = Svc()._xray_keep_days("ws", "LH", table_days)
        assert days == maintenance.MIN_RETENTION_DAYS, table_days


def test_nothing_in_the_engine_asks_for_less_than_the_minimum():
    """``allow_shorter`` is the only way past check_retention. No engine code passes it (a policy that
    wanted it would have to add a caller here, and this test with it)."""
    found = []
    for path in PACKAGE.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call):
                for k in node.keywords:
                    if k.arg == "allow_shorter" and not (isinstance(k.value, ast.Name) and k.value.id == "allow_shorter") \
                            and not (isinstance(k.value, ast.Constant) and k.value.value is False):
                        found.append((path.relative_to(PACKAGE).as_posix(), node.lineno))
    assert found == []
