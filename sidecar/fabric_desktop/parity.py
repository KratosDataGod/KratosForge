"""Platform experience parity at five levels, kept apart (Chris's brief, 2026-11-03; docs/parity.md).

For each platform area, each level is judged on its own evidence:

    visual       the app looks familiar to that platform's developers (a skin targets the platform)
    workflow     its navigation and steps follow that platform's (a skin arranges the pages)
    cloud_api    KratosForge talks to the platform's own service for this area
    local        the area's work runs on this PC (natively, with documented differences, or as
                 validation only: the cell's note says which)
    equivalence  results on this PC were compared with the platform's own and recorded

A level never borrows another's evidence: a familiar screen is not workflow parity, and a local run
that works is not equivalence. Statuses, weakest first:

    not_assessed  nobody has checked yet (the default; it never counts as missing or as done)
    missing       checked: KratosForge doesn't do it
    partial       does some of it; the note says what's missing or different
    built         does it, with tests in this repository (cloud tests run against fakes)
    verified      built, and a recorded run against the real platform backs it (docs/platforms.md, Live runs)

Rules ``Cell`` enforces, so the matrix can't claim more than the repository shows:

- partial, built and verified name the test files under sidecar/tests/ that back them (a test
  checks the files exist); verified also says which recorded live run.
- partial needs a note saying what's missing or different.
- visual and workflow can't rise above missing until a built-in skin targets the platform (skins.py).
- equivalence is never built: only a recorded comparison counts, so it is partial or verified.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from . import skins

LEVELS = {
    "visual": "Visual familiarity",
    "workflow": "Workflow compatibility",
    "cloud_api": "Cloud API integration",
    "local": "Local execution",
    "equivalence": "Verified runtime equivalence",
}
STATUSES = ("not_assessed", "missing", "partial", "built", "verified")
PLATFORMS = {"fabric": "Microsoft Fabric", "databricks": "Azure Databricks", "snowflake": "Snowflake"}
KUDP_RUN = "KUDP comparison, 2026-10-29, re-run 2026-11-03 (docs/platforms.md, Live runs)"


@dataclass(frozen=True)
class Cell:
    status: str = "not_assessed"
    tests: tuple[str, ...] = ()
    live: str = ""
    note: str = ""

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"unknown status {self.status!r}")
        if self.status in ("partial", "built", "verified") and not self.tests:
            raise ValueError(f"{self.status} needs the tests that back it")
        if self.status == "verified" and not self.live:
            raise ValueError("verified needs the recorded live run")
        if self.status == "partial" and not self.note:
            raise ValueError("partial needs a note saying what's missing or different")


@dataclass(frozen=True)
class Area:
    platform: str
    id: str
    label: str
    cells: dict[str, Cell]

    def __post_init__(self) -> None:
        if self.platform not in PLATFORMS:
            raise ValueError(f"{self.label}: unknown platform {self.platform!r}")
        unknown = set(self.cells) - set(LEVELS)
        if unknown:
            raise ValueError(f"{self.label}: unknown levels {sorted(unknown)}")
        skinned = any(t.get("platform") == self.platform for s in skins.built_in() for t in s.get("targets", []))
        for level in ("visual", "workflow"):
            if self.cell(level).status not in ("not_assessed", "missing") and not skinned:
                raise ValueError(f"{self.label}: {level} needs a built-in skin that targets {self.platform}")
        if self.cell("equivalence").status == "built":
            raise ValueError(f"{self.label}: equivalence is partial or verified, from a recorded comparison")

    def cell(self, level: str) -> Cell:
        return self.cells.get(level, Cell())


MISSING = Cell("missing")
# No skin targets a platform yet (the skin framework is being built): visual and workflow are missing.
_NO_SKIN = {"visual": MISSING, "workflow": MISSING}


def _a(platform: str, id_: str, label: str, **cells: Cell) -> Area:
    return Area(platform, id_, label, {**_NO_SKIN, **cells})


AREAS: tuple[Area, ...] = (
    # Microsoft Fabric
    _a("fabric", "workspaces", "Workspaces and items",
       cloud_api=Cell("verified", ("test_fabric_api.py",), KUDP_RUN, "Listing workspaces and lakehouses was recorded; other item types were not.")),
    _a("fabric", "lakehouse", "Lakehouse tables and files",
       cloud_api=Cell("built", ("test_fabric_api.py",), note="Tables weren't listed in the recorded live run."),
       local=Cell("built", ("test_onelake_writes.py", "test_local_delta.py"), note="Notebooks on this PC read and write OneLake; used live, not recorded.")),
    _a("fabric", "warehouse", "Warehouse and SQL analytics endpoint",
       cloud_api=Cell("built", ("test_warehouse_queries.py", "test_sql_exec.py")),
       local=Cell("partial", ("test_local_tsql.py", "test_tsql_contract.py", "test_tsql_loud_gaps.py"),
                  note="T-SQL is translated to DuckDB; documented gaps fail loudly (docs/sql-compatibility.md).")),
    _a("fabric", "notebooks", "Notebooks and Spark",
       cloud_api=Cell("verified", ("test_fabric_spark.py",), KUDP_RUN, "Spark SQL in one Livy session."),
       local=Cell("partial", ("test_engine_conformance.py", "test_spark_writer.py"),
                  note="PySpark and Spark SQL are simulated on DuckDB; known differences are listed in docs/audits/ENGINE_COMPATIBILITY.md."),
       equivalence=Cell("partial", ("test_engine_compare.py",), KUDP_RUN, "10 Spark SQL queries: 9 match in the 2026-11-03 re-run; decimal division still differs (Spark keeps 6 places).")),
    _a("fabric", "pipelines", "Data Factory pipelines",
       local=Cell("partial", ("test_pipeline_fabric_parity.py", "test_pipeline_runs.py"),
                  note="Pipelines run on this PC; expected outcomes come from documentation, not a recorded tenant run.")),
    _a("fabric", "dataflows", "Dataflows Gen2"),
    _a("fabric", "taskflows", "Medallion task flows"),
    _a("fabric", "semantic", "Semantic models, TMDL and Direct Lake",
       local=Cell("partial", ("test_tmdl.py", "test_model_directlake.py"),
                  note="TMDL is read offline and the Direct Lake check runs on this PC; models aren't served here.")),
    _a("fabric", "git", "Git integration and deployment",
       cloud_api=Cell("built", ("test_platform_repo.py", "test_notebook_publish.py", "test_fabric_git.py"), note="Publishing hasn't been recorded live.")),
    _a("fabric", "monitoring", "Monitoring and capacity",
       cloud_api=Cell("built", ("test_capacity.py",))),
    _a("fabric", "governance", "Security and governance"),
    # Azure Databricks
    _a("databricks", "connect", "Sign-in and workspace discovery",
       cloud_api=Cell("built", ("test_connprofiles.py", "test_databricks_discovery.py", "test_databricks_discovery_replies.py",
                                 "test_databricks_auth_errors.py", "test_databricks_auth_replies.py", "test_databricks_host.py",
                                 "test_databricks_profile_host.py", "test_databricks_credman.py"),
                      note="Against fakes of the CLI and APIs; no live run yet.")),
    _a("databricks", "sql", "SQL warehouses and the SQL editor",
       cloud_api=Cell("built", ("test_databricks_sql.py", "test_databricks_sql_errors.py", "test_databricks_sql_cancel.py"),
                      note="Against fakes of the SQL Statement API; no live run yet."),
       local=Cell("partial", ("test_databricks_sql_parity.py",),
                  note="Databricks SQL is translated to DuckDB; functions that differ are listed in docs/engines.md.")),
    _a("databricks", "notebooks", "Notebooks, Spark and dbutils",
       cloud_api=Cell("built", ("test_dbconnect.py", "test_databricks_workspace_sync.py", "test_databricks_sync_service.py"),
                      note="Databricks Connect (runtime 16 and later), behind an authorization, and a notebook folder synced "
                           "with a workspace folder (Preview); against fakes, no live run yet."),
       local=Cell("partial", ("test_dbutils.py", "test_dbutils_parity.py", "test_spark_api.py", "test_spark_api_parity.py"),
                  note="dbutils and PySpark are simulated on this PC; Databricks Runtime equivalence isn't claimed.")),
    _a("databricks", "unity", "Unity Catalog"),
    _a("databricks", "jobs", "Jobs and Lakeflow pipelines",
       cloud_api=Cell("partial", ("test_databricks_jobs.py", "test_databricks_runs_service.py"),
                      note="Monitor > Databricks (Preview) reads job runs and Lakeflow updates with their tasks and errors; "
                           "jobs aren't created or started here. Against fakes, no live run yet.")),
    _a("databricks", "compute", "Compute"),
    _a("databricks", "bundles", "Asset bundles and deployment",
       cloud_api=Cell("built", ("test_databricks_bundles.py", "test_databricks_bundles_service.py"),
                      note="Source control > Bundles (Preview) validates, plans and deploys through the Databricks CLI; "
                           "against a fake CLI, no live run yet.")),
    _a("databricks", "cost", "Cost and DBU",
       cloud_api=Cell("partial", ("test_databricks_billing.py",),
                      note="Optimize > Cost (Preview) reads DBUs from the billing system tables after a confirm; "
                           "no budgets or alerts. Against fakes, no live run yet.")),
    # Snowflake
    _a("snowflake", "connect", "Sign-in, roles and warehouses", cloud_api=MISSING),
    _a("snowflake", "worksheets", "SQL worksheets",
       cloud_api=MISSING,
       local=Cell("partial", ("test_snowflake_sql.py", "test_snowflake_sql_parity.py"),
                  note="Validation and translation only: Snowflake SQL runs on DuckDB with a label per statement. Not a Snowflake runtime.")),
    _a("snowflake", "objects", "Databases, schemas and object browser"),
    _a("snowflake", "snowpark", "Snowpark", local=MISSING),
    _a("snowflake", "loading", "Stages and data loading"),
    _a("snowflake", "tasks", "Streams, tasks and dynamic tables"),
    _a("snowflake", "history", "Query history, profiling and credits"),
)


def matrix() -> dict[str, Any]:
    """Every area's five levels, by platform, as the app and docs show them."""
    out: dict[str, Any] = {"levels": LEVELS, "statuses": STATUSES, "platforms": {}}
    for pid, label in PLATFORMS.items():
        out["platforms"][pid] = {"label": label, "areas": [
            {"id": a.id, "label": a.label, "levels": {lv: vars(a.cell(lv)) | {"tests": list(a.cell(lv).tests)} for lv in LEVELS}}
            for a in AREAS if a.platform == pid]}
    return out


_SHOW = {"not_assessed": "·", "missing": "Missing", "partial": "Partial", "built": "Built", "verified": "Verified"}


def docs_table() -> str:
    """The tables in docs/parity.md (a test keeps the page equal to this)."""
    lines: list[str] = []
    for pid, label in PLATFORMS.items():
        lines += [f"### {label}", "", "| Area | " + " | ".join(LEVELS.values()) + " |",
                  "| --- |" + " --- |" * len(LEVELS)]
        for a in (x for x in AREAS if x.platform == pid):
            lines.append(f"| {a.label} | " + " | ".join(_SHOW[a.cell(lv).status] for lv in LEVELS) + " |")
        notes = [(a.label, LEVELS[lv], a.cell(lv)) for a in AREAS if a.platform == pid for lv in LEVELS
                 if a.cell(lv).note or a.cell(lv).live]
        if notes:
            lines += ["", "Notes:", ""]
            lines += [f"- **{area}, {lv}:** " + " ".join(x for x in (c.note, f"Live: {c.live}." if c.live else "") if x)
                      for area, lv, c in notes]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
