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


# The Fabric-, Databricks- and Snowflake-familiar skins (skins.py, Preview) change the whole app's accent and the
# rail's order and names, so every area looks partly familiar; the pages keep KratosForge's own layout.
_SKIN_LOOK = {
    "fabric": Cell("partial", ("test_skins.py",), note="The Fabric-familiar skin (Preview) gives a teal "
                   "accent and Fabric's page order on the rail (Home, Create, Workspaces, OneLake and Monitor first); pages keep "
                   "KratosForge's own layout."),
    "databricks": Cell("partial", ("test_skins.py",), note="The Databricks-familiar skin (Preview) gives a blue "
                       "accent and Databricks' page names on the rail; pages keep KratosForge's own layout."),
    "snowflake": Cell("partial", ("test_skins.py",), note="The Snowflake-familiar skin (Preview) gives a blue "
                      "accent and Snowsight's page names on the rail; pages keep KratosForge's own layout."),
}


def _skinned(platform: str, id_: str, label: str, workflow: Cell | None = None, **cells: Cell) -> Area:
    """An area of a platform a built-in skin targets: its look is partial, its workflow missing unless
    a rail page named or placed as on that platform holds this area's work."""
    return Area(platform, id_, label, {"visual": _SKIN_LOOK[platform], "workflow": workflow or MISSING, **cells})


AREAS: tuple[Area, ...] = (
    # Microsoft Fabric
    _skinned("fabric", "workspaces", "Workspaces and items",
       workflow=Cell("partial", ("test_skins.py",),
                     note="The skin's rail has Workspaces third, after Home and Create, as in Fabric; inside it, the page is KratosForge's own."),
       cloud_api=Cell("verified", ("test_fabric_api.py",), KUDP_RUN, "Listing workspaces and lakehouses was recorded; other item types were not.")),
    _skinned("fabric", "lakehouse", "Lakehouse tables and files",
       workflow=Cell("partial", ("test_skins.py",),
                     note="The skin's rail has OneLake fourth, after Workspaces, as in Fabric; inside it, the page is KratosForge's own."),
       cloud_api=Cell("built", ("test_fabric_api.py",), note="Tables weren't listed in the recorded live run."),
       local=Cell("built", ("test_onelake_writes.py", "test_local_delta.py"), note="Notebooks on this PC read and write OneLake; used live, not recorded.")),
    _skinned("fabric", "warehouse", "Warehouse and SQL analytics endpoint",
       cloud_api=Cell("built", ("test_warehouse_queries.py", "test_sql_exec.py")),
       local=Cell("partial", ("test_local_tsql.py", "test_tsql_contract.py", "test_tsql_loud_gaps.py"),
                  note="T-SQL is translated to DuckDB; documented gaps fail loudly (docs/sql-compatibility.md).")),
    _skinned("fabric", "notebooks", "Notebooks and Spark",
       cloud_api=Cell("verified", ("test_fabric_spark.py",), KUDP_RUN, "Spark SQL in one Livy session."),
       local=Cell("partial", ("test_engine_conformance.py", "test_spark_writer.py"),
                  note="PySpark and Spark SQL are simulated on DuckDB; known differences are listed in docs/audits/ENGINE_COMPATIBILITY.md."),
       equivalence=Cell("partial", ("test_engine_compare.py",), KUDP_RUN, "10 Spark SQL queries: 9 match in the 2026-11-03 re-run; decimal division still differs (Spark keeps 6 places).")),
    _skinned("fabric", "pipelines", "Data Factory pipelines",
       local=Cell("partial", ("test_pipeline_fabric_parity.py", "test_pipeline_runs.py"),
                  note="Pipelines run on this PC; expected outcomes come from documentation, not a recorded tenant run.")),
    _skinned("fabric", "dataflows", "Dataflows Gen2"),
    _skinned("fabric", "taskflows", "Medallion task flows"),
    _skinned("fabric", "semantic", "Semantic models, TMDL and Direct Lake",
       local=Cell("partial", ("test_tmdl.py", "test_model_directlake.py"),
                  note="TMDL is read offline and the Direct Lake check runs on this PC; models aren't served here.")),
    _skinned("fabric", "git", "Git integration and deployment",
       cloud_api=Cell("built", ("test_platform_repo.py", "test_notebook_publish.py", "test_fabric_git.py"), note="Publishing hasn't been recorded live.")),
    _skinned("fabric", "monitoring", "Monitoring and capacity",
       workflow=Cell("partial", ("test_skins.py",),
                     note="The skin's rail has Monitor fifth, after OneLake, as in Fabric; inside it, the page is KratosForge's own."),
       cloud_api=Cell("built", ("test_capacity.py",))),
    _skinned("fabric", "governance", "Security and governance"),
    # Azure Databricks
    _skinned("databricks", "connect", "Sign-in and workspace discovery",
       cloud_api=Cell("built", ("test_connprofiles.py", "test_databricks_discovery.py", "test_databricks_discovery_replies.py",
                                 "test_databricks_auth_errors.py", "test_databricks_auth_replies.py", "test_databricks_host.py",
                                 "test_databricks_profile_host.py", "test_databricks_credman.py"),
                      note="Against fakes of the CLI and APIs; no live run yet.")),
    _skinned("databricks", "sql", "SQL warehouses and the SQL editor",
       cloud_api=Cell("built", ("test_databricks_sql.py", "test_databricks_sql_errors.py", "test_databricks_sql_cancel.py"),
                      note="Against fakes of the SQL Statement API; no live run yet."),
       local=Cell("partial", ("test_databricks_sql_parity.py", "test_databricks_coverage.py"),
                  note="Databricks SQL is translated to DuckDB; docs/databricks-functions.md says for every built-in "
                       "whether it runs, differs, is refused or fails, checked in CI.")),
    _skinned("databricks", "notebooks", "Notebooks, Spark and dbutils",
       cloud_api=Cell("built", ("test_dbconnect.py", "test_databricks_workspace_sync.py", "test_databricks_sync_service.py"),
                      note="Databricks Connect (runtime 16 and later), behind an authorization, and a notebook folder synced "
                           "with a workspace folder (Preview); against fakes, no live run yet."),
       local=Cell("partial", ("test_dbutils.py", "test_dbutils_parity.py", "test_spark_api.py", "test_spark_api_parity.py",
                              "test_databricks_notebook_helpers.py"),
                  note="dbutils and PySpark are simulated on this PC; Databricks Runtime equivalence isn't claimed. "
                       "Each dbutils call and % magic: docs/databricks-notebook-helpers.md.")),
    _skinned("databricks", "unity", "Unity Catalog"),
    _skinned("databricks", "jobs", "Jobs and Lakeflow pipelines",
       workflow=Cell("partial", ("test_skins.py", "test_databricks_runs_service.py"),
                     note="The skin names Monitor \"Jobs and pipelines\" and puts it where Databricks does; "
                          "inside it, runs are read, not created or started."),
       cloud_api=Cell("partial", ("test_databricks_jobs.py", "test_databricks_runs_service.py"),
                      note="Monitor > Databricks (Preview) reads job runs and Lakeflow updates with their tasks and errors; "
                           "jobs aren't created or started here. Against fakes, no live run yet.")),
    _skinned("databricks", "compute", "Compute"),
    _skinned("databricks", "bundles", "Asset bundles and deployment",
       cloud_api=Cell("partial", ("test_databricks_bundles.py", "test_databricks_bundles_service.py"),
                      note="Source control > Bundles (Preview) validates, plans and deploys through the Databricks CLI; "
                           "bundle run and destroy aren't offered. Against a fake CLI, no live run yet.")),
    _skinned("databricks", "cost", "Cost and DBU",
       cloud_api=Cell("partial", ("test_databricks_billing.py",),
                      note="Optimize > Cost (Preview) reads DBUs from the billing system tables after a confirm; "
                           "no budgets or alerts. Against fakes, no live run yet.")),
    # Snowflake
    _skinned("snowflake", "connect", "Sign-in, roles and warehouses", cloud_api=MISSING),
    _skinned("snowflake", "worksheets", "SQL worksheets",
       cloud_api=MISSING,
       local=Cell("partial", ("test_snowflake_sql.py", "test_snowflake_sql_parity.py", "test_snowflake_coverage.py"),
                  note="Validation and translation only: Snowflake SQL runs on DuckDB with a label per statement. Not a Snowflake runtime. "
                       "docs/snowflake-functions.md says for every function whether it runs, differs, is refused or fails.")),
    _skinned("snowflake", "objects", "Databases, schemas and object browser"),
    _skinned("snowflake", "snowpark", "Snowpark", local=MISSING),
    _skinned("snowflake", "loading", "Stages and data loading"),
    _skinned("snowflake", "tasks", "Streams, tasks and dynamic tables"),
    _skinned("snowflake", "history", "Query history, profiling and credits"),
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
        areas = [a for a in AREAS if a.platform == pid]
        shared = {lv for lv in LEVELS if len(areas) > 1 and areas[0].cell(lv).note
                  and all(a.cell(lv) == areas[0].cell(lv) for a in areas)}
        notes = [("Every area", LEVELS[lv], areas[0].cell(lv)) for lv in LEVELS if lv in shared]
        notes += [(a.label, LEVELS[lv], a.cell(lv)) for a in areas for lv in LEVELS
                  if lv not in shared and (a.cell(lv).note or a.cell(lv).live)]
        if notes:
            lines += ["", "Notes:", ""]
            lines += [f"- **{area}, {lv}:** " + " ".join(x for x in (c.note, f"Live: {c.live}." if c.live else "") if x)
                      for area, lv, c in notes]
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"
