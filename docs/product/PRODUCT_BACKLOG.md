# Product backlog

One backlog for Fabric Desktop by KratosBI, in twelve categories. It consolidates the gaps found in
[the feature inventory](FEATURE_INVENTORY.md) with the backlogs that specialist programs already
keep, without restating their requirements.

- **Source of truth:** [PRODUCT_BACKLOG.json](PRODUCT_BACKLOG.json), validated by
  [schema/product-backlog.schema.json](schema/product-backlog.schema.json).
- **Scores and priorities** are computed by `python docs/product/tools/product_data.py score` using
  the model in [the prioritized roadmap](PRIORITIZED_ROADMAP.md#scoring-model). Never edit
  `computed` by hand.
- **Tables below** are generated between the markers; edit the JSON instead.

## How items are owned

Every item names the workstream that builds it. When a specialist program already owns the work, the
item here is a tracking row: its capability says "owned and specified by", its acceptance criteria
point at that program's definition, and `source_refs` holds that program's own ids. This keeps one
place to rank all work without creating a second, drifting copy of anyone's requirements.

| Workstream | Ids it uses | Where its own plan lives |
| --- | --- | --- |
| Git-native development | Git phase 1-4, FD-113, FD-120, FD-132..135 | [git-integration-plan.md](../git-integration-plan.md) |
| Enterprise readiness | FD-101..105, 125, 126, 130, 131, 136..140 | [enterprise-backlog.md](enterprise-backlog.md), [enterprise-gap-analysis.md](enterprise-gap-analysis.md) |
| Staged roadmap | FD-114..118, 121, 127, 128 | [scale-and-operations-plan.md](../scale-and-operations-plan.md) |
| Prove | FD-109..112, 119, Prove plan #n | [prove/plan.md](../prove/plan.md) |
| Workload management | FD-122..124, Workloads stage n | [workloads/plan.md](../workloads/plan.md) |
| Bring your own AI integration | PR-AI-001..012 | [ai-integration-backlog.md](ai-integration-backlog.md) |
| SQL development sprint | SQL-001..006 | Its project thread |
| Enterprise governance foundation | GOV-001..005, FD-129 (moved from Enterprise readiness on 2026-10-17) | Its project thread |
| Administration and FinOps sprint | FINOPS-001..005 | Its project thread |
| Local Fabric pipeline execution | ORCH-001..010 | Its project thread |
| Local data engine and OneLake publishing | FD-LDE-001..020 | Its project thread; tracked here as PB-099, with overlaps noted on PB-015, PB-019..PB-024, PB-067 and PB-079 |
| Fuller What's new dialog | none (one thread) | Its project thread; tracked here as PB-100 |
| Master product (unassigned) | none | Items found in this assessment that no program owns yet; the product owner assigns them |

Git functionality appears only as tracking rows owned by the Git-native program (PB-040, PB-041,
PB-097). AI capabilities are provider-neutral: the backlog contains no embedded assistant, model
API key or provider-specific integration, only the MCP and local API work of the AI integration
program.

## Deduplication

Before adding an item, search this file and each program's plan for the same problem. When two
requests describe one problem, keep one item, list the other in `duplicates` with where it came
from, and keep the stricter acceptance criteria. In this first pass:

- The "calibrate cost estimates" gap from the inventory is merged into FINOPS-001 (PB-050).
- The "column completion" and "debugger" gaps were already Prove plan items (#12, #44); they are
  tracked, not restated (PB-027, PB-026).
- Enterprise gap-analysis rows marked DUPLICATE (FD-109..124, 127, 128, 132..135) are tracked once,
  under the program the gap analysis already names.
- The ten-flow tenant smoke test (PB-002) and FD-101's live test harness overlap: the harness is the
  tool, the smoke test is the run. PB-002 covers the run; FD-101 stays with enterprise readiness.

## Summary

<!-- BEGIN GENERATED: summary -->
| Category | P0 | P1 | P2 | P3 | Total |
| --- | ---: | ---: | ---: | ---: | ---: |
| [Local Development and Execution](#local-development-and-execution) | 1 | 3 | 2 | 0 | 6 |
| [SQL, Python, and Notebook Development](#sql-python-and-notebook-development) | 0 | 1 | 5 | 3 | 9 |
| [Data Engineering and Transformation](#data-engineering-and-transformation) | 0 | 0 | 5 | 0 | 5 |
| [Pipelines and Orchestration](#pipelines-and-orchestration) | 0 | 1 | 4 | 3 | 8 |
| [Fabric Integration and Deployment](#fabric-integration-and-deployment) | 0 | 1 | 5 | 0 | 6 |
| [Enterprise Shared Services](#enterprise-shared-services) | 0 | 1 | 2 | 0 | 3 |
| [Data Governance and Quality](#data-governance-and-quality) | 0 | 2 | 6 | 0 | 8 |
| [Developer Experience and Productivity](#developer-experience-and-productivity) | 0 | 2 | 5 | 1 | 8 |
| [Enterprise Administration and FinOps](#enterprise-administration-and-finops) | 0 | 3 | 5 | 2 | 10 |
| [Extensibility, APIs, CLI, and MCP](#extensibility-apis-cli-and-mcp) | 0 | 2 | 1 | 1 | 4 |
| [Security and Enterprise Readiness](#security-and-enterprise-readiness) | 1 | 7 | 2 | 0 | 10 |
| [Reliability, Testing, and Performance](#reliability-testing-and-performance) | 1 | 5 | 5 | 0 | 11 |
| **Total** | **3** | **28** | **47** | **10** | **88** |
<!-- END GENERATED: summary -->

States: Proposed (not yet approved), Approved (the product owner said go), In progress, In review
(a PR exists), Accepted (passed the [implementation review](IMPLEMENTATION_REVIEW_PROCESS.md)),
Rejected, Deferred. Only the product owner moves an item to Approved, and only an implementation
review moves it to Accepted; a merge alone does neither.

<!-- BEGIN GENERATED: items -->
## Local Development and Execution

| Id | Item | Priority | Owner | Effort | State |
| --- | --- | --- | --- | --- | --- |
| [PB-012](#pb-012) | Published Spark API compatibility matrix for this PC | P1 | Prove | M | Proposed |
| [PB-013](#pb-013) | Fix local-parity defects that give wrong answers | P1 | Master product (unassigned) | M | Proposed |
| [PB-014](#pb-014) | AST-based compatibility analyzer with an Unknown outcome | P1 | Prove | M | Proposed |
| [PB-015](#pb-015) | Write semantics for MERGE, UPDATE and DELETE on this PC | P2 | Prove | L | Proposed |
| [PB-016](#pb-016) | Reproducible environments and Fabric Environment items | P2 | Prove | L | Proposed |
| [PB-099](#pb-099) | Local data engine and OneLake publishing program | P0 | Local data engine and OneLake publishing | XL | In progress |

### PB-012

**Published Spark API compatibility matrix for this PC** · P1 (score 3.51) · M · confidence High · Proposed

- **Problem:** Notebook authors can't tell which PySpark and notebookutils calls work on the PC before they hit an error; docs list some gaps by hand and the code differs from them in places.
- **Proposed capability:** A matrix of every supported, partial and refused Spark, Spark SQL and notebookutils call, generated from the parity tests on each build and published in the docs and the Prove panel.
- **Current-state evidence:** sidecar/fabric_desktop/spark_local.py raises for MERGE/UPDATE/DELETE and ignores partitionBy and reader schema(); docs/notebooks.md lists limits by hand; No generated list exists
- **Inventory features:** [FEAT-NB-04](FEATURE_INVENTORY.md#feat-nb-04), [FEAT-NB-05](FEATURE_INVENTORY.md#feat-nb-05), [FEAT-SQL-02](FEATURE_INVENTORY.md#feat-sql-02)
- **Dependencies:** none
- **Owner:** Prove (FD-111, Prove plan Stage 2)
- **Acceptance criteria:**
    - Each row names the call, its status on the PC (works, differs, refused) and the test that proves it
    - CI fails when a test-backed row changes status without the matrix changing
    - The docs page states that local results are not claimed equivalent to Fabric unless a proof says so

### PB-013

**Fix local-parity defects that give wrong answers** · P1 (score 2.56) · M · confidence High · Proposed

- **Problem:** Several local behaviors differ silently from Fabric or from the app's own UI: a notebook using variableLibrary is flagged Fabric-only, %%sql MERGE cells report ok, notebookutils.session is missing though documented, insertInto defaults to errorifexists, and scheduled runs execute frozen cells that UI runs skip.
- **Proposed capability:** Each defect fixed or turned into an explicit, visible refusal, with a regression test per defect.
- **Current-state evidence:** fabric_run.py _FABRIC_ONLY includes notebookutils.variableLibrary; notebook.py %%sql MERGE path prints a note and returns ok; notebookutils_local.py docstring names session; attribute absent; local_schedules.py runs all cells; notebook.js skips frozen cells (inference from code reading)
- **Inventory features:** [FEAT-NB-04](FEATURE_INVENTORY.md#feat-nb-04), [FEAT-NB-05](FEATURE_INVENTORY.md#feat-nb-05), [FEAT-NB-07](FEATURE_INVENTORY.md#feat-nb-07), [FEAT-SQL-02](FEATURE_INVENTORY.md#feat-sql-02), [FEAT-ORC-01](FEATURE_INVENTORY.md#feat-orc-01)
- **Dependencies:** none
- **Owner:** Master product (unassigned) (FD-111 (analyzer is separate, PB-014))
- **Mandatory (required):** Wrong-answer defects in local execution undermine the core promise and the rule that local results are never claimed equivalent without proof.
- **Implementation package:** [pr-ready/PB-013.md](pr-ready/PB-013.md)
- **Acceptance criteria:**
    - compute_check reports a variableLibrary notebook as able to run on the PC
    - A %%sql MERGE, UPDATE or DELETE cell ends with a 'skipped on this PC' status and Run all stops
    - notebookutils.session, lakehouse and connections raise a message that says what to do instead
    - Scheduled and UI runs treat frozen cells the same way, covered by one test

### PB-014

**AST-based compatibility analyzer with an Unknown outcome** · P1 (score 2.73) · M · confidence Medium · Proposed

- **Problem:** The compute check is a regex scan, so it misses aliased imports and can't say 'not sure'.
- **Proposed capability:** A Python AST analyzer that reports, per cell, runs on PC / runs in Fabric / unknown with the reason, shared by Runs on, Prove and the advisor.
- **Current-state evidence:** fabric_run.py compute_check is regex-based; docs/product/enterprise-gap-analysis.md FD-111 partial
- **Inventory features:** [FEAT-NB-07](FEATURE_INVENTORY.md#feat-nb-07)
- **Dependencies:** [PB-013](#pb-013)
- **Owner:** Prove (FD-111)
- **Acceptance criteria:**
    - Aliased and from-imports are recognized
    - Unknown is a distinct result shown in the ribbon
    - Prove and Runs on read the same analyzer output

### PB-015

**Write semantics for MERGE, UPDATE and DELETE on this PC** · P2 (score 2.25) · L · confidence Medium · Proposed

- **Problem:** Spark notebooks that MERGE or UPDATE must run in Fabric today, so the most common incremental pattern costs capacity during development.
- **Proposed capability:** spark.sql MERGE/UPDATE/DELETE and DeltaTable merge executed through delta-rs on the PC, with the Fabric Delta profile check, behind a Preview flag.
- **Current-state evidence:** spark_local.py raises for MERGE, UPDATE, DELETE; meta/engine.py already merges with delta-rs for metadata runs
- **Inventory features:** [FEAT-NB-04](FEATURE_INVENTORY.md#feat-nb-04)
- **Dependencies:** [PB-012](#pb-012), Overlaps FD-LDE-005 local Delta: the LDE thread builds create, append, overwrite and time travel; MERGE, UPDATE and DELETE semantics stay with this item (owners per docs/local-data-engine-plan.md section 3, PR #126)
- **Owner:** Prove (Prove plan (local Spark coverage))
- **Acceptance criteria:**
    - The parity suite covers MERGE with matched/not-matched clauses, UPDATE and DELETE
    - Written tables pass the Fabric Delta profile check
    - Unsupported clauses are refused with a named reason
    - A Prove run of a MERGE notebook verifies equal outputs on PC and Fabric before the Preview tag is removed

### PB-016

**Reproducible environments and Fabric Environment items** · P2 (score 1.9) · L · confidence Low · Proposed

- **Problem:** Packages differ between the PC and Fabric and nothing installs or pins them; Fabric Environment items can't be read or attached.
- **Proposed capability:** Environment profiles that pin packages for the PC and map to a Fabric Environment item's libraries and Spark settings.
- **Current-state evidence:** envprofile.py classifies imports only; fabric_api.py has no Environment item calls; Package-source decision open (Prove)
- **Inventory features:** [FEAT-PRV-04](FEATURE_INVENTORY.md#feat-prv-04), [FEAT-FAB-09](FEATURE_INVENTORY.md#feat-fab-09)
- **Dependencies:** Product-owner decision: package source for environment profiles (Prove)
- **Owner:** Prove (FD-109)
- **Acceptance criteria:**
    - A notebook's profile installs the same versions on the PC that its Fabric Environment declares
    - The app can attach a notebook to an existing Environment item
    - Drift between PC and Fabric versions is shown before a run

### PB-099

**Local data engine and OneLake publishing program** · P0 (score 2.3) · XL · confidence Low · In progress

- **Problem:** Local files and SQL transformations can't be developed in a persistent local engine and published to OneLake with validation, publish history and incremental sync, without Fabric capacity.
- **Proposed capability:** Owned and specified by the Local data engine and OneLake publishing thread (FD-LDE-001..020); not restated here. Its plan (docs/local-data-engine-plan.md section 3) names the owner of each story; stories owned by other threads are FD-LDE-003 and 014 (SQL thread) and the FD-114/117 base of FD-LDE-011 (staged roadmap).
- **Current-state evidence:** DuckDB, sqlglot and delta-rs already in the engine (sql.py, spark_local.py, publish.py, meta/store.py); Audit, story owners and PR plan in docs/local-data-engine-plan.md (PR #126, which also carries FD-LDE-001/002)
- **Inventory features:** [FEAT-SQL-01](FEATURE_INVENTORY.md#feat-sql-01), [FEAT-NB-11](FEATURE_INVENTORY.md#feat-nb-11), [FEAT-ONE-05](FEATURE_INVENTORY.md#feat-one-05)
- **Dependencies:** none
- **Owner:** Local data engine and OneLake publishing (FD-LDE-001..020)
- **Priority set by the product owner:** P0 (Chris (product owner), 2026-10-17): Local execution without Fabric capacity is the product's core mission; build FD-LDE-001..020 as P0 despite its size
- **Merged duplicates:** Overlaps PB-021..PB-025 and PB-020 (SQL sprint: SQL editor, dialect, execution, connectivity, local Warehouse); Overlaps PB-015 (local Delta writes), PB-019 (incremental), PB-067 (large datasets), PB-050/PB-090 (FinOps), PB-079 (TOML job runner) and the metadata framework
- **Acceptance criteria:**
    - As defined in FD-LDE-001..020 by its owning thread
    - Each story's overlap with an existing item follows the owner named in that plan

## SQL, Python, and Notebook Development

| Id | Item | Priority | Owner | Effort | State |
| --- | --- | --- | --- | --- | --- |
| [PB-020](#pb-020) | Local Warehouse simulation | P3 | SQL development sprint | XL | Proposed |
| [PB-021](#pb-021) | SQL editor with IntelliSense and formatting | P2 | SQL development sprint | M | In progress |
| [PB-022](#pb-022) | Fabric SQL dialect compatibility analyzer | P1 | SQL development sprint | M | In progress |
| [PB-023](#pb-023) | Managed SQL execution with cancel and streaming | P2 | SQL development sprint | M | In progress |
| [PB-024](#pb-024) | Native Warehouse and SQL database connectivity | P3 | SQL development sprint | L | Proposed |
| [PB-025](#pb-025) | SQL object explorer | P2 | SQL development sprint | M | Proposed |
| [PB-026](#pb-026) | Notebook debugger | P3 | Prove | L | Proposed |
| [PB-027](#pb-027) | Column completion for attached tables | P2 | Prove | S | Proposed |
| [PB-028](#pb-028) | Package install magics that work on this PC | P2 | Prove | S | Proposed |

### PB-020

**Local Warehouse simulation** · P3 (score 1.21) · XL · confidence Low · Proposed

- **Problem:** Warehouse T-SQL can only be tested against Fabric compute.
- **Proposed capability:** Owned and specified by the SQL development sprint (SQL-006).
- **Current-state evidence:** No local T-SQL engine in the repo
- **Inventory features:** [FEAT-FAB-11](FEATURE_INVENTORY.md#feat-fab-11)
- **Dependencies:** [PB-022](#pb-022), Overlaps FD-LDE-004 T-SQL compatibility (LDE thread, on SQL-002's profiles) and FD-LDE-012 local TDS endpoint (LDE thread, only after Chris's go) (owners per docs/local-data-engine-plan.md section 3, PR #126)
- **Owner:** SQL development sprint (SQL-006)
- **Acceptance criteria:**
    - As defined in SQL-006 by its owning thread

### PB-021

**SQL editor with IntelliSense and formatting** · P2 (score 2.42) · M · confidence High · In progress

- **Problem:** The SQL page is a plain textarea with no completion or formatting.
- **Proposed capability:** Owned and specified by the SQL development sprint (SQL-001).
- **Current-state evidence:** app/ui/app.js SQL page uses a textarea
- **Inventory features:** [FEAT-SQL-01](FEATURE_INVENTORY.md#feat-sql-01)
- **Dependencies:** Overlaps FD-LDE-003 local SQL in the editor: the SQL thread owns the editor; the LDE thread supplies the local engine calls (owners per docs/local-data-engine-plan.md section 3, PR #126)
- **Owner:** SQL development sprint (SQL-001)
- **Acceptance criteria:**
    - As defined in SQL-001 by its owning thread

### PB-022

**Fabric SQL dialect compatibility analyzer** · P1 (score 2.73) · M · confidence Medium · In progress

- **Problem:** Spark SQL translated to DuckDB can succeed locally and still fail in Fabric (or the reverse), and nothing says so.
- **Proposed capability:** Owned and specified by the SQL development sprint (SQL-002).
- **Current-state evidence:** sql.py translates with sqlglot without a compatibility report
- **Inventory features:** [FEAT-SQL-01](FEATURE_INVENTORY.md#feat-sql-01), [FEAT-FAB-11](FEATURE_INVENTORY.md#feat-fab-11)
- **Dependencies:** Overlaps FD-LDE-004 T-SQL translation (LDE thread, built on SQL-002's profiles) and FD-LDE-014 SQL compatibility validation (SQL thread) (owners per docs/local-data-engine-plan.md section 3, PR #126)
- **Owner:** SQL development sprint (SQL-002)
- **Acceptance criteria:**
    - As defined in SQL-002 by its owning thread

### PB-023

**Managed SQL execution with cancel and streaming** · P2 (score 2.27) · M · confidence High · In progress

- **Problem:** SQL queries can't be cancelled, show only the first 1,000 rows, and Download CSV runs the query again.
- **Proposed capability:** Owned and specified by the SQL development sprint (SQL-003).
- **Current-state evidence:** service.py run_sql returns a fixed page; No cancel RPC for SQL
- **Inventory features:** [FEAT-SQL-01](FEATURE_INVENTORY.md#feat-sql-01)
- **Dependencies:** Overlaps FD-LDE-003 and FD-LDE-019: the SQL thread owns execution and streaming (SQL-003); the LDE thread supplies engine calls and large-dataset export (owners per docs/local-data-engine-plan.md section 3, PR #126)
- **Owner:** SQL development sprint (SQL-003)
- **Acceptance criteria:**
    - As defined in SQL-003 by its owning thread

### PB-024

**Native Warehouse and SQL database connectivity** · P3 (score 1.55) · L · confidence Medium · Proposed

- **Problem:** T-SQL developers can't reach a Warehouse, SQL analytics endpoint or Fabric SQL database from the app.
- **Proposed capability:** Owned and specified by the SQL development sprint (SQL-004).
- **Current-state evidence:** No TDS or ODBC client in sidecar/constraints.txt
- **Inventory features:** [FEAT-FAB-11](FEATURE_INVENTORY.md#feat-fab-11)
- **Dependencies:** [PB-021](#pb-021), Overlaps FD-LDE-012 and FD-LDE-013 local TDS endpoint and Power BI Desktop (LDE thread, only after Chris's go) (owners per docs/local-data-engine-plan.md section 3, PR #126)
- **Owner:** SQL development sprint (SQL-004)
- **Acceptance criteria:**
    - As defined in SQL-004 by its owning thread

### PB-025

**SQL object explorer** · P2 (score 2.29) · M · confidence Medium · Proposed

- **Problem:** There is no tree of schemas, tables, views and procedures for SQL work.
- **Proposed capability:** Owned and specified by the SQL development sprint (SQL-005).
- **Current-state evidence:** Lakehouse explorer lists tables only
- **Inventory features:** [FEAT-FAB-11](FEATURE_INVENTORY.md#feat-fab-11)
- **Dependencies:** [PB-024](#pb-024)
- **Owner:** SQL development sprint (SQL-005)
- **Acceptance criteria:**
    - As defined in SQL-005 by its owning thread

### PB-026

**Notebook debugger** · P3 (score 1.6) · L · confidence Medium · Proposed

- **Problem:** There are no breakpoints, stepping or watches; competitors (Databricks, Snowflake, VS Code) offer debugging.
- **Proposed capability:** Breakpoints and stepping in the process kernel, planned by the Prove program.
- **Current-state evidence:** No debugger code; docs/prove/plan.md Stage 5 backlog #44
- **Inventory features:** [FEAT-EXT-04](FEATURE_INVENTORY.md#feat-ext-04)
- **Dependencies:** [PB-073](#pb-073)
- **Owner:** Prove (Prove plan #44)
- **Acceptance criteria:**
    - Set a breakpoint in a cell, step, inspect variables, continue
    - Works only in the process kernel; the ribbon says so otherwise

### PB-027

**Column completion for attached tables** · P2 (score 2.21) · S · confidence High · Proposed

- **Problem:** Completion knows only what the kernel has run, so column names of attached tables aren't offered.
- **Proposed capability:** Completion of table and column names from the attached lakehouse's Delta schemas.
- **Current-state evidence:** notebook.py kernel_complete returned nothing for names not yet in the namespace (smoke)
- **Inventory features:** [FEAT-NB-08](FEATURE_INVENTORY.md#feat-nb-08)
- **Dependencies:** none
- **Owner:** Prove (Prove plan #12)
- **Acceptance criteria:**
    - Typing a table alias offers its columns without running a cell
    - Works offline from cached schemas

### PB-028

**Package install magics that work on this PC** · P2 (score 2.0) · S · confidence Medium · Proposed

- **Problem:** %pip is skipped with a note, so a missing package fails later; !pip and %%time would be syntax errors.
- **Proposed capability:** %pip and !pip install into the notebook's environment profile, %%time supported.
- **Current-state evidence:** notebook.py magic handling (code reading)
- **Inventory features:** [FEAT-NB-06](FEATURE_INVENTORY.md#feat-nb-06)
- **Dependencies:** [PB-016](#pb-016)
- **Owner:** Prove (FD-109)
- **Acceptance criteria:**
    - %pip install x installs into the active profile or explains why it can't
    - !pip and %%time no longer raise syntax errors

## Data Engineering and Transformation

| Id | Item | Priority | Owner | Effort | State |
| --- | --- | --- | --- | --- | --- |
| [PB-019](#pb-019) | Late-arriving rows and source deletes in incremental loads | P2 | Staged roadmap | M | Proposed |
| [PB-031](#pb-031) | Schema evolution without full rewrites | P2 | Staged roadmap | M | Proposed |
| [PB-067](#pb-067) | Bounded-memory runs: run dialog and orphan-file cleanup | P2 | Staged roadmap | M | Proposed |
| [PB-068](#pb-068) | Key lookups without reading whole key tables | P2 | Staged roadmap | M | Proposed |
| [PB-079](#pb-079) | Decide the future of the TOML job runner | P2 | Master product (unassigned) | S | Proposed |

### PB-019

**Late-arriving rows and source deletes in incremental loads** · P2 (score 2.19) · M · confidence Medium · Proposed

- **Problem:** The watermark filter is strictly greater-than, so rows that arrive late are dropped, and deletes at the source never reach the table.
- **Proposed capability:** A lookback window and soft- or hard-delete handling per entity.
- **Current-state evidence:** meta/engine.py watermark comparison; docs/scale-and-operations-plan.md capability 3
- **Inventory features:** [FEAT-META-02](FEATURE_INVENTORY.md#feat-meta-02)
- **Dependencies:** Overlaps FD-LDE-011 incremental and MERGE: this item's owner keeps FD-114/117; the LDE thread builds dataset-level publish on top of them (owners per docs/local-data-engine-plan.md section 3, PR #126)
- **Owner:** Staged roadmap (FD-117)
- **Acceptance criteria:**
    - A late row inside the lookback lands exactly once
    - A source delete marks or removes the row per the entity setting
    - Tests cover both on the PC

### PB-031

**Schema evolution without full rewrites** · P2 (score 2.04) · M · confidence Medium · Proposed

- **Problem:** Type changes rewrite whole tables and contracts aren't versioned per change.
- **Proposed capability:** Additive and widening changes applied in place; versioned schema history tied to the breaking-change check.
- **Current-state evidence:** meta/drift.py; docs/scale-and-operations-plan.md FD-118
- **Inventory features:** [FEAT-META-07](FEATURE_INVENTORY.md#feat-meta-07)
- **Dependencies:** none
- **Owner:** Staged roadmap (FD-118)
- **Acceptance criteria:**
    - Widening a type commits without rewriting data files
    - Each schema change is recorded with its version

### PB-067

**Bounded-memory runs: run dialog and orphan-file cleanup** · P2 (score 2.52) · M · confidence High · Proposed

- **Problem:** The preflight estimate exists but has no screen, and a stopped batch can leave orphan files.
- **Proposed capability:** A run dialog showing the estimate and choices, and cleanup of files a stopped batch wrote.
- **Current-state evidence:** meta_preflight has no caller in app/ui; meta/batches.py
- **Inventory features:** [FEAT-META-06](FEATURE_INVENTORY.md#feat-meta-06)
- **Dependencies:** Overlaps FD-LDE-019 large datasets: the LDE thread with the SQL thread (SQL-003) (owners per docs/local-data-engine-plan.md section 3, PR #126)
- **Owner:** Staged roadmap (FD-114)
- **Acceptance criteria:**
    - The run dialog shows the estimate before a large run (after a mockup)
    - Files from a stopped batch are removed or vacuumed

### PB-068

**Key lookups without reading whole key tables** · P2 (score 2.42) · M · confidence High · Proposed

- **Problem:** Every keyed run reads the whole key table, which grows with the business.
- **Proposed capability:** Key lookups limited to the business keys in the batch.
- **Current-state evidence:** meta/keys.py read_keys
- **Inventory features:** [FEAT-META-05](FEATURE_INVENTORY.md#feat-meta-05)
- **Dependencies:** none
- **Owner:** Staged roadmap (FD-115)
- **Acceptance criteria:**
    - A keyed run reads only matching key rows, shown by a test on a large key table

### PB-079

**Decide the future of the TOML job runner** · P2 (score 1.85) · S · confidence High · Proposed

- **Problem:** Job files run from the CLI only; the app labels their history as a notebook write and nothing in the UI creates them.
- **Proposed capability:** A product decision: expose jobs in the app, or keep them a CLI and agent feature and document it so.
- **Current-state evidence:** jobs.py, cli.py preview/run; no UI caller of preview_job or run_job
- **Inventory features:** [FEAT-NB-11](FEATURE_INVENTORY.md#feat-nb-11)
- **Dependencies:** Overlaps FD-LDE-006 local transformations: the LDE thread builds parameterized SQL scripts; pipeline orchestration stays with the ORCH thread (owners per docs/local-data-engine-plan.md section 3, PR #126)
- **Owner:** Master product (unassigned)
- **Acceptance criteria:**
    - Decision recorded in docs/product/implementation-decisions.md
    - Docs and Monitor labels match the decision

## Pipelines and Orchestration

| Id | Item | Priority | Owner | Effort | State |
| --- | --- | --- | --- | --- | --- |
| [PB-030](#pb-030) | Data pipeline and Copy job scope after decision D2 | P3 | Staged roadmap | L | Proposed |
| [PB-035](#pb-035) | Pipeline definition engine | P2 | Local Fabric pipeline execution | L | In progress |
| [PB-036](#pb-036) | Pipeline activity compatibility registry | P1 | Local Fabric pipeline execution | M | Proposed |
| [PB-037](#pb-037) | Local DAG execution engine | P3 | Local Fabric pipeline execution | XL | Proposed |
| [PB-044](#pb-044) | Agent queue pruning and dead-job requeue | P2 | Master product (unassigned) | S | Proposed |
| [PB-045](#pb-045) | Shared queue for several workers | P3 | Workload management | L | Proposed |
| [PB-048](#pb-048) | Schedule editing and safer schedule removal | P2 | Master product (unassigned) | M | Proposed |
| [PB-049](#pb-049) | Run the pipeline mailbox once on a tenant | P2 | Staged roadmap | S | Proposed |

### PB-030

**Data pipeline and Copy job scope after decision D2** · P3 (score 1.6) · L · confidence Low · Proposed

- **Problem:** Nobody creates Copy jobs or pipelines from the app, and who lands data into LH_Landing is undecided.
- **Proposed capability:** Decision D2 first; then guided ingestion as the staged roadmap defines it.
- **Current-state evidence:** fabric_api.py has no pipeline or Copy job create; Open question D2 in project memory
- **Inventory features:** [FEAT-FAB-10](FEATURE_INVENTORY.md#feat-fab-10), [FEAT-MESH-03](FEATURE_INVENTORY.md#feat-mesh-03)
- **Dependencies:** Product-owner decision D2: who lands data and creates Copy jobs
- **Owner:** Staged roadmap (FD-116)
- **Acceptance criteria:**
    - D2 answered by the product owner
    - Guided ingestion stages updated to match

### PB-035

**Pipeline definition engine** · P2 (score 2.15) · L · confidence Medium · In progress

- **Problem:** Fabric Data pipeline definitions can't be read, validated or run outside Fabric.
- **Proposed capability:** Owned and specified by the Local Fabric pipeline execution thread (ORCH-001).
- **Current-state evidence:** No pipeline definition parsing in the repo
- **Inventory features:** [FEAT-FAB-10](FEATURE_INVENTORY.md#feat-fab-10)
- **Dependencies:** none
- **Owner:** Local Fabric pipeline execution (ORCH-001)
- **Acceptance criteria:**
    - As defined in ORCH-001 by its owning thread

### PB-036

**Pipeline activity compatibility registry** · P1 (score 2.76) · M · confidence Medium · Proposed

- **Problem:** There is no list of which pipeline activities can run locally.
- **Proposed capability:** Owned and specified by the Local Fabric pipeline execution thread (ORCH-002).
- **Current-state evidence:** No activity registry in the repo
- **Dependencies:** [PB-035](#pb-035)
- **Owner:** Local Fabric pipeline execution (ORCH-002)
- **Acceptance criteria:**
    - As defined in ORCH-002 by its owning thread

### PB-037

**Local DAG execution engine** · P3 (score 1.35) · XL · confidence Low · Proposed

- **Problem:** Pipelines run only in Fabric.
- **Proposed capability:** Owned and specified by the Local Fabric pipeline execution thread (ORCH-003; ORCH-004..010 follow in that thread).
- **Current-state evidence:** Metadata runs order entities by dependency; no general DAG runner
- **Dependencies:** [PB-035](#pb-035), [PB-036](#pb-036)
- **Owner:** Local Fabric pipeline execution (ORCH-003, ORCH-004..010)
- **Acceptance criteria:**
    - As defined in ORCH-003 by its owning thread

### PB-044

**Agent queue pruning and dead-job requeue** · P2 (score 2.3) · S · confidence High · Proposed

- **Problem:** The agent's SQLite queue is never pruned and a dead job can't be revived without editing the database.
- **Proposed capability:** Retention for finished jobs and an agent requeue command.
- **Current-state evidence:** jobqueue.py has no prune (inference from code reading); agent CLI has no requeue
- **Inventory features:** [FEAT-ORC-02](FEATURE_INVENTORY.md#feat-orc-02)
- **Dependencies:** none
- **Owner:** Master product (unassigned)
- **Implementation package:** [pr-ready/PB-044.md](pr-ready/PB-044.md)
- **Acceptance criteria:**
    - Finished jobs older than the retention are removed on start
    - fabric-desktop agent requeue <id> moves a dead job back to queued
    - Tests cover both

### PB-045

**Shared queue for several workers** · P3 (score 0.9) · L · confidence Low · Proposed

- **Problem:** Each agent has its own queue, so work can't spread across machines.
- **Proposed capability:** A worker registry and shared claims, as the Workload management program plans.
- **Current-state evidence:** jobqueue.py per machine; docs/agent.md
- **Inventory features:** [FEAT-ORC-06](FEATURE_INVENTORY.md#feat-orc-06)
- **Dependencies:** [PB-003](#pb-003)
- **Owner:** Workload management (FD-124)
- **Acceptance criteria:**
    - As defined in FD-124 by its owning program

### PB-048

**Schedule editing and safer schedule removal** · P2 (score 1.84) · M · confidence High · Proposed

- **Problem:** Schedules can't be edited, removal has no confirmation, and only 12 time zones are offered.
- **Proposed capability:** Edit time and frequency, confirm removal, full time-zone list; PC schedules that run when the user is signed out.
- **Current-state evidence:** schedules.js; local_schedules.py
- **Inventory features:** [FEAT-FAB-08](FEATURE_INVENTORY.md#feat-fab-08), [FEAT-ORC-01](FEATURE_INVENTORY.md#feat-orc-01)
- **Dependencies:** none
- **Owner:** Master product (unassigned)
- **Acceptance criteria:**
    - A schedule's time can be changed in place
    - Remove asks for confirmation
    - Every Windows time zone can be picked

### PB-049

**Run the pipeline mailbox once on a tenant** · P2 (score 2.21) · S · confidence Medium · Proposed

- **Problem:** The pipeline-to-agent mailbox has only run against fakes and records free-text submitters.
- **Proposed capability:** One live run per the live test, and the writer's identity recorded.
- **Current-state evidence:** mailbox.py tests use fakes; docs/enterprise-roadmap.md still calls it unbuilt
- **Inventory features:** [FEAT-ORC-04](FEATURE_INVENTORY.md#feat-orc-04)
- **Dependencies:** [PB-002](#pb-002)
- **Owner:** Staged roadmap (Scale plan capability 7)
- **Acceptance criteria:**
    - A Fabric pipeline request runs once on an agent and reports back on the maintainer's tenant
    - submitted_by comes from the token

## Fabric Integration and Deployment

| Id | Item | Priority | Owner | Effort | State |
| --- | --- | --- | --- | --- | --- |
| [PB-018](#pb-018) | Publish matches Fabric items by id, not display name | P2 | Master product (unassigned) | S | Proposed |
| [PB-040](#pb-040) | Fabric workspace Git integration | P2 | Git-native development | L | Proposed |
| [PB-041](#pb-041) | Deployment preview and item deploy | P2 | Git-native development | L | Proposed |
| [PB-042](#pb-042) | Release rollback and the committed-revision rule | P2 | Staged roadmap | M | Proposed |
| [PB-043](#pb-043) | Cancel a Fabric run from the app | P2 | Master product (unassigned) | S | Proposed |
| [PB-051](#pb-051) | Fabric REST client resilience | P1 | Master product (unassigned) | S | Proposed |

### PB-018

**Publish matches Fabric items by id, not display name** · P2 (score 2.16) · S · confidence Medium · Proposed

- **Problem:** Publishing finds the Fabric notebook by display name, so a rename in either place makes a duplicate.
- **Proposed capability:** Record the Fabric item id on first publish and update that item afterwards.
- **Current-state evidence:** fabric_api.py save_notebook matches an existing notebook by displayName; workload_service.py _record_notebook_item already records the item id after publish, but publish doesn't use it
- **Inventory features:** [FEAT-FAB-06](FEATURE_INVENTORY.md#feat-fab-06)
- **Dependencies:** none
- **Owner:** Master product (unassigned)
- **Implementation package:** [pr-ready/PB-018.md](pr-ready/PB-018.md)
- **Acceptance criteria:**
    - Renaming the local notebook and publishing again updates the same Fabric item
    - A deleted Fabric item is recreated with a note

### PB-040

**Fabric workspace Git integration** · P2 (score 2.31) · L · confidence High · Proposed

- **Problem:** Workspaces connected to Git can't be seen or driven from the app.
- **Proposed capability:** Owned and specified by the Git-native program (Phase 2); not restated here.
- **Current-state evidence:** docs/git-integration-plan.md Phase 2
- **Inventory features:** [FEAT-FAB-12](FEATURE_INVENTORY.md#feat-fab-12)
- **Dependencies:** Product-owner go for Phase 2
- **Owner:** Git-native development (Git phase 2)
- **Acceptance criteria:**
    - As defined in the Git-native plan Phase 2

### PB-041

**Deployment preview and item deploy** · P2 (score 1.93) · L · confidence Medium · Proposed

- **Problem:** Promote copies metadata configuration only; other items move by hand.
- **Proposed capability:** Owned and specified by the Git-native program (FD-120, Phase 3).
- **Current-state evidence:** docs/git-integration-plan.md Phase 3
- **Inventory features:** [FEAT-FAB-13](FEATURE_INVENTORY.md#feat-fab-13)
- **Dependencies:** [PB-040](#pb-040)
- **Owner:** Git-native development (FD-120, Git phase 3)
- **Acceptance criteria:**
    - As defined in FD-120 by its owning program

### PB-042

**Release rollback and the committed-revision rule** · P2 (score 1.95) · M · confidence Medium · Proposed

- **Problem:** A bad promote can be undone only table by table, and Test/Prod can be promoted from uncommitted files.
- **Proposed capability:** Release-level rollback from meta.deployments and decision D1 enforced.
- **Current-state evidence:** meta/release.py; docs/scale-and-operations-plan.md D1
- **Inventory features:** [FEAT-META-09](FEATURE_INVENTORY.md#feat-meta-09), [FEAT-META-10](FEATURE_INVENTORY.md#feat-meta-10)
- **Dependencies:** Product-owner decision D1
- **Owner:** Staged roadmap (FD-121)
- **Pull requests:** [#113](https://github.com/KratosDataGod/fabric-desktop/pull/113)
- **Acceptance criteria:**
    - D1 answered
    - Rolling back a release restores the previous configuration in every target
    - Rollback is recorded as its own release

### PB-043

**Cancel a Fabric run from the app** · P2 (score 2.45) · S · confidence High · Proposed

- **Problem:** A Fabric run started here can be cancelled only as a workload action in Monitor > Workloads (Preview); Monitor > Runs and the activity tray, where people watch the run, offer no Cancel.
- **Proposed capability:** A Cancel action on running Fabric jobs in Monitor > Runs and the tray, reusing cancel_fabric_run.
- **Current-state evidence:** cloud_service.py cancel_fabric_run is called only from workload_service.py workload_act; app/ui/workloads.js offers cancel through workload actions
- **Inventory features:** [FEAT-FAB-07](FEATURE_INVENTORY.md#feat-fab-07)
- **Dependencies:** none
- **Owner:** Master product (unassigned)
- **Implementation package:** [pr-ready/PB-043.md](pr-ready/PB-043.md)
- **Acceptance criteria:**
    - Cancel in Monitor > Runs and the tray stops the Fabric job and the run shows Cancelled
    - A test covers cancel_fabric_run

### PB-051

**Fabric REST client resilience** · P1 (score 2.16) · S · confidence High · Proposed

- **Problem:** The client retries only 429s; a 5xx or expired token fails the action, and long-running operations give up after 300 seconds.
- **Proposed capability:** Bounded retries on safe 5xx for idempotent calls, one token refresh on 401, configurable LRO timeout.
- **Current-state evidence:** fabric_api.py retry handles 429 only (code reading)
- **Inventory features:** [FEAT-FAB-01](FEATURE_INVENTORY.md#feat-fab-01)
- **Dependencies:** none
- **Owner:** Master product (unassigned)
- **Mandatory (required):** Unsafe or missing retries are a reliability defect on every Fabric call; AGENTS.md asks reviewers to flag unsafe retries.
- **Implementation package:** [pr-ready/PB-051.md](pr-ready/PB-051.md)
- **Acceptance criteria:**
    - GETs retry on 500/502/503/504 with backoff; writes don't retry unless idempotent
    - A 401 refreshes once and retries
    - No write is retried in a way that could duplicate an item (test)

## Enterprise Shared Services

| Id | Item | Priority | Owner | Effort | State |
| --- | --- | --- | --- | --- | --- |
| [PB-052](#pb-052) | Verify the central platform on a tenant | P1 | Master product (unassigned) | M | Proposed |
| [PB-053](#pb-053) | Scheduled shortcut health check with alerts | P2 | Master product (unassigned) | S | Proposed |
| [PB-054](#pb-054) | Screens for platform revoke and source list | P2 | Master product (unassigned) | S | Proposed |

### PB-052

**Verify the central platform on a tenant** · P1 (score 1.8) · M · confidence Medium · Proposed

- **Problem:** SQL_Platform T-SQL runs only on SQLite in tests; whether User Data Functions expose the executing user is unverified; domain import shape is unverified.
- **Proposed capability:** A live check of setup, join, key issue, requests and domain import on a non-production tenant before anyone moves real keys.
- **Current-state evidence:** docs/platform.md lists the unverified points; central/setup.py setup() and join() have no tests
- **Inventory features:** [FEAT-MESH-01](FEATURE_INVENTORY.md#feat-mesh-01), [FEAT-MESH-02](FEATURE_INVENTORY.md#feat-mesh-02), [FEAT-MESH-04](FEATURE_INVENTORY.md#feat-mesh-04)
- **Dependencies:** [PB-002](#pb-002)
- **Owner:** Master product (unassigned)
- **Mandatory (required):** Central business keys are shared data; a defect would corrupt keys across every domain.
- **Acceptance criteria:**
    - Setup and join run on the maintainer's tenant and are recorded
    - Two concurrent key requests never share a key on Fabric SQL database
    - UDF caller identity confirmed or the design changed

### PB-053

**Scheduled shortcut health check with alerts** · P2 (score 2.12) · S · confidence Medium · Proposed

- **Problem:** Broken shortcuts are found only when someone opens the page.
- **Proposed capability:** A scheduled check (app or agent) that posts to Teams once per break.
- **Current-state evidence:** docs/shortcuts.md lists it as not built
- **Inventory features:** [FEAT-ONE-03](FEATURE_INVENTORY.md#feat-one-03)
- **Dependencies:** none
- **Owner:** Master product (unassigned)
- **Acceptance criteria:**
    - The agent runs the check on a schedule
    - A new break posts once

### PB-054

**Screens for platform revoke and source list** · P2 (score 2.07) · S · confidence High · Proposed

- **Problem:** Platform admins can't revoke a grant or list sources from the app though the engine supports both.
- **Proposed capability:** Revoke in Platform > Access and a sources list.
- **Current-state evidence:** platform_revoke and platform_sources have no caller in app/ui
- **Inventory features:** [FEAT-MESH-01](FEATURE_INVENTORY.md#feat-mesh-01)
- **Dependencies:** none
- **Owner:** Master product (unassigned)
- **Acceptance criteria:**
    - An admin revokes a grant with confirmation and it is audited

## Data Governance and Quality

| Id | Item | Priority | Owner | Effort | State |
| --- | --- | --- | --- | --- | --- |
| [PB-056](#pb-056) | Enterprise policy registry | P2 | Enterprise governance foundation | M | In progress |
| [PB-057](#pb-057) | Offline policy validation | P1 | Enterprise governance foundation | M | Proposed |
| [PB-059](#pb-059) | Governance exception requests and approvals | P2 | Enterprise governance foundation | M | Proposed |
| [PB-060](#pb-060) | Sensitive data classification that complements Purview | P2 | Enterprise readiness | L | Proposed |
| [PB-081](#pb-081) | Column lineage view | P1 | Staged roadmap | M | Proposed |
| [PB-082](#pb-082) | Versioned data contracts with consumer declarations | P2 | Staged roadmap | M | Proposed |
| [PB-083](#pb-083) | Endorsed items scope in the catalog | P2 | Master product (unassigned) | S | Proposed |
| [PB-084](#pb-084) | One late-table alert, not two | P2 | Workload management | S | Proposed |

### PB-056

**Enterprise policy registry** · P2 (score 2.38) · M · confidence Medium · In progress

- **Problem:** Policies (naming, schema, quality, classification, ownership) live in project files with no central, versioned source.
- **Proposed capability:** Owned and specified by the Enterprise governance foundation thread (GOV-001).
- **Current-state evidence:** No policy registry in the repo
- **Inventory features:** [FEAT-GOV-05](FEATURE_INVENTORY.md#feat-gov-05)
- **Dependencies:** none
- **Owner:** Enterprise governance foundation (GOV-001)
- **Acceptance criteria:**
    - As defined in GOV-001 by its owning thread

### PB-057

**Offline policy validation** · P1 (score 2.8) · M · confidence Medium · Proposed

- **Problem:** Policy checks need Fabric today or don't exist.
- **Proposed capability:** Owned and specified by the Enterprise governance foundation thread (GOV-002; GOV-003..005 follow there).
- **Current-state evidence:** No offline validator
- **Inventory features:** [FEAT-GOV-05](FEATURE_INVENTORY.md#feat-gov-05)
- **Dependencies:** [PB-056](#pb-056)
- **Owner:** Enterprise governance foundation (GOV-002, GOV-003..005)
- **Acceptance criteria:**
    - As defined in GOV-002 by its owning thread

### PB-059

**Governance exception requests and approvals** · P2 (score 1.96) · M · confidence Medium · Proposed

- **Problem:** Exceptions are lines in a project file with no request, approval or history.
- **Proposed capability:** Request, approve and expire exceptions with history, aligned with the policy registry.
- **Current-state evidence:** meta/project.py exceptions.toml
- **Inventory features:** [FEAT-GOV-03](FEATURE_INVENTORY.md#feat-gov-03)
- **Dependencies:** [PB-056](#pb-056)
- **Owner:** Enterprise governance foundation (FD-129)
- **Acceptance criteria:**
    - As defined in FD-129 by the Enterprise governance foundation thread (moved from Enterprise readiness on 2026-10-17), built on the GOV-001 policy registry

### PB-060

**Sensitive data classification that complements Purview** · P2 (score 1.8) · L · confidence Low · Proposed

- **Problem:** Columns carry no classification; a UDF exists with no caller.
- **Proposed capability:** Classification captured in contracts and carried to products, reading and writing Purview labels rather than replacing them.
- **Current-state evidence:** central/udf.py set_classification has no caller
- **Inventory features:** [FEAT-GOV-06](FEATURE_INVENTORY.md#feat-gov-06)
- **Dependencies:** [PB-056](#pb-056)
- **Owner:** Enterprise readiness (FD-131, GOV-00x)
- **Acceptance criteria:**
    - As defined in FD-131 and the governance foundation thread

### PB-081

**Column lineage view** · P1 (score 3.06) · M · confidence High · Proposed

- **Problem:** Column lineage is computed but no screen shows it.
- **Proposed capability:** A lineage view after a mockup, as the staged roadmap plans.
- **Current-state evidence:** meta_lineage has no caller in app/ui
- **Inventory features:** [FEAT-META-17](FEATURE_INVENTORY.md#feat-meta-17)
- **Dependencies:** none
- **Owner:** Staged roadmap (FD-128)
- **Acceptance criteria:**
    - As defined in FD-128 by its owning program

### PB-082

**Versioned data contracts with consumer declarations** · P2 (score 2.38) · M · confidence Medium · Proposed

- **Problem:** Breaking changes are blocked, but consumers can't declare what they read or see deprecations.
- **Proposed capability:** Consumer declarations and deprecation windows on product ports.
- **Current-state evidence:** meta/mesh.py breaking-change check
- **Inventory features:** [FEAT-MESH-05](FEATURE_INVENTORY.md#feat-mesh-05)
- **Dependencies:** none
- **Owner:** Staged roadmap (FD-127)
- **Acceptance criteria:**
    - As defined in FD-127 by its owning program

### PB-083

**Endorsed items scope in the catalog** · P2 (score 1.9) · S · confidence High · Proposed

- **Problem:** The Endorsed filter is always unavailable and its reason text points at an embedded catalog that doesn't exist.
- **Proposed capability:** Fix the text now; read endorsements when a supported API is confirmed.
- **Current-state evidence:** service.py endorsements returns available:false; docs/troubleshooting.md
- **Inventory features:** [FEAT-ONE-02](FEATURE_INVENTORY.md#feat-one-02)
- **Dependencies:** none
- **Owner:** Master product (unassigned)
- **Acceptance criteria:**
    - Reason text and troubleshooting.md describe what actually happens

### PB-084

**One late-table alert, not two** · P2 (score 1.84) · S · confidence High · Proposed

- **Problem:** Freshness alerts and incidents can both post the same late table to Teams.
- **Proposed capability:** Freshness evaluated as incidents with one routing path.
- **Current-state evidence:** docs/agent.md notes duplicates; meta/freshness.py and workloads/incidents.py
- **Inventory features:** [FEAT-GOV-01](FEATURE_INVENTORY.md#feat-gov-01), [FEAT-OPS-02](FEATURE_INVENTORY.md#feat-ops-02)
- **Dependencies:** none
- **Owner:** Workload management (FD-123)
- **Acceptance criteria:**
    - A late table produces one Teams message

## Developer Experience and Productivity

| Id | Item | Priority | Owner | Effort | State |
| --- | --- | --- | --- | --- | --- |
| [PB-034](#pb-034) | Start the notebook wizard from a table | P2 | Prove | S | Proposed |
| [PB-062](#pb-062) | Save a support bundle from Settings | P2 | Enterprise readiness | S | Proposed |
| [PB-077](#pb-077) | User and agent docs match the code | P2 | Master product (unassigned) | S | Proposed |
| [PB-078](#pb-078) | Preview tags match the docs | P1 | Master product (unassigned) | S | Proposed |
| [PB-085](#pb-085) | Declare cell dependencies and mark stale on new table versions | P2 | Prove | S | Proposed |
| [PB-086](#pb-086) | Ready to work checklist | P1 | Enterprise readiness | S | In review |
| [PB-087](#pb-087) | Clean up downloaded preview files | P2 | Master product (unassigned) | S | Proposed |
| [PB-100](#pb-100) | Fuller What's new dialog | P3 | Fuller What's new dialog | S | In progress |

### PB-034

**Start the notebook wizard from a table** · P2 (score 2.4) · S · confidence High · Proposed

- **Problem:** The wizard can't be launched from a lakehouse table, where most people start.
- **Proposed capability:** Open the wizard pre-filled from a table in the catalog or explorer.
- **Current-state evidence:** wizard.js entry points
- **Inventory features:** [FEAT-NB-10](FEATURE_INVENTORY.md#feat-nb-10)
- **Dependencies:** none
- **Owner:** Prove (Prove plan Stage 3)
- **Acceptance criteria:**
    - Right-click a table > New notebook from this table opens the wizard with it selected

### PB-062

**Save a support bundle from Settings** · P2 (score 2.3) · S · confidence High · Proposed

- **Problem:** The support bundle is CLI only, so most users and help desks can't produce one.
- **Proposed capability:** Settings > Help > Save support bundle with a contents preview, as FD-138 asks.
- **Current-state evidence:** support_bundle.py has no RPC or UI; Smoke: CLI wrote about.json and CONTENTS.txt
- **Inventory features:** [FEAT-SHL-11](FEATURE_INVENTORY.md#feat-shl-11)
- **Dependencies:** none
- **Owner:** Enterprise readiness (FD-138)
- **Acceptance criteria:**
    - The bundle is saved from Settings after showing what it contains
    - Contents match the CLI bundle

### PB-077

**User and agent docs match the code** · P2 (score 2.45) · S · confidence High · Proposed

- **Problem:** Several docs describe behavior the code doesn't have (Report a problem adds the tenant id; workload views read agent status; the mailbox is unbuilt; incidents are a Delta table; %%sql publish).
- **Proposed capability:** Each mismatch fixed in the docs, with a doc-claims checklist added to the review template.
- **Current-state evidence:** See FEATURE_INVENTORY.md limitations for FEAT-SHL-10, FEAT-ORC-04, FEAT-ORC-05, FEAT-OPS-02, FEAT-FAB-06
- **Inventory features:** [FEAT-SHL-10](FEATURE_INVENTORY.md#feat-shl-10), [FEAT-ORC-04](FEATURE_INVENTORY.md#feat-orc-04), [FEAT-ORC-05](FEATURE_INVENTORY.md#feat-orc-05), [FEAT-OPS-02](FEATURE_INVENTORY.md#feat-ops-02), [FEAT-FAB-06](FEATURE_INVENTORY.md#feat-fab-06)
- **Dependencies:** none
- **Owner:** Master product (unassigned)
- **Implementation package:** [pr-ready/PB-077.md](pr-ready/PB-077.md)
- **Acceptance criteria:**
    - Every mismatch listed in the package is corrected
    - zensical build --strict passes

### PB-078

**Preview tags match the docs** · P1 (score 2.45) · S · confidence High · Proposed

- **Problem:** Docs call batch_by, release records, who-can-promote, objectives and column lineage Preview, but the app has no Preview id for them, so they can't be hidden by policy.
- **Proposed capability:** A PREVIEW_FEATURES id for each, or the docs changed where the feature is not exposed in the UI.
- **Current-state evidence:** app/ui/features.js PREVIEW_FEATURES has 19 ids; docs/metadata-guide.md Preview labels
- **Inventory features:** [FEAT-SHL-05](FEATURE_INVENTORY.md#feat-shl-05)
- **Dependencies:** none
- **Owner:** Master product (unassigned)
- **Mandatory (required):** AGENTS.md requires untested Fabric integrations to keep Preview labels; enterprises rely on hiding Preview by policy.
- **Implementation package:** [pr-ready/PB-078.md](pr-ready/PB-078.md)
- **Acceptance criteria:**
    - Every feature the docs call Preview has an id or the docs say why not
    - A UI test lists docs-Preview features against the ids

### PB-085

**Declare cell dependencies and mark stale on new table versions** · P2 (score 2.04) · S · confidence Medium · Proposed

- **Problem:** Declared dependencies are read by the engine but nothing sets them, and new Delta versions don't mark cells stale.
- **Proposed capability:** UI to declare a dependency and staleness driven by Delta versions.
- **Current-state evidence:** depgraph.py depends_on; deps.js
- **Inventory features:** [FEAT-NB-09](FEATURE_INVENTORY.md#feat-nb-09)
- **Dependencies:** none
- **Owner:** Prove
- **Acceptance criteria:**
    - A declared dependency is saved and shown
    - A new version of a read table marks its cells stale

### PB-086

**Ready to work checklist** · P1 (score 2.75) · S · confidence High · In review

- **Problem:** First-run failures (no capacity, no workspace access) appear as errors later.
- **Proposed capability:** Built by the enterprise program as FD-136 (PR #94).
- **Current-state evidence:** PR #94 merged after this assessment's commit
- **Inventory features:** [FEAT-ADM-04](FEATURE_INVENTORY.md#feat-adm-04)
- **Dependencies:** none
- **Owner:** Enterprise readiness (FD-136)
- **Pull requests:** [#94](https://github.com/KratosDataGod/fabric-desktop/pull/94)
- **Acceptance criteria:**
    - Accepted through the implementation review process

### PB-087

**Clean up downloaded preview files** · P2 (score 1.9) · S · confidence Medium · Proposed

- **Problem:** File previews download whole into the app data folder and are never removed (inference).
- **Proposed capability:** Size limit and cleanup on close.
- **Current-state evidence:** preview_service.py (code reading)
- **Inventory features:** [FEAT-FAB-04](FEATURE_INVENTORY.md#feat-fab-04)
- **Dependencies:** none
- **Owner:** Master product (unassigned)
- **Acceptance criteria:**
    - Preview files are removed when the panel closes or on next start
    - Files over a limit preview their head only

### PB-100

**Fuller What's new dialog** · P3 (score 1.65) · S · confidence Medium · In progress

- **Problem:** The What's new dialog shows only the current build, so people who skip updates miss what changed.
- **Proposed capability:** Owned and specified by the Fuller What's new dialog thread: list the last 30 days of builds, show the 10 newest, and link to the repository. Not restated here.
- **Current-state evidence:** app/ui/settings.js showWhatsNew (What's new menu item and Settings link)
- **Inventory features:** [FEAT-SHL-09](FEATURE_INVENTORY.md#feat-shl-09)
- **Dependencies:** none
- **Owner:** Fuller What's new dialog
- **Acceptance criteria:**
    - As defined by its owning thread

## Enterprise Administration and FinOps

| Id | Item | Priority | Owner | Effort | State |
| --- | --- | --- | --- | --- | --- |
| [PB-017](#pb-017) | Local vs Fabric execution advisor | P1 | Administration and FinOps sprint | M | Proposed |
| [PB-047](#pb-047) | Incident screens | P2 | Workload management | M | Proposed |
| [PB-050](#pb-050) | Label every CU number measured, estimated, scenario or unknown | P1 | Administration and FinOps sprint | M | In progress |
| [PB-088](#pb-088) | Capacity inventory and health | P2 | Administration and FinOps sprint | M | Proposed |
| [PB-089](#pb-089) | Measured CU import and reconciliation | P2 | Administration and FinOps sprint | L | Proposed |
| [PB-090](#pb-090) | Preflight risk analyzer | P1 | Administration and FinOps sprint | M | Proposed |
| [PB-091](#pb-091) | Agent dashboard | P2 | Workload management | M | Proposed |
| [PB-092](#pb-092) | Shared workload ownership | P3 | Workload management | M | Proposed |
| [PB-093](#pb-093) | Telemetry decision | P3 | Enterprise readiness | M | Proposed |
| [PB-094](#pb-094) | Measure the process kernel, not the engine | P2 | Prove | S | Proposed |

### PB-017

**Local vs Fabric execution advisor** · P1 (score 3.15) · M · confidence Medium · Proposed

- **Problem:** Choosing This PC or Fabric is a manual guess.
- **Proposed capability:** Owned and specified by the Administration and FinOps sprint (FINOPS-005), using the compatibility analyzer and measured costs.
- **Current-state evidence:** Runs on is a saved choice; no recommendation
- **Inventory features:** [FEAT-NB-07](FEATURE_INVENTORY.md#feat-nb-07), [FEAT-META-14](FEATURE_INVENTORY.md#feat-meta-14)
- **Dependencies:** [PB-050](#pb-050), [PB-014](#pb-014)
- **Owner:** Administration and FinOps sprint (FINOPS-005)
- **Acceptance criteria:**
    - As defined in FINOPS-005 by its owning thread

### PB-047

**Incident screens** · P2 (score 2.27) · M · confidence High · Proposed

- **Problem:** The incident engine runs only in the agent; the app has six incident methods and no screen.
- **Proposed capability:** Incident list and detail with acknowledge, assign, resolve, after a mockup.
- **Current-state evidence:** incident_service.py RPCs have no caller in app/ui
- **Inventory features:** [FEAT-OPS-02](FEATURE_INVENTORY.md#feat-ops-02), [FEAT-GOV-07](FEATURE_INVENTORY.md#feat-gov-07), [FEAT-SHL-06](FEATURE_INVENTORY.md#feat-shl-06)
- **Dependencies:** none
- **Owner:** Workload management (FD-123, Workloads stage 2.3B)
- **Acceptance criteria:**
    - As defined in FD-123 by its owning program

### PB-050

**Label every CU number measured, estimated, scenario or unknown** · P1 (score 3.48) · M · confidence High · In progress

- **Problem:** Cost panel figures are estimates from fixed defaults and aren't calibrated against the Capacity Metrics app.
- **Proposed capability:** Owned and specified by the Administration and FinOps sprint (FINOPS-001).
- **Current-state evidence:** costs.py defaults: $0.18/CU-hour, Python 1 CU, Spark 8 CU
- **Inventory features:** [FEAT-ADM-01](FEATURE_INVENTORY.md#feat-adm-01), [FEAT-PRV-05](FEATURE_INVENTORY.md#feat-prv-05)
- **Dependencies:** none
- **Owner:** Administration and FinOps sprint (FINOPS-001)
- **Merged duplicates:** Calibrate cost estimates against the Capacity Metrics app (feature inventory, FEAT-ADM-01)
- **Acceptance criteria:**
    - As defined in FINOPS-001 by its owning thread

### PB-088

**Capacity inventory and health** · P2 (score 2.52) · M · confidence Medium · Proposed

- **Problem:** Admins can't see their capacities' size, state and load from the app.
- **Proposed capability:** Owned and specified by the Administration and FinOps sprint (FINOPS-002).
- **Current-state evidence:** fabric_api.py lists capacities only
- **Dependencies:** [PB-050](#pb-050)
- **Owner:** Administration and FinOps sprint (FINOPS-002)
- **Acceptance criteria:**
    - As defined in FINOPS-002 by its owning thread

### PB-089

**Measured CU import and reconciliation** · P2 (score 2.05) · L · confidence Low · Proposed

- **Problem:** Estimates are never reconciled with what Fabric actually charged.
- **Proposed capability:** Owned and specified by the Administration and FinOps sprint (FINOPS-003).
- **Current-state evidence:** No Capacity Metrics import
- **Dependencies:** [PB-050](#pb-050)
- **Owner:** Administration and FinOps sprint (FINOPS-003)
- **Acceptance criteria:**
    - As defined in FINOPS-003 by its owning thread

### PB-090

**Preflight risk analyzer** · P1 (score 3.04) · M · confidence Medium · Proposed

- **Problem:** People learn a run was expensive or too big after it runs.
- **Proposed capability:** Owned and specified by the Administration and FinOps sprint (FINOPS-004).
- **Current-state evidence:** meta/preflight.py estimates memory only
- **Inventory features:** [FEAT-META-06](FEATURE_INVENTORY.md#feat-meta-06)
- **Dependencies:** [PB-050](#pb-050)
- **Owner:** Administration and FinOps sprint (FINOPS-004)
- **Acceptance criteria:**
    - As defined in FINOPS-004 by its owning thread

### PB-091

**Agent dashboard** · P2 (score 2.4) · M · confidence High · Proposed

- **Problem:** Agent status records are written but nothing shows them.
- **Proposed capability:** Agent list, queue and availability view.
- **Current-state evidence:** agent_status.py; workloads/agents.py read only by incidents
- **Inventory features:** [FEAT-ORC-05](FEATURE_INVENTORY.md#feat-orc-05)
- **Dependencies:** none
- **Owner:** Workload management (FD-122)
- **Acceptance criteria:**
    - As defined in FD-122 by its owning program

### PB-092

**Shared workload ownership** · P3 (score 1.72) · M · confidence Medium · Proposed

- **Problem:** Workload owners are stored per PC, so teams see different owners.
- **Proposed capability:** Ownership stored centrally, as the workloads plan stage 2B defines.
- **Current-state evidence:** workloads/store.py local
- **Inventory features:** [FEAT-OPS-01](FEATURE_INVENTORY.md#feat-ops-01)
- **Dependencies:** none
- **Owner:** Workload management (Workloads stage 2B)
- **Acceptance criteria:**
    - As defined in Workloads stage 2B

### PB-093

**Telemetry decision** · P3 (score 1.15) · M · confidence Low · Proposed

- **Problem:** There is no usage data, and whether to collect any is undecided.
- **Proposed capability:** Decision D-C1, then build only what it allows.
- **Current-state evidence:** No telemetry code
- **Inventory features:** [FEAT-ADM-06](FEATURE_INVENTORY.md#feat-adm-06)
- **Dependencies:** Product-owner decision D-C1
- **Owner:** Enterprise readiness (FD-137)
- **Acceptance criteria:**
    - D-C1 recorded
    - Nothing is collected without consent and a policy switch

### PB-094

**Measure the process kernel, not the engine** · P2 (score 2.48) · S · confidence Medium · Proposed

- **Problem:** With the process kernel on, CPU and memory are measured from the engine process (inference), so scorecards can be wrong.
- **Proposed capability:** Measure the child kernel's resources.
- **Current-state evidence:** measure.py RunMeter
- **Inventory features:** [FEAT-PRV-05](FEATURE_INVENTORY.md#feat-prv-05)
- **Dependencies:** none
- **Owner:** Prove (FD-119)
- **Acceptance criteria:**
    - Scorecard resources match the kernel process in a test

## Extensibility, APIs, CLI, and MCP

| Id | Item | Priority | Owner | Effort | State |
| --- | --- | --- | --- | --- | --- |
| [PB-095](#pb-095) | MCP server and local API for external AI tools | P2 | Bring your own AI integration | XL | In review |
| [PB-096](#pb-096) | Run the AI client checklist for each named tool | P1 | Bring your own AI integration | S | Proposed |
| [PB-097](#pb-097) | Extensions | P3 | Git-native development | XL | Proposed |
| [PB-098](#pb-098) | One list of engine methods | P1 | Bring your own AI integration | S | Proposed |

### PB-095

**MCP server and local API for external AI tools** · P2 (score 1.95) · XL · confidence Medium · In review

- **Problem:** External AI tools can't see or drive the app's capabilities.
- **Proposed capability:** The bring-your-own-AI stack PR-AI-001..012, built and specified by its program.
- **Current-state evidence:** PRs #99-#108, #110, #112 open; #98 plan merged
- **Inventory features:** [FEAT-EXT-02](FEATURE_INVENTORY.md#feat-ext-02)
- **Dependencies:** none
- **Owner:** Bring your own AI integration (PR-AI-001..012)
- **Pull requests:** [#99](https://github.com/KratosDataGod/fabric-desktop/pull/99), [#100](https://github.com/KratosDataGod/fabric-desktop/pull/100), [#101](https://github.com/KratosDataGod/fabric-desktop/pull/101), [#102](https://github.com/KratosDataGod/fabric-desktop/pull/102), [#103](https://github.com/KratosDataGod/fabric-desktop/pull/103), [#104](https://github.com/KratosDataGod/fabric-desktop/pull/104), [#105](https://github.com/KratosDataGod/fabric-desktop/pull/105), [#106](https://github.com/KratosDataGod/fabric-desktop/pull/106), [#107](https://github.com/KratosDataGod/fabric-desktop/pull/107), [#108](https://github.com/KratosDataGod/fabric-desktop/pull/108), [#110](https://github.com/KratosDataGod/fabric-desktop/pull/110), [#112](https://github.com/KratosDataGod/fabric-desktop/pull/112)
- **Acceptance criteria:**
    - As defined in PR-AI-001..012
    - Each PR passes the implementation review

### PB-096

**Run the AI client checklist for each named tool** · P1 (score 2.75) · S · confidence High · Proposed

- **Problem:** Only the MCP SDK client and plain HTTP were tested; named AI tools are marked Untested.
- **Proposed capability:** Each client run through docs/product/ai-integration-client-checklist.md and recorded.
- **Current-state evidence:** AI integration thread notes
- **Inventory features:** [FEAT-EXT-02](FEATURE_INVENTORY.md#feat-ext-02)
- **Dependencies:** [PB-095](#pb-095)
- **Owner:** Bring your own AI integration (PR-AI-011)
- **Acceptance criteria:**
    - Each named client has a recorded result or stays Untested

### PB-097

**Extensions** · P3 (score 1.2) · XL · confidence Low · Proposed

- **Problem:** Organizations can't add validators, templates or commands.
- **Proposed capability:** Owned and specified by the Git-native program (FD-132..135).
- **Current-state evidence:** docs/git-integration-plan.md Phase 4
- **Inventory features:** [FEAT-EXT-03](FEATURE_INVENTORY.md#feat-ext-03)
- **Dependencies:** none
- **Owner:** Git-native development (FD-132..135, Git phase 4)
- **Acceptance criteria:**
    - As defined in FD-132..135

### PB-098

**One list of engine methods** · P1 (score 2.6) · S · confidence Medium · Proposed

- **Problem:** server.py METHODS and the capability registry can drift apart.
- **Proposed capability:** METHODS generated from, or checked against, the capability registry.
- **Current-state evidence:** server.py METHODS
- **Inventory features:** [FEAT-SHL-02](FEATURE_INVENTORY.md#feat-shl-02)
- **Dependencies:** [PB-095](#pb-095)
- **Owner:** Bring your own AI integration (PR-AI-003)
- **Acceptance criteria:**
    - A test fails when a method is in one list and not the other

## Security and Enterprise Readiness

| Id | Item | Priority | Owner | Effort | State |
| --- | --- | --- | --- | --- | --- |
| [PB-001](#pb-001) | Sign the app and engine | P0 | Enterprise readiness | S | Proposed |
| [PB-005](#pb-005) | Encrypt the token cache | P1 | Enterprise readiness | S | Proposed |
| [PB-006](#pb-006) | Content Security Policy for the main window | P1 | Enterprise readiness | M | Proposed |
| [PB-007](#pb-007) | Audit trail of runs and changes | P1 | Enterprise readiness | M | In review |
| [PB-008](#pb-008) | Keep the embedded Fabric view on Fabric hosts | P1 | Enterprise readiness | S | Proposed |
| [PB-009](#pb-009) | Local data retention | P1 | Enterprise readiness | S | In review |
| [PB-010](#pb-010) | Security and network docs match the shipped app | P1 | Master product (unassigned) | S | Proposed |
| [PB-061](#pb-061) | Missing policy keys | P2 | Enterprise readiness | M | Proposed |
| [PB-063](#pb-063) | Assistive technology audit | P1 | Enterprise readiness | M | Proposed |
| [PB-065](#pb-065) | Separation of duties by signed-in identity | P2 | Enterprise readiness | S | Proposed |

### PB-001

**Sign the app and engine** · P0 (score 2.48) · S · confidence High · Proposed

- **Problem:** Unsigned binaries are blocked by Smart App Control and WDAC on many PCs, so the engine doesn't start.
- **Proposed capability:** Azure Trusted Signing in the release workflow for the exe and engine binaries.
- **Current-state evidence:** ci.yml signs only when Trusted Signing secrets exist; PR #28 blocked
- **Inventory features:** [FEAT-SEC-01](FEATURE_INVENTORY.md#feat-sec-01), [FEAT-SHL-09](FEATURE_INVENTORY.md#feat-shl-09)
- **Dependencies:** Chris creates the Azure Trusted Signing account (D-C3)
- **Owner:** Enterprise readiness (FD-103, FD-104)
- **Mandatory (blocker):** Without signing the app does not run on many managed PCs; the launch audience is about 5,000 people.
- **Pull requests:** [#28](https://github.com/KratosDataGod/fabric-desktop/pull/28)
- **Acceptance criteria:**
    - Released exe and engine DLLs carry a valid signature
    - A Smart App Control PC starts the engine

### PB-005

**Encrypt the token cache** · P1 (score 1.96) · S · confidence High · Proposed

- **Problem:** The MSAL token cache is a plain file on Windows.
- **Proposed capability:** DPAPI-protected cache through msal-extensions.
- **Current-state evidence:** auth.py writes a plain cache file
- **Inventory features:** [FEAT-AUTH-03](FEATURE_INVENTORY.md#feat-auth-03)
- **Dependencies:** Decision D-C2
- **Owner:** Enterprise readiness (FD-131b)
- **Mandatory (required):** Refresh tokens on disk unencrypted are a security finding any enterprise review will raise.
- **Acceptance criteria:**
    - The cache is unreadable to another Windows user
    - Scheduled runs and the app share it safely

### PB-006

**Content Security Policy for the main window** · P1 (score 1.47) · M · confidence Medium · Proposed

- **Problem:** The main webview runs with csp: null and the Tauri global, so escaping is the only defense against injected HTML.
- **Proposed capability:** Move notebook HTML output to another origin, then set a CSP.
- **Current-state evidence:** tauri.conf.json csp: null, withGlobalTauri: true
- **Inventory features:** [FEAT-SEC-02](FEATURE_INVENTORY.md#feat-sec-02)
- **Dependencies:** none
- **Owner:** Enterprise readiness (FD-102b)
- **Mandatory (required):** Defense in depth for a webview that renders user and notebook content.
- **Acceptance criteria:**
    - A CSP is set and the UI test suite passes
    - Notebook HTML output can't call Tauri commands

### PB-007

**Audit trail of runs and changes** · P1 (score 2.08) · M · confidence High · In review

- **Problem:** Nothing records who ran or changed what in a tamper-evident way.
- **Proposed capability:** Hash-chained audit lines, built in PR #89.
- **Current-state evidence:** PR #89 merged after this assessment's commit; awaiting implementation review
- **Inventory features:** [FEAT-ADM-02](FEATURE_INVENTORY.md#feat-adm-02), [FEAT-PRV-03](FEATURE_INVENTORY.md#feat-prv-03)
- **Dependencies:** none
- **Owner:** Enterprise readiness (FD-125)
- **Mandatory (required):** Enterprise compliance needs an audit record of changes to shared data.
- **Pull requests:** [#89](https://github.com/KratosDataGod/fabric-desktop/pull/89)
- **Acceptance criteria:**
    - As defined in FD-125
    - Accepted through the implementation review

### PB-008

**Keep the embedded Fabric view on Fabric hosts** · P1 (score 1.84) · S · confidence Medium · Proposed

- **Problem:** The embedded view's allow-list accepts any *.microsoft.com and *.live.com page.
- **Proposed capability:** Navigation limited to Fabric, Power BI and sign-in hosts; others open in the browser.
- **Current-state evidence:** main.rs fabric_view host check (code reading)
- **Inventory features:** [FEAT-FAB-05](FEATURE_INVENTORY.md#feat-fab-05)
- **Dependencies:** [PB-071](#pb-071)
- **Owner:** Enterprise readiness (FD-102)
- **Mandatory (required):** The embedded view holds saved Fabric sign-in cookies.
- **Acceptance criteria:**
    - A link to another Microsoft site opens outside the app
    - Covered by a Rust test run in CI

### PB-009

**Local data retention** · P1 (score 1.72) · S · confidence High · In review

- **Problem:** Copies of lakehouse data stay on PCs indefinitely.
- **Proposed capability:** Built in PR #95 (FD-131b).
- **Current-state evidence:** PR #95 merged after this assessment's commit; awaiting implementation review
- **Inventory features:** [FEAT-ADM-05](FEATURE_INVENTORY.md#feat-adm-05)
- **Dependencies:** none
- **Owner:** Enterprise readiness (FD-131b)
- **Mandatory (required):** Sensitive data protection policy needs bounded local copies.
- **Pull requests:** [#95](https://github.com/KratosDataGod/fabric-desktop/pull/95)
- **Acceptance criteria:**
    - As defined in FD-131b
    - A dry run deletes nothing

### PB-010

**Security and network docs match the shipped app** · P1 (score 2.2) · S · confidence High · Proposed

- **Problem:** docs/security.md says there is no redaction, scanning, SBOM or provenance (all exist); enterprise-install.md's host list omits hosts the app calls and misattributes one.
- **Proposed capability:** Corrected security and network documentation that IT can rely on.
- **Current-state evidence:** docs/security.md:41; ci.yml audit and SBOM jobs; docs/enterprise-install.md hosts table
- **Inventory features:** [FEAT-SEC-03](FEATURE_INVENTORY.md#feat-sec-03), [FEAT-SHL-02](FEATURE_INVENTORY.md#feat-shl-02)
- **Dependencies:** none
- **Owner:** Master product (unassigned)
- **Mandatory (required):** Security reviewers and firewall teams act on these documents; wrong docs block or mislead deployment.
- **Implementation package:** [pr-ready/PB-010.md](pr-ready/PB-010.md)
- **Acceptance criteria:**
    - Every claim in security.md matches the code at the time of the PR
    - The hosts list matches every host in fabric_api.py, auth.py, updater.py and main.rs

### PB-061

**Missing policy keys** · P2 (score 1.96) · M · confidence High · Proposed

- **Problem:** Docs describe policy keys (allowed hosts, retention days, telemetry, three-value allowed compute) the app doesn't read.
- **Proposed capability:** Implement or remove each documented key.
- **Current-state evidence:** app_settings.py key list; docs/enterprise-install.md
- **Inventory features:** [FEAT-SHL-08](FEATURE_INVENTORY.md#feat-shl-08)
- **Dependencies:** none
- **Owner:** Enterprise readiness (FD-105)
- **Acceptance criteria:**
    - As defined in FD-105

### PB-063

**Assistive technology audit** · P1 (score 2.21) · M · confidence Medium · Proposed

- **Problem:** Accessibility is checked statically only; no screen-reader or contrast audit is recorded.
- **Proposed capability:** A Narrator and keyboard audit on Windows with fixes.
- **Current-state evidence:** app/tests/ui.test.cjs static checks
- **Inventory features:** [FEAT-SHL-07](FEATURE_INVENTORY.md#feat-shl-07), [FEAT-SHL-04](FEATURE_INVENTORY.md#feat-shl-04)
- **Dependencies:** none
- **Owner:** Enterprise readiness (FD-139)
- **Mandatory (required):** Enterprise procurement often requires an accessibility conformance statement.
- **Acceptance criteria:**
    - Audit results recorded per page
    - Blocking findings fixed

### PB-065

**Separation of duties by signed-in identity** · P2 (score 2.05) · S · confidence High · Proposed

- **Problem:** separate_promoter compares PC login names, not signed-in identities.
- **Proposed capability:** Compare Entra object ids.
- **Current-state evidence:** duties.py
- **Inventory features:** [FEAT-GOV-04](FEATURE_INVENTORY.md#feat-gov-04)
- **Dependencies:** none
- **Owner:** Enterprise readiness (FD-130)
- **Acceptance criteria:**
    - The same Entra user on two PCs is recognized as one person

## Reliability, Testing, and Performance

| Id | Item | Priority | Owner | Effort | State |
| --- | --- | --- | --- | --- | --- |
| [PB-002](#pb-002) | Tenant smoke test of ten core flows | P0 | Master product (unassigned) | M | Proposed |
| [PB-003](#pb-003) | Prove OneLake leases and MERGE writes live | P1 | Enterprise readiness | S | Proposed |
| [PB-064](#pb-064) | Per-build release assets, pinning and rollback | P1 | Enterprise readiness | M | Proposed |
| [PB-066](#pb-066) | Backup and restore of local state | P2 | Enterprise readiness | S | In review |
| [PB-069](#pb-069) | Compare large tables in batches | P2 | Staged roadmap | M | Proposed |
| [PB-070](#pb-070) | UI tests for the notebook editor and command bar | P2 | Master product (unassigned) | M | Proposed |
| [PB-071](#pb-071) | Run the Rust tests in CI | P1 | Master product (unassigned) | S | Proposed |
| [PB-072](#pb-072) | Long engine calls stop blocking the app | P1 | Master product (unassigned) | L | Proposed |
| [PB-073](#pb-073) | Process kernel by default with limits | P2 | Prove | M | Proposed |
| [PB-074](#pb-074) | Release pipeline hardening | P1 | Enterprise readiness | S | Proposed |
| [PB-076](#pb-076) | Retire side by side into Prove | P2 | Prove | S | Proposed |

### PB-002

**Tenant smoke test of ten core flows** · P0 (score 2.76) · M · confidence High · Proposed

- **Problem:** Almost every Fabric-facing feature has only been tested with Fabric faked; the live test harness has never run.
- **Proposed capability:** A scripted ten-flow smoke test on the maintainer's non-production workspace, plus one live-test harness run, with results recorded in the inventory.
- **Current-state evidence:** livetest.py never run; docs/README.md lists features tried on the maintainer's tenant
- **Inventory features:** [FEAT-SHL-01](FEATURE_INVENTORY.md#feat-shl-01), [FEAT-AUTH-01](FEATURE_INVENTORY.md#feat-auth-01), [FEAT-AUTH-04](FEATURE_INVENTORY.md#feat-auth-04), [FEAT-FAB-01](FEATURE_INVENTORY.md#feat-fab-01), [FEAT-FAB-06](FEATURE_INVENTORY.md#feat-fab-06), [FEAT-FAB-07](FEATURE_INVENTORY.md#feat-fab-07), [FEAT-ONE-05](FEATURE_INVENTORY.md#feat-one-05), [FEAT-META-14](FEATURE_INVENTORY.md#feat-meta-14), [FEAT-OPS-04](FEATURE_INVENTORY.md#feat-ops-04), [FEAT-PRV-01](FEATURE_INVENTORY.md#feat-prv-01)
- **Dependencies:** Chris runs it on his non-production workspace
- **Owner:** Master product (unassigned) (FD-101, FD-140)
- **Mandatory (blocker):** Launch to about 5,000 people needs evidence the core flows work on a real tenant.
- **Merged duplicates:** Ten-flow smoke test (honest review, 2026-10-16); Running the FD-101 harness once (enterprise readiness)
- **Implementation package:** [pr-ready/PB-002.md](pr-ready/PB-002.md)
- **Acceptance criteria:**
    - Ten flows pass or have recorded defects
    - The inventory's verification moves only for flows that passed
    - No production workspace is touched

### PB-003

**Prove OneLake leases and MERGE writes live** · P1 (score 2.08) · S · confidence Medium · Proposed

- **Problem:** Leases depend on OneLake honoring conditional requests, which has never been tried.
- **Proposed capability:** The live test extended to leases, two writers and MERGE.
- **Current-state evidence:** leases.py; livetest.py lacks lease and MERGE checks
- **Inventory features:** [FEAT-ORC-03](FEATURE_INVENTORY.md#feat-orc-03), [FEAT-ONE-05](FEATURE_INVENTORY.md#feat-one-05)
- **Dependencies:** [PB-002](#pb-002)
- **Owner:** Enterprise readiness (FD-101)
- **Mandatory (required):** If OneLake ignores conditional requests, concurrent writers can overwrite data.
- **Acceptance criteria:**
    - A second writer is refused while a lease is held on OneLake
    - A MERGE write on OneLake passes the profile check

### PB-064

**Per-build release assets, pinning and rollback** · P1 (score 1.72) · M · confidence Medium · Proposed

- **Problem:** Only the newest build per channel exists, so IT can't pin or roll back.
- **Proposed capability:** Keep each build's assets and let policy pin a version.
- **Current-state evidence:** ci.yml release job replaces assets; promote.yml
- **Inventory features:** [FEAT-REL-01](FEATURE_INVENTORY.md#feat-rel-01), [FEAT-SHL-09](FEATURE_INVENTORY.md#feat-shl-09)
- **Dependencies:** none
- **Owner:** Enterprise readiness (FD-104)
- **Mandatory (required):** Enterprise update control needs rollback.
- **Acceptance criteria:**
    - A previous build can be installed and pinned by policy
    - Promote to stable names the build it promotes

### PB-066

**Backup and restore of local state** · P2 (score 1.84) · S · confidence High · In review

- **Problem:** Settings, schedules, queue and receipts can't be backed up.
- **Proposed capability:** Built in PR #91 (FD-126).
- **Current-state evidence:** PR #91 merged after this assessment's commit; awaiting implementation review
- **Inventory features:** [FEAT-ADM-03](FEATURE_INVENTORY.md#feat-adm-03)
- **Dependencies:** none
- **Owner:** Enterprise readiness (FD-126)
- **Pull requests:** [#91](https://github.com/KratosDataGod/fabric-desktop/pull/91)
- **Acceptance criteria:**
    - As defined in FD-126

### PB-069

**Compare large tables in batches** · P2 (score 1.99) · M · confidence Medium · Proposed

- **Problem:** Environment compare reads whole tables.
- **Proposed capability:** Batched or sampled compare.
- **Current-state evidence:** meta/compare.py
- **Inventory features:** [FEAT-META-08](FEATURE_INVENTORY.md#feat-meta-08)
- **Dependencies:** none
- **Owner:** Staged roadmap (FD-114)
- **Acceptance criteria:**
    - Compare of a table larger than memory finishes

### PB-070

**UI tests for the notebook editor and command bar** · P2 (score 2.07) · M · confidence High · Proposed

- **Problem:** UI tests check parsing, names and a few behaviors; the editor and keyboard flows have none.
- **Proposed capability:** Behavior tests for the editor, command mode and command bar.
- **Current-state evidence:** app/tests/ui.test.cjs has 23 tests
- **Inventory features:** [FEAT-NB-01](FEATURE_INVENTORY.md#feat-nb-01), [FEAT-SHL-04](FEATURE_INVENTORY.md#feat-shl-04)
- **Dependencies:** none
- **Owner:** Master product (unassigned)
- **Acceptance criteria:**
    - Editor, command mode and Ctrl+K have tests that fail on regressions

### PB-071

**Run the Rust tests in CI** · P1 (score 2.45) · S · confidence High · Proposed

- **Problem:** The Rust shell has tests (watchdog, sign-in, integrity) that CI never runs.
- **Proposed capability:** A Windows cargo test job.
- **Current-state evidence:** ci.yml has cargo audit and cargo build, no cargo test; cargo test failed to build on Linux (gdk-3.0)
- **Inventory features:** [FEAT-SHL-03](FEATURE_INVENTORY.md#feat-shl-03), [FEAT-SHL-09](FEATURE_INVENTORY.md#feat-shl-09), [FEAT-FAB-05](FEATURE_INVENTORY.md#feat-fab-05), [FEAT-REL-01](FEATURE_INVENTORY.md#feat-rel-01)
- **Dependencies:** none
- **Owner:** Master product (unassigned)
- **Mandatory (required):** The watchdog, sign-in and update integrity code is security- and reliability-critical and untested in CI.
- **Implementation package:** [pr-ready/PB-071.md](pr-ready/PB-071.md)
- **Acceptance criteria:**
    - cargo test runs on windows-latest on every PR
    - Existing Rust tests pass or failures are fixed

### PB-072

**Long engine calls stop blocking the app** · P1 (score 1.48) · L · confidence Medium · Proposed

- **Problem:** All engine calls share one pipe behind a mutex; anything off the watchdog's quick list has no time limit, so a slow Fabric call freezes every page.
- **Proposed capability:** Concurrent or cancellable long calls with time limits.
- **Current-state evidence:** main.rs rpc mutex; watchdog.rs QUICK list
- **Inventory features:** [FEAT-SHL-01](FEATURE_INVENTORY.md#feat-shl-01), [FEAT-SHL-03](FEATURE_INVENTORY.md#feat-shl-03)
- **Dependencies:** [PB-071](#pb-071)
- **Owner:** Master product (unassigned)
- **Mandatory (required):** A frozen app with no way out is the most visible reliability failure.
- **Acceptance criteria:**
    - A slow Fabric call doesn't block a quick call (test)
    - Every call has a limit

### PB-073

**Process kernel by default with limits** · P2 (score 1.86) · M · confidence Medium · Proposed

- **Problem:** The in-engine kernel restarts the whole engine on Stop; the process kernel is opt-in with no limits.
- **Proposed capability:** Process kernel default after a Windows trial, with time and memory limits.
- **Current-state evidence:** kernel_proc.py; Settings > Preview
- **Inventory features:** [FEAT-NB-03](FEATURE_INVENTORY.md#feat-nb-03)
- **Dependencies:** none
- **Owner:** Prove (FD-110)
- **Acceptance criteria:**
    - As defined in FD-110

### PB-074

**Release pipeline hardening** · P1 (score 2.2) · S · confidence High · Proposed

- **Problem:** Release doesn't wait for the audit job; docs.yml and codex-review.yml use tag-pinned actions; zensical installs unpinned.
- **Proposed capability:** Release depends on audit; all actions SHA-pinned; tool versions pinned.
- **Current-state evidence:** ci.yml; docs.yml; codex-review.yml
- **Inventory features:** [FEAT-SEC-03](FEATURE_INVENTORY.md#feat-sec-03)
- **Dependencies:** none
- **Owner:** Enterprise readiness (FD-103)
- **Mandatory (required):** Supply-chain controls are only as strong as the weakest workflow.
- **Acceptance criteria:**
    - Release needs audit
    - No tag-pinned action remains

### PB-076

**Retire side by side into Prove** · P2 (score 1.8) · S · confidence Medium · Proposed

- **Problem:** Side by side writes real tables twice; Prove does the same check in sandboxes.
- **Proposed capability:** Keep the benchmark, retire the correctness path.
- **Current-state evidence:** side_by_side.py; PR #114 open
- **Inventory features:** [FEAT-PRV-06](FEATURE_INVENTORY.md#feat-prv-06)
- **Dependencies:** none
- **Owner:** Prove (FD-112)
- **Acceptance criteria:**
    - As defined in FD-112
<!-- END GENERATED: items -->
