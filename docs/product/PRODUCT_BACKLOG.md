# Product backlog

One backlog for KratosForge, in twelve categories. It consolidates the gaps found in
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
| Codebase audit and hardening | HD-001..057 | Its project thread (docs/hardening/backlog.md); tracked here as PB-101, which now also owns PB-010, PB-051 and PB-071, with overlaps noted on the items listed in PB-101 |
| Release management | none yet | Its project thread (docs/release-management/): release engineering and integration order; ids recorded when it sends them |
| UX and simplicity oversight | UX-001..021 | Its project thread ([docs/ux/backlog.md](https://github.com/KratosDataGod/KratosForge/pull/166)); tracked here as PB-102. Routed to other owners: UX-010 and UX-014 Workload management; UX-003, UX-005 and UX-012 Enterprise readiness; UX-004 Local data engine; UX-009 Staged roadmap (keys); UX-002 and UX-013 Codebase audit and hardening; UX-007 Release management. UX-021 (Preview tags on untested Fabric writes, absorbs QA-020): Prove for Publish and Run all in Fabric, Staged roadmap for Promote, Sync keys, variable library and quarantine replay, Codebase audit and hardening for Create workspace; UX-018 (absorbs QA-019) stays with this thread |
| Engineering quality review | QA-001..030, PR-QA-01..14 | Its project thread ([docs/quality/](https://github.com/KratosDataGod/KratosForge/pull/176)): the independent code-quality gate that gives the release manager PASS, CONDITIONAL PASS or FAIL; tracked here as PB-103, a blocker because QA-001 and QA-002 lose data. Routed to other owners: QA-001, QA-002 and QA-009 Prove; QA-003, QA-004, QA-006, QA-007 and QA-010 Codebase audit and hardening; QA-005 Enterprise governance; QA-008 wording FinOps first (PR #174), then Prove, Local data engine, governance and Codebase audit and hardening for their own files, and UX and simplicity oversight for the README and top-level docs; QA-012 and QA-016 Enterprise readiness; QA-015 Release management; QA-019 folded into UX-018 and QA-020 into UX-021 |
| Optimize central lakehouse | PR-OPT-001..009 | Its project thread, from Chris's Optimize brief of 2026-10-18: a shared optimization lakehouse (fdopt_ tables), local execution and optimization savings tracking, reporting, offline sync, multiple lakehouses, security and its own cost accounting; tracked here as PB-104. Works with the Administration and FinOps sprint on CU labels (PB-050) and measured CU import (PB-089). Its Cost and savings tab replaces Monitor > Cost (unit testing item 36) |
| Simplified incident management | none yet | Its project thread, from Chris's unit testing on 2026-10-18 (item 33): detect, triage and hand off incidents, stored in a central lakehouse or handed to ServiceNow or Jira; tracked here as PB-105. Builds on the Workload management thread's incidents (PB-047) |
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
| [Local Development and Execution](#local-development-and-execution) | 1 | 3 | 2 | 1 | 7 |
| [SQL, Python, and Notebook Development](#sql-python-and-notebook-development) | 0 | 1 | 6 | 5 | 12 |
| [Data Engineering and Transformation](#data-engineering-and-transformation) | 0 | 0 | 5 | 0 | 5 |
| [Pipelines and Orchestration](#pipelines-and-orchestration) | 0 | 1 | 4 | 4 | 9 |
| [Fabric Integration and Deployment](#fabric-integration-and-deployment) | 0 | 1 | 6 | 2 | 9 |
| [Enterprise Shared Services](#enterprise-shared-services) | 0 | 1 | 2 | 1 | 4 |
| [Data Governance and Quality](#data-governance-and-quality) | 0 | 3 | 9 | 0 | 12 |
| [Developer Experience and Productivity](#developer-experience-and-productivity) | 0 | 3 | 7 | 1 | 11 |
| [Enterprise Administration and FinOps](#enterprise-administration-and-finops) | 0 | 4 | 5 | 2 | 11 |
| [Extensibility, APIs, CLI, and MCP](#extensibility-apis-cli-and-mcp) | 0 | 3 | 2 | 1 | 6 |
| [Security and Enterprise Readiness](#security-and-enterprise-readiness) | 2 | 7 | 2 | 0 | 11 |
| [Reliability, Testing, and Performance](#reliability-testing-and-performance) | 2 | 5 | 5 | 0 | 12 |
| **Total** | **5** | **32** | **55** | **17** | **109** |
<!-- END GENERATED: summary -->

States: Proposed (not yet approved), Approved (the product owner said go), In progress, In review
(a PR exists), Accepted (passed the [implementation review](IMPLEMENTATION_REVIEW_PROCESS.md)),
Rejected, Deferred. Only the product owner moves an item to Approved, and only an implementation
review moves it to Accepted; a merge alone does neither.

<!-- BEGIN GENERATED: items -->
## Local Development and Execution

| Id | Item | Priority | Owner | Effort | State |
| --- | --- | --- | --- | --- | --- |
| [PB-012](#pb-012) | Published Spark API compatibility matrix for this PC | P1 | Prove | M | Accepted |
| [PB-013](#pb-013) | Fix local-parity defects that give wrong answers | P1 | Master product (unassigned) | M | Accepted |
| [PB-014](#pb-014) | AST-based compatibility analyzer with an Unknown outcome | P1 | Prove | M | In progress |
| [PB-015](#pb-015) | Write semantics for MERGE, UPDATE and DELETE on this PC | P2 | Prove | L | In progress |
| [PB-016](#pb-016) | Reproducible environments and Fabric Environment items | P2 | Prove | L | In progress |
| [PB-099](#pb-099) | Local data engine and OneLake publishing program | P0 | Local data engine and OneLake publishing | XL | In progress |
| [PB-117](#pb-117) | Snowflake pillar: audit, ADRs and parity matrix | P3 | Fabric and Databricks unified development | M | In review |

### PB-012

**Published Spark API compatibility matrix for this PC** · P1 (score 3.51) · M · confidence High · Accepted

- **Problem:** Notebook authors can't tell which PySpark and notebookutils calls work on the PC before they hit an error; docs list some gaps by hand and the code differs from them in places.
- **Proposed capability:** A matrix of every supported, partial and refused Spark, Spark SQL and notebookutils call, generated from the parity tests on each build and published in the docs and the Prove panel.
- **Current-state evidence:** sidecar/fabric_desktop/spark_local.py raises for MERGE/UPDATE/DELETE and ignores partitionBy and reader schema(); docs/notebooks.md lists limits by hand; No generated list exists; No implementation review; checked against product data and main in the acceptance batch of 2026-10-28 (product-management/in-review-acceptance-batch.html): Accepted with Documented Limitations (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4); limitations: MERGE, UPDATE and DELETE, partitionBy and RDDs aren't supported. The page says local results aren't claimed to match Fabric.
- **Inventory features:** [FEAT-NB-04](FEATURE_INVENTORY.md#feat-nb-04), [FEAT-NB-05](FEATURE_INVENTORY.md#feat-nb-05), [FEAT-SQL-02](FEATURE_INVENTORY.md#feat-sql-02)
- **Dependencies:** none
- **Owner:** Prove (FD-111, Prove plan Stage 2)
- **Pull requests:** [#393](https://github.com/KratosDataGod/KratosForge/pull/393)
- **Acceptance criteria:**
    - Each row names the call, its status on the PC (works, differs, refused) and the test that proves it
    - CI fails when a test-backed row changes status without the matrix changing
    - The docs page states that local results are not claimed equivalent to Fabric unless a proof says so

### PB-013

**Fix local-parity defects that give wrong answers** · P1 (score 2.56) · M · confidence High · Accepted

- **Problem:** Several local behaviors differ silently from Fabric or from the app's own UI: a notebook using variableLibrary is flagged Fabric-only, %%sql MERGE cells report ok, notebookutils.session is missing though documented, insertInto defaults to errorifexists, and scheduled runs execute frozen cells that UI runs skip.
- **Proposed capability:** Each defect fixed or turned into an explicit, visible refusal, with a regression test per defect.
- **Current-state evidence:** fabric_run.py _FABRIC_ONLY includes notebookutils.variableLibrary; notebook.py %%sql MERGE path prints a note and returns ok; notebookutils_local.py docstring names session; attribute absent; local_schedules.py runs all cells; notebook.js skips frozen cells (inference from code reading); Re-checked 2026-10-25: all five defects are fixed on main (pr-ready/PB-013.md); No implementation review; checked against product data and main in the acceptance batch of 2026-10-28 (product-management/in-review-acceptance-batch.html): Accepted (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4)
- **Inventory features:** [FEAT-NB-04](FEATURE_INVENTORY.md#feat-nb-04), [FEAT-NB-05](FEATURE_INVENTORY.md#feat-nb-05), [FEAT-NB-07](FEATURE_INVENTORY.md#feat-nb-07), [FEAT-SQL-02](FEATURE_INVENTORY.md#feat-sql-02), [FEAT-ORC-01](FEATURE_INVENTORY.md#feat-orc-01)
- **Dependencies:** none
- **Owner:** Master product (unassigned) (FD-111 (analyzer is separate, PB-014))
- **Mandatory (required):** Wrong-answer defects in local execution undermine the core promise and the rule that local results are never claimed equivalent without proof.
- **Pull requests:** [#410](https://github.com/KratosDataGod/KratosForge/pull/410), [#692](https://github.com/KratosDataGod/KratosForge/pull/692), [#762](https://github.com/KratosDataGod/KratosForge/pull/762), [#771](https://github.com/KratosDataGod/KratosForge/pull/771), [#890](https://github.com/KratosDataGod/KratosForge/pull/890)
- **Story issues:** [#851](https://github.com/KratosDataGod/KratosForge/issues/851)
- **Implementation package:** [pr-ready/PB-013.md](pr-ready/PB-013.md)
- **Acceptance criteria:**
    - compute_check reports a variableLibrary notebook as able to run on the PC
    - A %%sql MERGE, UPDATE or DELETE cell ends with a 'skipped on this PC' status and Run all stops
    - notebookutils.session, lakehouse and connections raise a message that says what to do instead
    - Scheduled and UI runs treat frozen cells the same way, covered by one test

### PB-014

**AST-based compatibility analyzer with an Unknown outcome** · P1 (score 2.73) · M · confidence Medium · In progress

- **Problem:** The compute check is a regex scan, so it misses aliased imports and can't say 'not sure'.
- **Proposed capability:** A Python AST analyzer that reports, per cell, runs on PC / runs in Fabric / unknown with the reason, shared by Runs on, Prove and the advisor.
- **Current-state evidence:** fabric_run.py compute_check reads Python cells' AST (PB-014 branch): aliased and from-imports, SQL kept in variables, an Unknown status per side; Prove, Validate, the advisor and the risk check read it. Unknown in the ribbon is not built yet.; docs/product/enterprise-gap-analysis.md FD-111 partial; compute_check parses each Python cell, follows aliases and assignments, follows %run and notebook.run into notebooks on this PC, and reports Unknown with a reason; Prove, Validate, the advisor and the risk check read Unknown as not blocked and not fine (#1190, #1262, #1275, #1300, #1305, #1324) on main at b3c5b223. Criterion 2 (Unknown shown in the ribbon) not confirmed here: the notebook's note shows only for blockers; Implementation review docs/product/reviews/PB-014-1190.md (2026-10-31): Changes Required on criterion 2 only; the notebook doesn't show Unknown cells. Criteria 1 and 3 are met
- **Inventory features:** [FEAT-NB-07](FEATURE_INVENTORY.md#feat-nb-07)
- **Dependencies:** [PB-013](#pb-013)
- **Owner:** Prove (FD-111)
- **Pull requests:** [#1190](https://github.com/KratosDataGod/KratosForge/pull/1190), [#1262](https://github.com/KratosDataGod/KratosForge/pull/1262), [#1275](https://github.com/KratosDataGod/KratosForge/pull/1275), [#1300](https://github.com/KratosDataGod/KratosForge/pull/1300), [#1305](https://github.com/KratosDataGod/KratosForge/pull/1305), [#1324](https://github.com/KratosDataGod/KratosForge/pull/1324)
- **Acceptance criteria:**
    - Aliased and from-imports are recognized
    - Unknown is a distinct result shown in the ribbon
    - Prove and Runs on read the same analyzer output

### PB-015

**Write semantics for MERGE, UPDATE and DELETE on this PC** · P2 (score 2.25) · L · confidence Medium · In progress

- **Problem:** Spark notebooks that MERGE or UPDATE must run in Fabric today, so the most common incremental pattern costs capacity during development.
- **Proposed capability:** spark.sql MERGE/UPDATE/DELETE and DeltaTable merge executed through delta-rs on the PC, with the Fabric Delta profile check, behind a Preview flag.
- **Current-state evidence:** spark_local.py raises for MERGE, UPDATE, DELETE; meta/engine.py already merges with delta-rs for metadata runs; MERGE INTO (#933) and UPDATE and DELETE (#1028) run on this PC as Preview, and the run check judges them against the notebook's lakehouse (#1054) on main at 02118fa4. Criterion 4: Prove takes Spark notebooks (proof_spark.py), and a MERGE, UPDATE and DELETE proof matched with Apache Spark 3.5 and Delta 3.2 standing in for Fabric; the live run in Fabric waits on Chris's yes; Prove takes Spark notebooks with MERGE, UPDATE and DELETE: in Fabric the proof copy's first cell points Spark at the proof, tested with a real Spark and Delta standing in for Fabric (#1157) on main at 0c96c9ce. Criterion 4's live Prove run is still to do
- **Inventory features:** [FEAT-NB-04](FEATURE_INVENTORY.md#feat-nb-04)
- **Dependencies:** [PB-012](#pb-012), Overlaps FD-LDE-005 local Delta: the LDE thread builds create, append, overwrite and time travel; MERGE, UPDATE and DELETE semantics stay with this item (owners per docs/local-data-engine-plan.md section 3, PR #126)
- **Owner:** Prove (Prove plan (local Spark coverage))
- **Pull requests:** [#933](https://github.com/KratosDataGod/KratosForge/pull/933), [#1028](https://github.com/KratosDataGod/KratosForge/pull/1028), [#1054](https://github.com/KratosDataGod/KratosForge/pull/1054), [#1157](https://github.com/KratosDataGod/KratosForge/pull/1157), [#1272](https://github.com/KratosDataGod/KratosForge/pull/1272)
- **Story issues:** [#848](https://github.com/KratosDataGod/KratosForge/issues/848)
- **Acceptance criteria:**
    - The parity suite covers MERGE with matched/not-matched clauses, UPDATE and DELETE
    - Written tables pass the Fabric Delta profile check
    - Unsupported clauses are refused with a named reason
    - A Prove run of a MERGE notebook verifies equal outputs on PC and Fabric before the Preview tag is removed

### PB-016

**Reproducible environments and Fabric Environment items** · P2 (score 1.9) · L · confidence Low · In progress

- **Problem:** Packages differ between the PC and Fabric and nothing installs or pins them; Fabric Environment items can't be read or attached.
- **Proposed capability:** Environment profiles that pin packages for the PC and map to a Fabric Environment item's libraries and Spark settings.
- **Current-state evidence:** envprofile.py classifies imports only; fabric_api.py has no Environment item calls; Package-source decision open (Prove)
- **Inventory features:** [FEAT-PRV-04](FEATURE_INVENTORY.md#feat-prv-04), [FEAT-FAB-09](FEATURE_INVENTORY.md#feat-fab-09)
- **Dependencies:** Product-owner decision: package source for environment profiles (Prove)
- **Owner:** Prove (FD-109)
- **Pull requests:** [#906](https://github.com/KratosDataGod/KratosForge/pull/906), [#942](https://github.com/KratosDataGod/KratosForge/pull/942)
- **Story issues:** [#899](https://github.com/KratosDataGod/KratosForge/issues/899), [#941](https://github.com/KratosDataGod/KratosForge/issues/941)
- **Acceptance criteria:**
    - A notebook's profile installs the same versions on the PC that its Fabric Environment declares
    - The app can attach a notebook to an existing Environment item
    - Drift between PC and Fabric versions is shown before a run

### PB-099

**Local data engine and OneLake publishing program** · P0 (score 2.3) · XL · confidence Low · In progress

- **Problem:** Local files and SQL transformations can't be developed in a persistent local engine and published to OneLake with validation, publish history and incremental sync, without Fabric capacity.
- **Proposed capability:** Owned and specified by the Local data engine and OneLake publishing thread (FD-LDE-001..020); not restated here. Its plan (docs/local-data-engine-plan.md section 3) names the owner of each story; stories owned by other threads are FD-LDE-003 and 014 (SQL thread) and the FD-114/117 base of FD-LDE-011 (staged roadmap).
- **Current-state evidence:** DuckDB, sqlglot and delta-rs already in the engine (sql.py, spark_local.py, publish.py, meta/store.py); Audit, story owners and PR plan in docs/local-data-engine-plan.md (PR #126, which also carries FD-LDE-001/002); DuckDB 2.0 readiness (#1341 parts: #1343 to #1357), sqlglot range tested at both ends (#1274), and OneLake reads, writes, shortcut checks and leases wait out throttling (#1291, #1293, #1327) on main at b3c5b223; DuckDB 2.0: every engine Arrow reader, notebook SQL cells and the TPC-H notebook read relations, and CI runs the engine suite on 2.0 (#1355, #1490, #1498, #1502) on main at 063b84ed; 2.0 is still not allowed in pyproject
- **Inventory features:** [FEAT-SQL-01](FEATURE_INVENTORY.md#feat-sql-01), [FEAT-NB-11](FEATURE_INVENTORY.md#feat-nb-11), [FEAT-ONE-05](FEATURE_INVENTORY.md#feat-one-05)
- **Dependencies:** Overlaps UX-004 screens for local data projects (Local data engine) (UX and simplicity backlog, PB-102), Overlaps HD-034 fresh token before long writes (local data engine); HD-001..004 Delta commit, profile and OneLake path guards (P0, hardening thread) (Codebase audit and hardening backlog, PB-101)
- **Owner:** Local data engine and OneLake publishing (FD-LDE-001..020)
- **Priority set by the product owner:** P0 (Chris (product owner), 2026-10-17): Local execution without Fabric capacity is the product's core mission; build FD-LDE-001..020 as P0 despite its size
- **Merged duplicates:** Overlaps PB-021..PB-025 and PB-020 (SQL sprint: SQL editor, dialect, execution, connectivity, local Warehouse); Overlaps PB-015 (local Delta writes), PB-019 (incremental), PB-067 (large datasets), PB-050/PB-090 (FinOps), PB-079 (TOML job runner) and the metadata framework
- **Pull requests:** [#580](https://github.com/KratosDataGod/KratosForge/pull/580), [#588](https://github.com/KratosDataGod/KratosForge/pull/588), [#609](https://github.com/KratosDataGod/KratosForge/pull/609), [#610](https://github.com/KratosDataGod/KratosForge/pull/610), [#612](https://github.com/KratosDataGod/KratosForge/pull/612), [#620](https://github.com/KratosDataGod/KratosForge/pull/620), [#622](https://github.com/KratosDataGod/KratosForge/pull/622), [#628](https://github.com/KratosDataGod/KratosForge/pull/628), [#631](https://github.com/KratosDataGod/KratosForge/pull/631), [#1210](https://github.com/KratosDataGod/KratosForge/pull/1210), [#1254](https://github.com/KratosDataGod/KratosForge/pull/1254), [#1268](https://github.com/KratosDataGod/KratosForge/pull/1268), [#1274](https://github.com/KratosDataGod/KratosForge/pull/1274), [#1291](https://github.com/KratosDataGod/KratosForge/pull/1291), [#1293](https://github.com/KratosDataGod/KratosForge/pull/1293), [#1304](https://github.com/KratosDataGod/KratosForge/pull/1304), [#1326](https://github.com/KratosDataGod/KratosForge/pull/1326), [#1327](https://github.com/KratosDataGod/KratosForge/pull/1327), [#1338](https://github.com/KratosDataGod/KratosForge/pull/1338), [#1342](https://github.com/KratosDataGod/KratosForge/pull/1342), [#1343](https://github.com/KratosDataGod/KratosForge/pull/1343), [#1344](https://github.com/KratosDataGod/KratosForge/pull/1344), [#1345](https://github.com/KratosDataGod/KratosForge/pull/1345), [#1346](https://github.com/KratosDataGod/KratosForge/pull/1346), [#1349](https://github.com/KratosDataGod/KratosForge/pull/1349), [#1355](https://github.com/KratosDataGod/KratosForge/pull/1355), [#1356](https://github.com/KratosDataGod/KratosForge/pull/1356), [#1357](https://github.com/KratosDataGod/KratosForge/pull/1357), [#1363](https://github.com/KratosDataGod/KratosForge/pull/1363), [#1490](https://github.com/KratosDataGod/KratosForge/pull/1490), [#1498](https://github.com/KratosDataGod/KratosForge/pull/1498), [#1502](https://github.com/KratosDataGod/KratosForge/pull/1502)
- **Acceptance criteria:**
    - As defined in FD-LDE-001..020 by its owning thread
    - Each story's overlap with an existing item follows the owner named in that plan

### PB-117

**Snowflake pillar: audit, ADRs and parity matrix** · P3 (score 1.56) · M · confidence Medium · In review

- **Problem:** Snowflake is one of the three target platforms and has no work at all.
- **Proposed capability:** An evidence-based plan: what Snowflake development can run on the PC, what needs a live account, and how it fits the engine registry and connection profiles.
- **Current-state evidence:** No Snowflake code or docs on main; Story KF-SNOWFLAKE-RESEARCH in the orchestration plan (#864), queue priority P4; #993 (SF-00 engine registry) and #995 (SF-04 SQL on this PC) merged as Preview; 114 Snowflake function calls are compared with the values Snowflake's SQL reference documents, and the docs table is generated from that check (#1202, no Snowflake account used) on main at f3750b9d
- **Inventory features:** [FEAT-EXT-05](FEATURE_INVENTORY.md#feat-ext-05)
- **Dependencies:** No code and no Snowflake account spend before Chris's go
- **Owner:** Fabric and Databricks unified development (KF-SNOWFLAKE-RESEARCH)
- **Pull requests:** [#993](https://github.com/KratosDataGod/KratosForge/pull/993), [#995](https://github.com/KratosDataGod/KratosForge/pull/995), [#1202](https://github.com/KratosDataGod/KratosForge/pull/1202), [#1225](https://github.com/KratosDataGod/KratosForge/pull/1225), [#1248](https://github.com/KratosDataGod/KratosForge/pull/1248), [#1321](https://github.com/KratosDataGod/KratosForge/pull/1321)
- **Story issues:** [#853](https://github.com/KratosDataGod/KratosForge/issues/853)
- **Acceptance criteria:**
    - A parity matrix like databricks/parity-matrix.md, with official sources for each row
    - ADRs for sign-in, the engine adapter and what never runs inside the engine process
    - A sliced plan of independently reviewable PRs

## SQL, Python, and Notebook Development

| Id | Item | Priority | Owner | Effort | State |
| --- | --- | --- | --- | --- | --- |
| [PB-020](#pb-020) | Local Warehouse simulation | P3 | SQL development sprint | XL | Proposed |
| [PB-021](#pb-021) | SQL editor with IntelliSense and formatting | P2 | SQL development sprint | M | Accepted |
| [PB-022](#pb-022) | Fabric SQL dialect compatibility analyzer | P1 | SQL development sprint | M | Accepted |
| [PB-023](#pb-023) | Managed SQL execution with cancel and streaming | P2 | SQL development sprint | M | Accepted |
| [PB-024](#pb-024) | Native Warehouse connectivity | P3 | Build full warehouse development experience | L | In review |
| [PB-025](#pb-025) | SQL object explorer | P2 | Build full warehouse development experience | M | Accepted |
| [PB-026](#pb-026) | Notebook debugger | P3 | Prove | L | Accepted |
| [PB-027](#pb-027) | Column completion for attached tables | P2 | Prove | S | In review |
| [PB-028](#pb-028) | Package install magics that work on this PC | P2 | Prove | S | Accepted |
| [PB-106](#pb-106) | Warehouse development program | P2 | Build full warehouse development experience | XL | In progress |
| [PB-114](#pb-114) | Fabric SQL database connectivity | P3 | Build full warehouse development experience | L | Proposed |
| [PB-118](#pb-118) | Databricks notebook conversion says what it could not carry over | P3 | Fabric and Databricks unified development | S | In review |

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

**SQL editor with IntelliSense and formatting** · P2 (score 2.42) · M · confidence High · Accepted

- **Problem:** The SQL page is a plain textarea with no completion or formatting.
- **Proposed capability:** Owned and specified by the SQL development sprint (SQL-001).
- **Current-state evidence:** app/ui/app.js SQL page uses a textarea; PR #118 merged 2026-10-17; awaiting implementation review; No implementation review; checked against product data and main in the acceptance batch of 2026-10-28 (product-management/in-review-acceptance-batch.html): Accepted (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4)
- **Inventory features:** [FEAT-SQL-01](FEATURE_INVENTORY.md#feat-sql-01)
- **Dependencies:** Overlaps FD-LDE-003 local SQL in the editor: the SQL thread owns the editor; the LDE thread supplies the local engine calls (owners per docs/local-data-engine-plan.md section 3, PR #126)
- **Owner:** SQL development sprint (SQL-001)
- **Pull requests:** [#118](https://github.com/KratosDataGod/KratosForge/pull/118)
- **Acceptance criteria:**
    - As defined in SQL-001 by its owning thread

### PB-022

**Fabric SQL dialect compatibility analyzer** · P1 (score 2.73) · M · confidence Medium · Accepted

- **Problem:** Spark SQL translated to DuckDB can succeed locally and still fail in Fabric (or the reverse), and nothing says so.
- **Proposed capability:** Owned and specified by the SQL development sprint (SQL-002).
- **Current-state evidence:** sql.py translates with sqlglot without a compatibility report; PR #120 merged 2026-10-17; awaiting implementation review; No implementation review; checked against product data and main in the acceptance batch of 2026-10-28 (product-management/in-review-acceptance-batch.html): Accepted (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4)
- **Inventory features:** [FEAT-SQL-01](FEATURE_INVENTORY.md#feat-sql-01), [FEAT-FAB-11](FEATURE_INVENTORY.md#feat-fab-11)
- **Dependencies:** Overlaps FD-LDE-004 T-SQL translation (LDE thread, built on SQL-002's profiles) and FD-LDE-014 SQL compatibility validation (SQL thread) (owners per docs/local-data-engine-plan.md section 3, PR #126)
- **Owner:** SQL development sprint (SQL-002)
- **Pull requests:** [#120](https://github.com/KratosDataGod/KratosForge/pull/120)
- **Acceptance criteria:**
    - As defined in SQL-002 by its owning thread

### PB-023

**Managed SQL execution with cancel and streaming** · P2 (score 2.27) · M · confidence High · Accepted

- **Problem:** SQL queries can't be cancelled, show only the first 1,000 rows, and Download CSV runs the query again.
- **Proposed capability:** Owned and specified by the SQL development sprint (SQL-003).
- **Current-state evidence:** service.py run_sql returns a fixed page; No cancel RPC for SQL; PR #134 merged 2026-10-17; awaiting implementation review; No implementation review; checked against product data and main in the acceptance batch of 2026-10-28 (product-management/in-review-acceptance-batch.html): Accepted (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4)
- **Inventory features:** [FEAT-SQL-01](FEATURE_INVENTORY.md#feat-sql-01)
- **Dependencies:** Overlaps FD-LDE-003 and FD-LDE-019: the SQL thread owns execution and streaming (SQL-003); the LDE thread supplies engine calls and large-dataset export (owners per docs/local-data-engine-plan.md section 3, PR #126), Overlaps HD-015 external SQL timeout interrupts (Codebase audit and hardening backlog, PB-101)
- **Owner:** SQL development sprint (SQL-003)
- **Pull requests:** [#134](https://github.com/KratosDataGod/KratosForge/pull/134)
- **Acceptance criteria:**
    - As defined in SQL-003 by its owning thread

### PB-024

**Native Warehouse connectivity** · P3 (score 1.55) · L · confidence Medium · In review

- **Problem:** T-SQL developers can't reach a Warehouse, SQL analytics endpoint or Fabric SQL database from the app.
- **Proposed capability:** Owned and specified by the SQL development sprint (SQL-004).
- **Current-state evidence:** SQL-004 read-only warehouse queries (#581) with mssql-python (#580); Warehouse page WH-01 (#636); mssql-python is in sidecar/constraints.txt (#580); a first tenant run on 2026-10-27 led to #808 and #809; Narrowed to warehouses and SQL analytics endpoints (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4); Fabric SQL database moved to PB-114. Stays In review until the ODBC driver licence terms (PB-024 review, finding 2) are settled; acceptance batch 2 of 2026-10-29 (product-management/in-review-acceptance-batch-2.html): kept In review (Chris chose 'Accept all as recommended' on the decision card, 2026-10-30, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpKnZygxfq5AA9Hd5e1zTC9b): Stays open until the end-user terms are settled.
- **Inventory features:** [FEAT-FAB-11](FEATURE_INVENTORY.md#feat-fab-11)
- **Dependencies:** [PB-021](#pb-021), Overlaps FD-LDE-012 and FD-LDE-013 local TDS endpoint and Power BI Desktop (LDE thread, only after Chris's go) (owners per docs/local-data-engine-plan.md section 3, PR #126), End-user terms for the Microsoft ODBC driver the engine ships (counsel)
- **Owner:** Build full warehouse development experience (SQL-004)
- **Pull requests:** [#580](https://github.com/KratosDataGod/KratosForge/pull/580), [#581](https://github.com/KratosDataGod/KratosForge/pull/581), [#636](https://github.com/KratosDataGod/KratosForge/pull/636)
- **Acceptance criteria:**
    - As defined in SQL-004 by its owning thread

### PB-025

**SQL object explorer** · P2 (score 2.29) · M · confidence Medium · Accepted

- **Problem:** There is no tree of schemas, tables, views and procedures for SQL work.
- **Proposed capability:** Owned and specified by the SQL development sprint (SQL-005).
- **Current-state evidence:** Lakehouse explorer lists tables only; WH-02 object explorer (#637) and WH-03 Script as (#638) on the Warehouse page; Implementation review docs/product/reviews/PB-025-637.md: Accepted with Documented Limitations (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4); limitations: None blocking. Findings 1 and 2 (wording, keyboard tree) were fixed in #835.
- **Inventory features:** [FEAT-FAB-11](FEATURE_INVENTORY.md#feat-fab-11)
- **Dependencies:** [PB-024](#pb-024)
- **Owner:** Build full warehouse development experience (SQL-005)
- **Pull requests:** [#637](https://github.com/KratosDataGod/KratosForge/pull/637), [#638](https://github.com/KratosDataGod/KratosForge/pull/638), [#833](https://github.com/KratosDataGod/KratosForge/pull/833), [#835](https://github.com/KratosDataGod/KratosForge/pull/835)
- **Acceptance criteria:**
    - As defined in SQL-005 by its owning thread

### PB-026

**Notebook debugger** · P3 (score 1.6) · L · confidence Medium · Accepted

- **Problem:** There are no breakpoints, stepping or watches; competitors (Databricks, Snowflake, VS Code) offer debugging.
- **Proposed capability:** Breakpoints and stepping in the process kernel, planned by the Prove program.
- **Current-state evidence:** No debugger code; docs/prove/plan.md Stage 5 backlog #44; No implementation review; checked against product data and main in the acceptance batch of 2026-10-28 (product-management/in-review-acceptance-batch.html): Accepted with Documented Limitations (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4); limitations: Notebooks only, and it needs Run notebooks in their own process.
- **Inventory features:** [FEAT-EXT-04](FEATURE_INVENTORY.md#feat-ext-04)
- **Dependencies:** [PB-073](#pb-073)
- **Owner:** Prove (Prove plan #44)
- **Pull requests:** [#387](https://github.com/KratosDataGod/KratosForge/pull/387)
- **Acceptance criteria:**
    - Set a breakpoint in a cell, step, inspect variables, continue
    - Works only in the process kernel; the ribbon says so otherwise

### PB-027

**Column completion for attached tables** · P2 (score 2.21) · S · confidence High · In review

- **Problem:** Completion knows only what the kernel has run, so column names of attached tables aren't offered.
- **Proposed capability:** Completion of table and column names from the attached lakehouse's Delta schemas.
- **Current-state evidence:** notebook.py kernel_complete returned nothing for names not yet in the namespace (smoke); A %%sql cell completes an alias's columns from the table's Delta schema without running a cell, and falls back on cached names offline (#901; test_schema_cache.py, notebook-sql-complete.test.cjs) on main at 0c96c9ce
- **Inventory features:** [FEAT-NB-08](FEATURE_INVENTORY.md#feat-nb-08)
- **Dependencies:** none
- **Owner:** Prove (Prove plan #12)
- **Pull requests:** [#901](https://github.com/KratosDataGod/KratosForge/pull/901)
- **Story issues:** [#878](https://github.com/KratosDataGod/KratosForge/issues/878)
- **Acceptance criteria:**
    - Typing a table alias offers its columns without running a cell
    - Works offline from cached schemas

### PB-028

**Package install magics that work on this PC** · P2 (score 2.0) · S · confidence Medium · Accepted

- **Problem:** %pip is skipped with a note, so a missing package fails later; !pip and %%time would be syntax errors.
- **Proposed capability:** %pip and !pip install into the notebook's environment profile, %%time supported.
- **Current-state evidence:** notebook.py magic handling (code reading); No implementation review; checked against product data and main in the acceptance batch of 2026-10-28 (product-management/in-review-acceptance-batch.html): Accepted with Documented Limitations (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4); limitations: %%spark, %%sparkr and %%csharp still run in Fabric.
- **Inventory features:** [FEAT-NB-06](FEATURE_INVENTORY.md#feat-nb-06)
- **Dependencies:** [PB-016](#pb-016)
- **Owner:** Prove (FD-109)
- **Pull requests:** [#440](https://github.com/KratosDataGod/KratosForge/pull/440), [#515](https://github.com/KratosDataGod/KratosForge/pull/515)
- **Acceptance criteria:**
    - %pip install x installs into the active profile or explains why it can't
    - !pip and %%time no longer raise syntax errors

### PB-106

**Warehouse development program** · P2 (score 2.52) · XL · confidence Medium · In progress

- **Problem:** Fabric warehouse developers write T-SQL in the browser with no local checking, templates, saved work or Git deploy preview.
- **Proposed capability:** Owned and specified by the 'Build full warehouse development experience' thread (WH-01..21, SF-01..06); not restated here. Phases: connect and query, templates and saved work, build and deploy from Git, manage data, then Snowflake.
- **Current-state evidence:** Chris's go 2026-10-24 18:16Z for phases 1-2; WH-01/02/03/04/05/04b/08 merged as #636-#641, #645
- **Inventory features:** [FEAT-FAB-11](FEATURE_INVENTORY.md#feat-fab-11), [FEAT-SQL-01](FEATURE_INVENTORY.md#feat-sql-01), [FEAT-SQL-03](FEATURE_INVENTORY.md#feat-sql-03)
- **Dependencies:** [PB-024](#pb-024), [PB-025](#pb-025), Decision D1 warehouse writes (Chris), Decision D3 live read query (Chris), Phases 3-5 need Chris's go
- **Owner:** Build full warehouse development experience (WH-01..21, SF-01..06)
- **Merged duplicates:** Absorbs PB-020 local warehouse simulation and the SQL sprint's SQL-004/005 screens
- **Pull requests:** [#636](https://github.com/KratosDataGod/KratosForge/pull/636), [#637](https://github.com/KratosDataGod/KratosForge/pull/637), [#638](https://github.com/KratosDataGod/KratosForge/pull/638), [#639](https://github.com/KratosDataGod/KratosForge/pull/639), [#641](https://github.com/KratosDataGod/KratosForge/pull/641), [#645](https://github.com/KratosDataGod/KratosForge/pull/645), [#660](https://github.com/KratosDataGod/KratosForge/pull/660), [#662](https://github.com/KratosDataGod/KratosForge/pull/662), [#723](https://github.com/KratosDataGod/KratosForge/pull/723), [#727](https://github.com/KratosDataGod/KratosForge/pull/727), [#728](https://github.com/KratosDataGod/KratosForge/pull/728), [#729](https://github.com/KratosDataGod/KratosForge/pull/729), [#730](https://github.com/KratosDataGod/KratosForge/pull/730), [#731](https://github.com/KratosDataGod/KratosForge/pull/731), [#743](https://github.com/KratosDataGod/KratosForge/pull/743), [#745](https://github.com/KratosDataGod/KratosForge/pull/745), [#747](https://github.com/KratosDataGod/KratosForge/pull/747), [#774](https://github.com/KratosDataGod/KratosForge/pull/774), [#783](https://github.com/KratosDataGod/KratosForge/pull/783), [#787](https://github.com/KratosDataGod/KratosForge/pull/787), [#808](https://github.com/KratosDataGod/KratosForge/pull/808), [#809](https://github.com/KratosDataGod/KratosForge/pull/809), [#823](https://github.com/KratosDataGod/KratosForge/pull/823), [#912](https://github.com/KratosDataGod/KratosForge/pull/912), [#949](https://github.com/KratosDataGod/KratosForge/pull/949), [#985](https://github.com/KratosDataGod/KratosForge/pull/985), [#1112](https://github.com/KratosDataGod/KratosForge/pull/1112), [#1181](https://github.com/KratosDataGod/KratosForge/pull/1181)
- **Story issues:** [#810](https://github.com/KratosDataGod/KratosForge/issues/810), [#946](https://github.com/KratosDataGod/KratosForge/issues/946), [#947](https://github.com/KratosDataGod/KratosForge/issues/947)
- **Acceptance criteria:**
    - As defined in WH-01..21 by its owning thread

### PB-114

**Fabric SQL database connectivity** · P3 (score 1.55) · L · confidence Medium · Proposed

- **Problem:** The Warehouse page and the engine reach warehouses and SQL analytics endpoints only; a Fabric SQL database can't be queried (PB-024 review, finding 1).
- **Proposed capability:** Resolve and connect to Fabric SQL database items the same way as warehouses, with the same read-only rule and write confirms.
- **Current-state evidence:** Split from PB-024 on Chris's acceptance of the 2026-10-28 batch; warehouse.resolve accepts warehouse and endpoint kinds and *.datawarehouse.fabric.microsoft.com hosts only (PB-024 review)
- **Inventory features:** [FEAT-FAB-11](FEATURE_INVENTORY.md#feat-fab-11)
- **Dependencies:** [PB-024](#pb-024)
- **Owner:** Build full warehouse development experience (docs/product/reviews/PB-024-581.md finding 1)
- **Acceptance criteria:**
    - A Fabric SQL database opens on the Warehouse page and runs a read-only query
    - Writes follow the same preview and confirm as warehouses

### PB-118

**Databricks notebook conversion says what it could not carry over** · P3 (score 1.76) · S · confidence Medium · In review

- **Problem:** Importing or exporting Databricks notebooks drops widgets, magics and dbutils calls without telling the developer.
- **Proposed capability:** A loss report on every Databricks import and export, extending the existing converter.
- **Current-state evidence:** Story KF-DBX-LOSS-REPORT in the orchestration plan (#864); Databricks import and export list every construct that didn't carry over, cell by cell, and name the Fabric migration gotchas (#1053, #1146; test_notebook_formats.py) on main at 02118fa4
- **Inventory features:** [FEAT-NB-26](FEATURE_INVENTORY.md#feat-nb-26), [FEAT-NB-12](FEATURE_INVENTORY.md#feat-nb-12)
- **Dependencies:** none
- **Owner:** Fabric and Databricks unified development (KF-DBX-LOSS-REPORT)
- **Pull requests:** [#1053](https://github.com/KratosDataGod/KratosForge/pull/1053), [#1146](https://github.com/KratosDataGod/KratosForge/pull/1146), [#1242](https://github.com/KratosDataGod/KratosForge/pull/1242), [#1289](https://github.com/KratosDataGod/KratosForge/pull/1289)
- **Story issues:** [#854](https://github.com/KratosDataGod/KratosForge/issues/854)
- **Acceptance criteria:**
    - Each dropped or changed construct is listed with its cell
    - Round-trip tests cover widgets, %sh, %fs and dbutils
    - Docs list what converts and what doesn't

## Data Engineering and Transformation

| Id | Item | Priority | Owner | Effort | State |
| --- | --- | --- | --- | --- | --- |
| [PB-019](#pb-019) | Late-arriving rows and source deletes in incremental loads | P2 | Staged roadmap | M | Accepted |
| [PB-031](#pb-031) | Schema evolution without full rewrites | P2 | Staged roadmap | M | Accepted |
| [PB-067](#pb-067) | Bounded-memory runs: run dialog and orphan-file cleanup | P2 | Staged roadmap | M | In progress |
| [PB-068](#pb-068) | Key lookups without reading whole key tables | P2 | Staged roadmap | M | Accepted |
| [PB-079](#pb-079) | Decide the future of the TOML job runner | P2 | Master product (unassigned) | S | In progress |

### PB-019

**Late-arriving rows and source deletes in incremental loads** · P2 (score 2.19) · M · confidence Medium · Accepted

- **Problem:** The watermark filter is strictly greater-than, so rows that arrive late are dropped, and deletes at the source never reach the table.
- **Proposed capability:** A lookback window and soft- or hard-delete handling per entity.
- **Current-state evidence:** meta/engine.py watermark comparison; docs/scale-and-operations-plan.md capability 3; Late-arriving rows and source deletes in incremental loads merged (#914) on main at 4eb1f509; No implementation review; checked against product data and main in acceptance batch 2 of 2026-10-29 (product-management/in-review-acceptance-batch-2.html): Accepted with Documented Limitations (Chris chose 'Accept all as recommended' on the decision card, 2026-10-30, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpKnZygxfq5AA9Hd5e1zTC9b); limitations: Opt-in, for Delta sources with keys only. An empty source stops a deletes run instead of deleting everything. Not run on a tenant.
- **Inventory features:** [FEAT-META-02](FEATURE_INVENTORY.md#feat-meta-02)
- **Dependencies:** Overlaps FD-LDE-011 incremental and MERGE: this item's owner keeps FD-114/117; the LDE thread builds dataset-level publish on top of them (owners per docs/local-data-engine-plan.md section 3, PR #126)
- **Owner:** Staged roadmap (FD-117)
- **Pull requests:** [#914](https://github.com/KratosDataGod/KratosForge/pull/914), [#1013](https://github.com/KratosDataGod/KratosForge/pull/1013)
- **Story issues:** [#883](https://github.com/KratosDataGod/KratosForge/issues/883)
- **Acceptance criteria:**
    - A late row inside the lookback lands exactly once
    - A source delete marks or removes the row per the entity setting
    - Tests cover both on the PC

### PB-031

**Schema evolution without full rewrites** · P2 (score 2.04) · M · confidence Medium · Accepted

- **Problem:** Type changes rewrite whole tables and contracts aren't versioned per change.
- **Proposed capability:** Additive and widening changes applied in place; versioned schema history tied to the breaking-change check.
- **Current-state evidence:** meta/drift.py; docs/scale-and-operations-plan.md FD-118; Each schema change is recorded in meta.schema_history with its version, and narrowing is refused unless overwrite_schema (#979) on main at 188d5a93. Widening still rewrites the table: delta-rs 1.6.6 can't write typeWidening, and it would leave the Fabric-safe profile. Criterion 1 can't be met as written; Chris decides at acceptance; acceptance batch 2 of 2026-10-29 (product-management/in-review-acceptance-batch-2.html): criterion 1 changed and Accepted with Documented Limitations (Chris chose 'Accept all as recommended' on the decision card, 2026-10-30, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpKnZygxfq5AA9Hd5e1zTC9b); limitations: widening rewrites the table, because delta-rs 1.6.6 can't write Delta's typeWidening and it would leave the Fabric-safe profile
- **Inventory features:** [FEAT-META-07](FEATURE_INVENTORY.md#feat-meta-07)
- **Dependencies:** none
- **Owner:** Staged roadmap (FD-118)
- **Pull requests:** [#979](https://github.com/KratosDataGod/KratosForge/pull/979)
- **Story issues:** [#904](https://github.com/KratosDataGod/KratosForge/issues/904)
- **Acceptance criteria:**
    - Widening a type rewrites the table, narrowing is refused unless overwrite_schema
    - Each schema change is recorded with its version

### PB-067

**Bounded-memory runs: run dialog and orphan-file cleanup** · P2 (score 2.52) · M · confidence High · In progress

- **Problem:** The preflight estimate exists but has no screen, and a stopped batch can leave orphan files.
- **Proposed capability:** A run dialog showing the estimate and choices, and cleanup of files a stopped batch wrote.
- **Current-state evidence:** meta_preflight has no caller in app/ui; meta/batches.py; Delete left-overs in Settings and Runs on (#687, #706) removes orphan local write files; the run dialog is still open; A stopped batched load removes the files it wrote, or leaves them for VACUUM when another writer committed (#950) on main at 188d5a93. The run dialog (criterion 1) is still to build: meta_preflight has no caller in app/ui
- **Inventory features:** [FEAT-META-06](FEATURE_INVENTORY.md#feat-meta-06)
- **Dependencies:** Overlaps FD-LDE-019 large datasets: the LDE thread with the SQL thread (SQL-003) (owners per docs/local-data-engine-plan.md section 3, PR #126), Overlaps HD-033 bounded previews (staged roadmap) (Codebase audit and hardening backlog, PB-101)
- **Owner:** Staged roadmap (FD-114)
- **Pull requests:** [#687](https://github.com/KratosDataGod/KratosForge/pull/687), [#706](https://github.com/KratosDataGod/KratosForge/pull/706), [#950](https://github.com/KratosDataGod/KratosForge/pull/950)
- **Story issues:** [#915](https://github.com/KratosDataGod/KratosForge/issues/915)
- **Acceptance criteria:**
    - The run dialog shows the estimate before a large run (after a mockup)
    - Files from a stopped batch are removed or vacuumed

### PB-068

**Key lookups without reading whole key tables** · P2 (score 2.42) · M · confidence High · Accepted

- **Problem:** Every keyed run reads the whole key table, which grows with the business.
- **Proposed capability:** Key lookups limited to the business keys in the batch.
- **Current-state evidence:** meta/keys.py read_keys; No implementation review; checked against product data and main in acceptance batch 2 of 2026-10-29 (product-management/in-review-acceptance-batch-2.html): Accepted (Chris chose 'Accept all as recommended' on the decision card, 2026-10-30, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpKnZygxfq5AA9Hd5e1zTC9b)
- **Inventory features:** [FEAT-META-05](FEATURE_INVENTORY.md#feat-meta-05)
- **Dependencies:** Overlaps HD-032 T-SQL key import fails above 699 rows (staged roadmap) (Codebase audit and hardening backlog, PB-101), Direction change (Chris's unit testing, 2026-10-18, item 25): one Fabric SQL database in a dedicated Keys workspace shared by all environments (Chris, 2026-10-20: one for all); built in PR #293, not yet moved on a live tenant
- **Owner:** Staged roadmap (FD-115)
- **Pull requests:** [#913](https://github.com/KratosDataGod/KratosForge/pull/913)
- **Story issues:** [#847](https://github.com/KratosDataGod/KratosForge/issues/847)
- **Acceptance criteria:**
    - A keyed run reads only matching key rows, shown by a test on a large key table

### PB-079

**Decide the future of the TOML job runner** · P2 (score 1.85) · S · confidence High · In progress

- **Problem:** Job files run from the CLI only; the app labels their history as a notebook write and nothing in the UI creates them.
- **Proposed capability:** A product decision: expose jobs in the app, or keep them a CLI and agent feature and document it so.
- **Current-state evidence:** jobs.py, cli.py preview/run; no UI caller of preview_job or run_job; Labels made honest: Monitor and Optimize call a job file's run a Job run (#908). The decision isn't recorded in implementation-decisions.md yet on main at b19b7456
- **Inventory features:** [FEAT-NB-11](FEATURE_INVENTORY.md#feat-nb-11)
- **Dependencies:** Overlaps FD-LDE-006 local transformations: the LDE thread builds parameterized SQL scripts; pipeline orchestration stays with the ORCH thread (owners per docs/local-data-engine-plan.md section 3, PR #126)
- **Owner:** Master product (unassigned)
- **Pull requests:** [#908](https://github.com/KratosDataGod/KratosForge/pull/908)
- **Story issues:** [#900](https://github.com/KratosDataGod/KratosForge/issues/900)
- **Acceptance criteria:**
    - Decision recorded in docs/product/implementation-decisions.md
    - Docs and Monitor labels match the decision

## Pipelines and Orchestration

| Id | Item | Priority | Owner | Effort | State |
| --- | --- | --- | --- | --- | --- |
| [PB-030](#pb-030) | Data pipeline and Copy job scope after decision D2 | P3 | Staged roadmap | L | Proposed |
| [PB-035](#pb-035) | Pipeline definition engine | P2 | Local Fabric pipeline execution | L | Accepted |
| [PB-036](#pb-036) | Pipeline activity compatibility registry | P1 | Local Fabric pipeline execution | M | Accepted |
| [PB-037](#pb-037) | Local DAG execution engine | P3 | Local Fabric pipeline execution | XL | Accepted |
| [PB-044](#pb-044) | Agent queue pruning and dead-job requeue | P2 | Master product (unassigned) | S | Accepted |
| [PB-045](#pb-045) | Shared queue for several workers | P3 | Workload management | L | In review |
| [PB-048](#pb-048) | Schedule editing and safer schedule removal | P2 | Master product (unassigned) | M | Accepted |
| [PB-049](#pb-049) | Run the pipeline mailbox once on a tenant | P2 | Staged roadmap | S | In progress |
| [PB-107](#pb-107) | Data Factory program | P3 | Build full Data Factory experience | XL | In progress |

### PB-030

**Data pipeline and Copy job scope after decision D2** · P3 (score 1.6) · L · confidence Low · Proposed

- **Problem:** Nobody creates Copy jobs or pipelines from the app, and who lands data into LH_Landing is undecided.
- **Proposed capability:** Decision D2 first; then guided ingestion as the staged roadmap defines it.
- **Current-state evidence:** fabric_api.py has no pipeline or Copy job create; Open question D2 in project memory
- **Inventory features:** [FEAT-FAB-10](FEATURE_INVENTORY.md#feat-fab-10), [FEAT-MESH-03](FEATURE_INVENTORY.md#feat-mesh-03)
- **Dependencies:** Product-owner decision D2: who lands data and creates Copy jobs, Direction change (Chris's unit testing, 2026-10-18, item 24): one landing workspace shared by Dev, Test and Prod, split by domain with secure zones; who writes to it is still D2
- **Owner:** Staged roadmap (FD-116)
- **Acceptance criteria:**
    - D2 answered by the product owner
    - Guided ingestion stages updated to match

### PB-035

**Pipeline definition engine** · P2 (score 2.15) · L · confidence Medium · Accepted

- **Problem:** Fabric Data pipeline definitions can't be read, validated or run outside Fabric.
- **Proposed capability:** Owned and specified by the Local Fabric pipeline execution thread (ORCH-001).
- **Current-state evidence:** No pipeline definition parsing in the repo; PR #130 merged 2026-10-17; awaiting implementation review; No implementation review; checked against product data and main in the acceptance batch of 2026-10-28 (product-management/in-review-acceptance-batch.html): Accepted with Documented Limitations (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4); limitations: Run in Fabric and Refresh in Fabric haven't run on a tenant.
- **Inventory features:** [FEAT-FAB-10](FEATURE_INVENTORY.md#feat-fab-10)
- **Dependencies:** none
- **Owner:** Local Fabric pipeline execution (ORCH-001)
- **Pull requests:** [#130](https://github.com/KratosDataGod/KratosForge/pull/130)
- **Acceptance criteria:**
    - As defined in ORCH-001 by its owning thread

### PB-036

**Pipeline activity compatibility registry** · P1 (score 2.76) · M · confidence Medium · Accepted

- **Problem:** There is no list of which pipeline activities can run locally.
- **Proposed capability:** Owned and specified by the Local Fabric pipeline execution thread (ORCH-002).
- **Current-state evidence:** sidecar/fabric_desktop/orchestration/registry.py and activities.py: 28 entries at registry version 7 (15 local, 5 static, 8 cloud), with the overclaim rules and a fingerprint test; sidecar/tests/test_activity_registry.py: 77 tests pass on main at d0747a66 (2026-10-28); fabric-desktop pipeline compat|activities, Service pipeline_compatibility and pipeline_activity_registry, two Preview capabilities; docs/orchestration/activity-registry.md; No implementation review; checked against product data and main in the acceptance batch of 2026-10-28 (product-management/in-review-acceptance-batch.html): Accepted (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4)
- **Inventory features:** [FEAT-ORC-09](FEATURE_INVENTORY.md#feat-orc-09)
- **Dependencies:** [PB-035](#pb-035)
- **Owner:** Local Fabric pipeline execution (ORCH-002)
- **Pull requests:** [#150](https://github.com/KratosDataGod/KratosForge/pull/150), [#563](https://github.com/KratosDataGod/KratosForge/pull/563), [#568](https://github.com/KratosDataGod/KratosForge/pull/568), [#871](https://github.com/KratosDataGod/KratosForge/pull/871)
- **Story issues:** [#868](https://github.com/KratosDataGod/KratosForge/issues/868)
- **Acceptance criteria:**
    - As defined in ORCH-002 by its owning thread

### PB-037

**Local DAG execution engine** · P3 (score 1.35) · XL · confidence Low · Accepted

- **Problem:** Pipelines run only in Fabric.
- **Proposed capability:** Owned and specified by the Local Fabric pipeline execution thread (ORCH-003; ORCH-004..010 follow in that thread).
- **Current-state evidence:** Metadata runs order entities by dependency; no general DAG runner; No implementation review; checked against product data and main in the acceptance batch of 2026-10-28 (product-management/in-review-acceptance-batch.html): Accepted with Documented Limitations (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4); limitations: Copy data runs only in Fabric until P5. Steps that read or write OneLake use capacity.
- **Inventory features:** [FEAT-ORC-08](FEATURE_INVENTORY.md#feat-orc-08)
- **Dependencies:** [PB-035](#pb-035), [PB-036](#pb-036)
- **Owner:** Local Fabric pipeline execution (ORCH-003, ORCH-004..010)
- **Pull requests:** [#218](https://github.com/KratosDataGod/KratosForge/pull/218), [#319](https://github.com/KratosDataGod/KratosForge/pull/319), [#559](https://github.com/KratosDataGod/KratosForge/pull/559), [#563](https://github.com/KratosDataGod/KratosForge/pull/563), [#568](https://github.com/KratosDataGod/KratosForge/pull/568), [#578](https://github.com/KratosDataGod/KratosForge/pull/578), [#579](https://github.com/KratosDataGod/KratosForge/pull/579), [#791](https://github.com/KratosDataGod/KratosForge/pull/791)
- **Acceptance criteria:**
    - As defined in ORCH-003 by its owning thread

### PB-044

**Agent queue pruning and dead-job requeue** · P2 (score 2.3) · S · confidence High · Accepted

- **Problem:** The agent's SQLite queue is never pruned and a dead job can't be revived without editing the database.
- **Proposed capability:** Retention for finished jobs and an agent requeue command.
- **Current-state evidence:** jobqueue.py has no prune (inference from code reading); agent CLI has no requeue; PR #689: finished agent jobs are pruned and `agent requeue` revives a dead job; Implementation review docs/product/reviews/PB-044-689.md: Accepted with Documented Limitations (Chris 'I accept all 4', 2026-10-26, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpREfN9JPWyocAdUEQR2tzj9); limitations: the package's manual requeue check and a live agent run are still to do
- **Inventory features:** [FEAT-ORC-02](FEATURE_INVENTORY.md#feat-orc-02)
- **Dependencies:** none
- **Owner:** Master product (unassigned)
- **Pull requests:** [#689](https://github.com/KratosDataGod/KratosForge/pull/689)
- **Implementation package:** [pr-ready/PB-044.md](pr-ready/PB-044.md)
- **Acceptance criteria:**
    - Finished jobs older than the retention are removed on start
    - fabric-desktop agent requeue <id> moves a dead job back to queued
    - Tests cover both

### PB-045

**Shared queue for several workers** · P3 (score 0.9) · L · confidence Low · In review

- **Problem:** Each agent has its own queue, so work can't spread across machines.
- **Proposed capability:** A worker registry and shared claims, as the Workload management program plans.
- **Current-state evidence:** jobqueue.py per machine; docs/agent.md; acceptance batch of 2026-10-28 (product-management/in-review-acceptance-batch.html): kept In review (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4): The mechanism that makes it safe hasn't been tried on a tenant yet.; acceptance batch 2 of 2026-10-29 (product-management/in-review-acceptance-batch-2.html): kept In review (Chris chose 'Accept all as recommended' on the decision card, 2026-10-30, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpKnZygxfq5AA9Hd5e1zTC9b): Waits on the live check (your PB-002/PB-003 yes).
- **Inventory features:** [FEAT-ORC-06](FEATURE_INVENTORY.md#feat-orc-06)
- **Dependencies:** [PB-003](#pb-003)
- **Owner:** Workload management (FD-124)
- **Pull requests:** [#146](https://github.com/KratosDataGod/KratosForge/pull/146), [#149](https://github.com/KratosDataGod/KratosForge/pull/149)
- **Acceptance criteria:**
    - As defined in FD-124 by its owning program

### PB-048

**Schedule editing and safer schedule removal** · P2 (score 1.84) · M · confidence High · Accepted

- **Problem:** Schedules can't be edited, removal has no confirmation, and only 12 time zones are offered.
- **Proposed capability:** Edit time and frequency, confirm removal, full time-zone list; PC schedules that run when the user is signed out.
- **Current-state evidence:** schedules.js; local_schedules.py; A schedule's time edits in place, Remove asks with a danger confirm, every Windows time zone (time_zones.py) on main at b19b7456; No implementation review; checked against product data and main in acceptance batch 2 of 2026-10-29 (product-management/in-review-acceptance-batch-2.html): Accepted with Documented Limitations (Chris chose 'Accept all as recommended' on the decision card, 2026-10-30, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpKnZygxfq5AA9Hd5e1zTC9b); limitations: Editing a Fabric schedule in place hasn't been tried on a tenant.
- **Inventory features:** [FEAT-FAB-08](FEATURE_INVENTORY.md#feat-fab-08), [FEAT-ORC-01](FEATURE_INVENTORY.md#feat-orc-01)
- **Dependencies:** none
- **Owner:** Master product (unassigned)
- **Pull requests:** [#881](https://github.com/KratosDataGod/KratosForge/pull/881)
- **Story issues:** [#869](https://github.com/KratosDataGod/KratosForge/issues/869)
- **Acceptance criteria:**
    - A schedule's time can be changed in place
    - Remove asks for confirmation
    - Every Windows time zone can be picked

### PB-049

**Run the pipeline mailbox once on a tenant** · P2 (score 2.21) · S · confidence Medium · In progress

- **Problem:** The pipeline-to-agent mailbox has only run against fakes and records free-text submitters.
- **Proposed capability:** One live run per the live test, and the writer's identity recorded.
- **Current-state evidence:** mailbox.py tests use fakes; docs/enterprise-roadmap.md still calls it unbuilt; Acceptance changed 2026-10-28 from 'submitted_by comes from the token': OneLake reports every writer as $superuser (OneLake API parity), so draft #931 (issue #920) checks the named pipeline run instead. Needs Chris's acceptance of the change; Part 1 merged (#931): the agent checks the named pipeline run, and [pipeline] require_run (Preview) refuses requests it can't check on main at 188d5a93. Part 2 is the live run (with PB-002's yes); One pipeline run vouches for one request (#1059, issue #969) on main at 02118fa4; acceptance batch 2 of 2026-10-29 (product-management/in-review-acceptance-batch-2.html): changed criterion 2 (check the named run against Fabric's job history) approved (Chris chose 'Accept all as recommended' on the decision card, 2026-10-30, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpKnZygxfq5AA9Hd5e1zTC9b); stays In progress for the live run
- **Inventory features:** [FEAT-ORC-04](FEATURE_INVENTORY.md#feat-orc-04)
- **Dependencies:** [PB-002](#pb-002)
- **Owner:** Staged roadmap (Scale plan capability 7)
- **Pull requests:** [#931](https://github.com/KratosDataGod/KratosForge/pull/931), [#1059](https://github.com/KratosDataGod/KratosForge/pull/1059)
- **Story issues:** [#920](https://github.com/KratosDataGod/KratosForge/issues/920), [#969](https://github.com/KratosDataGod/KratosForge/issues/969)
- **Acceptance criteria:**
    - A Fabric pipeline request runs once on an agent and reports back on the maintainer's tenant
    - The agent checks the pipeline run a request names against Fabric's job history and records whether it checked out

### PB-107

**Data Factory program** · P3 (score 1.66) · XL · confidence Medium · In progress

- **Problem:** Pipelines, copy jobs, dataflows and connections are authored and watched only in the Fabric portal.
- **Proposed capability:** Owned and specified by the 'Build full Data Factory experience' thread (DF-1..10), built on the local pipeline runner's P-stages; not restated here.
- **Current-state evidence:** Chris's 'Go' 2026-10-24 18:16Z; DF-1 engine merged as #640; Mockup waits on Chris
- **Inventory features:** [FEAT-FAB-10](FEATURE_INVENTORY.md#feat-fab-10), [FEAT-ORC-10](FEATURE_INVENTORY.md#feat-orc-10), [FEAT-ORC-08](FEATURE_INVENTORY.md#feat-orc-08)
- **Dependencies:** [PB-037](#pb-037), Chris reacts to the Data Factory mockup before any UI, Plan decision 3 (real exports)
- **Owner:** Build full Data Factory experience (DF-1..10, Pipelines P5-P11)
- **Merged duplicates:** Absorbs PB-030 (Copy job scope after D2) and the pipeline-authoring half of PB-035
- **Pull requests:** [#640](https://github.com/KratosDataGod/KratosForge/pull/640), [#653](https://github.com/KratosDataGod/KratosForge/pull/653), [#665](https://github.com/KratosDataGod/KratosForge/pull/665), [#666](https://github.com/KratosDataGod/KratosForge/pull/666), [#667](https://github.com/KratosDataGod/KratosForge/pull/667), [#668](https://github.com/KratosDataGod/KratosForge/pull/668), [#669](https://github.com/KratosDataGod/KratosForge/pull/669), [#700](https://github.com/KratosDataGod/KratosForge/pull/700), [#709](https://github.com/KratosDataGod/KratosForge/pull/709), [#714](https://github.com/KratosDataGod/KratosForge/pull/714), [#724](https://github.com/KratosDataGod/KratosForge/pull/724), [#732](https://github.com/KratosDataGod/KratosForge/pull/732), [#735](https://github.com/KratosDataGod/KratosForge/pull/735), [#736](https://github.com/KratosDataGod/KratosForge/pull/736), [#737](https://github.com/KratosDataGod/KratosForge/pull/737), [#739](https://github.com/KratosDataGod/KratosForge/pull/739), [#740](https://github.com/KratosDataGod/KratosForge/pull/740), [#741](https://github.com/KratosDataGod/KratosForge/pull/741), [#746](https://github.com/KratosDataGod/KratosForge/pull/746), [#1222](https://github.com/KratosDataGod/KratosForge/pull/1222), [#1250](https://github.com/KratosDataGod/KratosForge/pull/1250), [#1251](https://github.com/KratosDataGod/KratosForge/pull/1251), [#1256](https://github.com/KratosDataGod/KratosForge/pull/1256), [#1333](https://github.com/KratosDataGod/KratosForge/pull/1333)
- **Acceptance criteria:**
    - As defined in DF-1..10 by its owning thread

## Fabric Integration and Deployment

| Id | Item | Priority | Owner | Effort | State |
| --- | --- | --- | --- | --- | --- |
| [PB-018](#pb-018) | Publish matches Fabric items by id, not display name | P2 | Master product (unassigned) | S | Accepted |
| [PB-040](#pb-040) | Fabric workspace Git integration | P2 | Git-native development | L | Proposed |
| [PB-041](#pb-041) | Deployment preview and item deploy | P2 | Git-native development | L | Proposed |
| [PB-042](#pb-042) | Release rollback and the committed-revision rule | P2 | Staged roadmap | M | Accepted |
| [PB-043](#pb-043) | Cancel a Fabric run from the app | P2 | Master product (unassigned) | S | Accepted |
| [PB-051](#pb-051) | Fabric REST client resilience | P1 | Codebase audit and hardening | S | Accepted |
| [PB-110](#pb-110) | Semantic model interchange (Apache Ossie) | P3 | TMDL and OSI semantic model conversion | M | In progress |
| [PB-116](#pb-116) | Fabric Spark interactive sessions (Livy) as a Preview engine | P2 | Fabric and Databricks unified development | M | In review |
| [PB-121](#pb-121) | Power BI embedding (Embed Studio) | P3 | Power BI embedding (PBE) | L | In progress |

### PB-018

**Publish matches Fabric items by id, not display name** · P2 (score 2.16) · S · confidence Medium · Accepted

- **Problem:** Publishing finds the Fabric notebook by display name, so a rename in either place makes a duplicate.
- **Proposed capability:** Record the Fabric item id on first publish and update that item afterwards.
- **Current-state evidence:** fabric_api.py save_notebook matches an existing notebook by displayName; workload_service.py _record_notebook_item already records the item id after publish, but publish doesn't use it; PR #685: publish updates the Fabric notebook it published before, even after a rename; Implementation review docs/product/reviews/PB-018-685.md: Accepted with Documented Limitations (Chris 'I accept all 4', 2026-10-26, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpREfN9JPWyocAdUEQR2tzj9); limitations: publish after a rename not yet run on a tenant
- **Inventory features:** [FEAT-FAB-06](FEATURE_INVENTORY.md#feat-fab-06)
- **Dependencies:** none
- **Owner:** Master product (unassigned)
- **Pull requests:** [#685](https://github.com/KratosDataGod/KratosForge/pull/685)
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

**Release rollback and the committed-revision rule** · P2 (score 1.95) · M · confidence Medium · Accepted

- **Problem:** A bad promote can be undone only table by table, and Test/Prod can be promoted from uncommitted files.
- **Proposed capability:** Release-level rollback from meta.deployments and decision D1 enforced.
- **Current-state evidence:** meta/release.py; docs/scale-and-operations-plan.md D1; PR #707 (D1): Test and Prod in OneLake take only files a pushed commit holds; rollback is still open; Roll back... on a Test or Prod release card restores a kept release and records it as a new release (meta/promote.py rollback, #508); D1 enforced by #707 on main at b19b7456; No implementation review; checked against product data and main in acceptance batch 2 of 2026-10-29 (product-management/in-review-acceptance-batch-2.html): Accepted with Documented Limitations (Chris chose 'Accept all as recommended' on the decision card, 2026-10-30, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpKnZygxfq5AA9Hd5e1zTC9b); limitations: Only releases that kept their files can be rolled back. Tables keep their rows until their next run. Not run on a tenant.
- **Inventory features:** [FEAT-META-09](FEATURE_INVENTORY.md#feat-meta-09), [FEAT-META-10](FEATURE_INVENTORY.md#feat-meta-10)
- **Dependencies:** Product-owner decision D1, Overlaps HD-023 promote a named commit (Codebase audit and hardening backlog, PB-101)
- **Owner:** Staged roadmap (FD-121)
- **Pull requests:** [#113](https://github.com/KratosDataGod/KratosForge/pull/113), [#508](https://github.com/KratosDataGod/KratosForge/pull/508), [#707](https://github.com/KratosDataGod/KratosForge/pull/707)
- **Acceptance criteria:**
    - D1 answered
    - Rolling back a release restores the previous configuration in every target
    - Rollback is recorded as its own release

### PB-043

**Cancel a Fabric run from the app** · P2 (score 2.45) · S · confidence High · Accepted

- **Problem:** A Fabric run started here can be cancelled only as a workload action in Monitor > KratosForge > Operations > Workloads (Preview); Monitor > Runs and the activity tray, where people watch the run, offer no Cancel.
- **Proposed capability:** A Cancel action on running Fabric jobs in Monitor > Runs and the tray, reusing cancel_fabric_run.
- **Current-state evidence:** cloud_service.py cancel_fabric_run is called only from workload_service.py workload_act; app/ui/workloads.js offers cancel through workload actions; Re-checked 2026-10-25: the Fabric step card cancels a run; Monitor > Runs and the tray still can't; Cancel in Monitor > Runs and the tray (Preview id fabric-cancel), tested in test_cloud.py and fabric-cancel.test.cjs on main at b19b7456; No implementation review; checked against product data and main in acceptance batch 2 of 2026-10-29 (product-management/in-review-acceptance-batch-2.html): Accepted with Documented Limitations (Chris chose 'Accept all as recommended' on the decision card, 2026-10-30, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpKnZygxfq5AA9Hd5e1zTC9b); limitations: Preview. Not yet tried against a real Fabric run.
- **Inventory features:** [FEAT-FAB-07](FEATURE_INVENTORY.md#feat-fab-07)
- **Dependencies:** none
- **Owner:** Master product (unassigned)
- **Pull requests:** [#875](https://github.com/KratosDataGod/KratosForge/pull/875), [#972](https://github.com/KratosDataGod/KratosForge/pull/972)
- **Story issues:** [#850](https://github.com/KratosDataGod/KratosForge/issues/850)
- **Implementation package:** [pr-ready/PB-043.md](pr-ready/PB-043.md)
- **Acceptance criteria:**
    - Cancel in Monitor > Runs and the tray stops the Fabric job and the run shows Cancelled
    - A test covers cancel_fabric_run

### PB-051

**Fabric REST client resilience** · P1 (score 2.16) · S · confidence High · Accepted

- **Problem:** The client retries only 429s; a 5xx or expired token fails the action, and long-running operations give up after 300 seconds.
- **Proposed capability:** Bounded retries on safe 5xx for idempotent calls, one token refresh on 401, configurable LRO timeout.
- **Current-state evidence:** fabric_api.py retry handles 429 only (code reading); send_with_retry retries safe methods on 5xx and 429; a 401 gets one new token and one resend (#923); LRO poll_timeout is a FabricClient setting on main at b19b7456; No implementation review; checked against product data and main in acceptance batch 2 of 2026-10-29 (product-management/in-review-acceptance-batch-2.html): Accepted (Chris chose 'Accept all as recommended' on the decision card, 2026-10-30, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpKnZygxfq5AA9Hd5e1zTC9b)
- **Inventory features:** [FEAT-FAB-01](FEATURE_INVENTORY.md#feat-fab-01)
- **Dependencies:** Overlaps HD-039 honour Retry-After; HD-051 shared HTTP retry; HD-035 bounded status polling (Prove) (Codebase audit and hardening backlog, PB-101)
- **Owner:** Codebase audit and hardening (HD-039/HD-051)
- **Mandatory (required):** Unsafe or missing retries are a reliability defect on every Fabric call; AGENTS.md asks reviewers to flag unsafe retries.
- **Pull requests:** [#923](https://github.com/KratosDataGod/KratosForge/pull/923), [#937](https://github.com/KratosDataGod/KratosForge/pull/937), [#939](https://github.com/KratosDataGod/KratosForge/pull/939), [#944](https://github.com/KratosDataGod/KratosForge/pull/944), [#1055](https://github.com/KratosDataGod/KratosForge/pull/1055)
- **Story issues:** [#863](https://github.com/KratosDataGod/KratosForge/issues/863)
- **Implementation package:** [pr-ready/PB-051.md](pr-ready/PB-051.md)
- **Acceptance criteria:**
    - GETs retry on 500/502/503/504 with backoff; writes don't retry unless idempotent
    - A 401 refreshes once and retries
    - No write is retried in a way that could duplicate an item (test)

### PB-110

**Semantic model interchange (Apache Ossie)** · P3 (score 1.65) · M · confidence Medium · In progress

- **Problem:** Semantic models are locked in TMDL/TMSL and can't move to or from other semantic layers.
- **Proposed capability:** Owned by the 'TMDL and OSI semantic model conversion' thread: engine merged (#599); a Deploy > Semantic model card waits on the mockup.
- **Current-state evidence:** #599 merged, vendored converter at 82b9eff
- **Inventory features:** [FEAT-META-19](FEATURE_INVENTORY.md#feat-meta-19), [FEAT-META-13](FEATURE_INVENTORY.md#feat-meta-13)
- **Dependencies:** Chris reacts to the mockup, Chris decides whether Import may create new models
- **Owner:** TMDL and OSI semantic model conversion (model_ossie_*)
- **Pull requests:** [#599](https://github.com/KratosDataGod/KratosForge/pull/599), [#681](https://github.com/KratosDataGod/KratosForge/pull/681), [#716](https://github.com/KratosDataGod/KratosForge/pull/716), [#758](https://github.com/KratosDataGod/KratosForge/pull/758), [#777](https://github.com/KratosDataGod/KratosForge/pull/777)
- **Acceptance criteria:**
    - Export, preview and import from the Semantic model tab with a confirm; Save TMDL to Git

### PB-116

**Fabric Spark interactive sessions (Livy) as a Preview engine** · P2 (score 1.8) · M · confidence Medium · In review

- **Problem:** Notebooks that need real Spark can only run as a whole Fabric job; there is no interactive Fabric Spark session from the app.
- **Proposed capability:** A Preview engine that runs cells in a Fabric Spark session through the Livy API, with remote-compute confirmation.
- **Current-state evidence:** Built work is a saved patch in the project files (databricks/fabric-spark-wip), to restart from main; Story KF-FABRIC-SPARK in the orchestration plan (#864); Livy sessions run as a Preview engine behind remote compute confirmation, tested with Fabric faked (test_fabric_spark.py, 34 tests) on main at 02118fa4. One recorded live run on 2026-10-29 ran 10 queries in a Fabric Spark session (docs/platforms.md)
- **Inventory features:** [FEAT-EXT-05](FEATURE_INVENTORY.md#feat-ext-05), [FEAT-NB-07](FEATURE_INVENTORY.md#feat-nb-07)
- **Dependencies:** none
- **Owner:** Fabric and Databricks unified development (KF-FABRIC-SPARK)
- **Pull requests:** [#891](https://github.com/KratosDataGod/KratosForge/pull/891), [#1133](https://github.com/KratosDataGod/KratosForge/pull/1133)
- **Story issues:** [#852](https://github.com/KratosDataGod/KratosForge/issues/852)
- **Acceptance criteria:**
    - Every remote session start goes through the remote compute confirmation and risk check
    - Tests with Fabric faked cover start, run, cancel and stop
    - Docs say it uses Fabric capacity and is Preview

### PB-121

**Power BI embedding (Embed Studio)** · P3 (score 1.56) · L · confidence Medium · In progress

- **Problem:** Reports and semantic models can't be viewed or tested inside the app.
- **Proposed capability:** An isolated Power BI embed host frame with its own policy, tokens kept in memory, and no service principal secrets.
- **Current-state evidence:** PBE-02 design proposed (docs/architecture/power-bi-embedding.md); PBE-03 host frame and PBE-04 engine calls merged; no screen yet on main at 02118fa4
- **Inventory features:** [FEAT-FAB-05](FEATURE_INVENTORY.md#feat-fab-05)
- **Dependencies:** none
- **Owner:** Power BI embedding (PBE) (PBE-02..04)
- **Pull requests:** [#1126](https://github.com/KratosDataGod/KratosForge/pull/1126), [#1134](https://github.com/KratosDataGod/KratosForge/pull/1134), [#1135](https://github.com/KratosDataGod/KratosForge/pull/1135)
- **Acceptance criteria:**
    - Owned and specified by the Power BI embedding program (PBE)

## Enterprise Shared Services

| Id | Item | Priority | Owner | Effort | State |
| --- | --- | --- | --- | --- | --- |
| [PB-052](#pb-052) | Verify the central platform on a tenant | P1 | Master product (unassigned) | M | In progress |
| [PB-053](#pb-053) | Scheduled shortcut health check with alerts | P2 | Master product (unassigned) | S | Accepted |
| [PB-054](#pb-054) | Screens for platform revoke and source list | P2 | Master product (unassigned) | S | Accepted |
| [PB-105](#pb-105) | Simplified incident management | P3 | Simplified incident management | L | In progress |

### PB-052

**Verify the central platform on a tenant** · P1 (score 1.8) · M · confidence Medium · In progress

- **Problem:** SQL_Platform T-SQL runs only on SQLite in tests; whether User Data Functions expose the executing user is unverified; domain import shape is unverified.
- **Proposed capability:** A live check of setup, join, key issue, requests and domain import on a non-production tenant before anyone moves real keys.
- **Current-state evidence:** docs/platform.md lists the unverified points; central/setup.py setup() and join() have no tests; Smoke flows 11 to 16 merged (#919); upgrade, retry and role proofs with Fabric faked (#1036, #1038, #1041, #1045) on main at 188d5a93; A test runs every role write in the role matrix and requires an audit row naming the caller; three unaudited writes are now audited (#1200) on main at f3750b9d; Requests record what their approval made, declines and withdrawals say what stays in Fabric, deletes refuse while something still uses the domain, landing zone or source, and changes anyone may make are on the audit trail (#1205 to #1354, with Fabric faked) on main at b3c5b223; not run on a tenant
- **Inventory features:** [FEAT-MESH-01](FEATURE_INVENTORY.md#feat-mesh-01), [FEAT-MESH-02](FEATURE_INVENTORY.md#feat-mesh-02), [FEAT-MESH-04](FEATURE_INVENTORY.md#feat-mesh-04)
- **Dependencies:** [PB-002](#pb-002), Direction changes (Chris's unit testing, 2026-10-18, item 24 and 25): shared landing workspace and a keys database in a dedicated workspace change what the tenant check sets up
- **Owner:** Master product (unassigned)
- **Mandatory (required):** Central business keys are shared data; a defect would corrupt keys across every domain.
- **Pull requests:** [#919](https://github.com/KratosDataGod/KratosForge/pull/919), [#1036](https://github.com/KratosDataGod/KratosForge/pull/1036), [#1038](https://github.com/KratosDataGod/KratosForge/pull/1038), [#1041](https://github.com/KratosDataGod/KratosForge/pull/1041), [#1045](https://github.com/KratosDataGod/KratosForge/pull/1045), [#1200](https://github.com/KratosDataGod/KratosForge/pull/1200), [#1205](https://github.com/KratosDataGod/KratosForge/pull/1205), [#1213](https://github.com/KratosDataGod/KratosForge/pull/1213), [#1239](https://github.com/KratosDataGod/KratosForge/pull/1239), [#1241](https://github.com/KratosDataGod/KratosForge/pull/1241), [#1243](https://github.com/KratosDataGod/KratosForge/pull/1243), [#1244](https://github.com/KratosDataGod/KratosForge/pull/1244), [#1255](https://github.com/KratosDataGod/KratosForge/pull/1255), [#1259](https://github.com/KratosDataGod/KratosForge/pull/1259), [#1265](https://github.com/KratosDataGod/KratosForge/pull/1265), [#1267](https://github.com/KratosDataGod/KratosForge/pull/1267), [#1308](https://github.com/KratosDataGod/KratosForge/pull/1308), [#1309](https://github.com/KratosDataGod/KratosForge/pull/1309), [#1322](https://github.com/KratosDataGod/KratosForge/pull/1322), [#1328](https://github.com/KratosDataGod/KratosForge/pull/1328), [#1335](https://github.com/KratosDataGod/KratosForge/pull/1335), [#1348](https://github.com/KratosDataGod/KratosForge/pull/1348), [#1351](https://github.com/KratosDataGod/KratosForge/pull/1351), [#1353](https://github.com/KratosDataGod/KratosForge/pull/1353), [#1354](https://github.com/KratosDataGod/KratosForge/pull/1354)
- **Story issues:** [#909](https://github.com/KratosDataGod/KratosForge/issues/909)
- **Acceptance criteria:**
    - Setup and join run on the maintainer's tenant and are recorded
    - Two concurrent key requests never share a key on Fabric SQL database
    - UDF caller identity confirmed or the design changed

### PB-053

**Scheduled shortcut health check with alerts** · P2 (score 2.12) · S · confidence Medium · Accepted

- **Problem:** Broken shortcuts are found only when someone opens the page.
- **Proposed capability:** A scheduled check (app or agent) that posts to Teams once per break.
- **Current-state evidence:** docs/shortcuts.md lists it as not built; [shortcut_health] in agent.toml runs the check on a schedule and posts each break once through platform_notify's ledger (#902) on main at b19b7456; No implementation review; checked against product data and main in acceptance batch 2 of 2026-10-29 (product-management/in-review-acceptance-batch-2.html): Accepted with Documented Limitations (Chris chose 'Accept all as recommended' on the decision card, 2026-10-30, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpKnZygxfq5AA9Hd5e1zTC9b); limitations: Runs in the agent only, not the desktop app, and only on the agent's own environments. Preview, not run on a tenant.
- **Inventory features:** [FEAT-ONE-03](FEATURE_INVENTORY.md#feat-one-03)
- **Dependencies:** none
- **Owner:** Master product (unassigned)
- **Pull requests:** [#902](https://github.com/KratosDataGod/KratosForge/pull/902)
- **Story issues:** [#857](https://github.com/KratosDataGod/KratosForge/issues/857)
- **Acceptance criteria:**
    - The agent runs the check on a schedule
    - A new break posts once

### PB-054

**Screens for platform revoke and source list** · P2 (score 2.07) · S · confidence High · Accepted

- **Problem:** Platform admins can't revoke a grant or list sources from the app though the engine supports both.
- **Proposed capability:** Revoke in Platform > Access and a sources list.
- **Current-state evidence:** platform_revoke and platform_sources have no caller in app/ui; PR #694: a product's page takes a project's access away (Preview); the source list screen is not checked here; Implementation review docs/product/reviews/PB-054-694.md: Accepted with Documented Limitations (Chris 'I accept all 4', 2026-10-26, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpREfN9JPWyocAdUEQR2tzj9); limitations: the sources list isn't built and revoke hasn't run against a tenant's UDF_Platform
- **Inventory features:** [FEAT-MESH-01](FEATURE_INVENTORY.md#feat-mesh-01)
- **Dependencies:** none
- **Owner:** Master product (unassigned)
- **Pull requests:** [#694](https://github.com/KratosDataGod/KratosForge/pull/694)
- **Acceptance criteria:**
    - An admin revokes a grant with confirmation and it is audited

### PB-105

**Simplified incident management** · P3 (score 1.43) · L · confidence Low · In progress

- **Problem:** Incidents open automatically from workload health, but there is no simple way to triage them across teams or hand them to the service desk an organization already uses; ServiceNow-style tools are complex.
- **Proposed capability:** Owned and specified by the Simplified incident management thread, from Chris's unit testing on 2026-10-18 (item 33): detect, triage and hand off, either stored in a central lakehouse or handed to ServiceNow or Jira. Not restated here.
- **Current-state evidence:** Chris's unit testing, 2026-10-18, item 33; Incidents today: sidecar/fabric_desktop/workloads/incidents.py (PRs #74, #79, #125, #127)
- **Inventory features:** [FEAT-OPS-02](FEATURE_INVENTORY.md#feat-ops-02)
- **Dependencies:** none
- **Owner:** Simplified incident management
- **Merged duplicates:** Builds on PB-047 incident screens (Workload management)
- **Acceptance criteria:**
    - As defined by its owning thread

## Data Governance and Quality

| Id | Item | Priority | Owner | Effort | State |
| --- | --- | --- | --- | --- | --- |
| [PB-056](#pb-056) | Enterprise policy registry | P2 | Enterprise governance foundation | M | Accepted |
| [PB-057](#pb-057) | Offline policy validation | P1 | Enterprise governance foundation | M | Accepted |
| [PB-059](#pb-059) | Governance exception requests and approvals | P2 | Enterprise governance foundation | M | Accepted |
| [PB-060](#pb-060) | Sensitive data classification that complements Purview | P2 | Enterprise readiness | L | Proposed |
| [PB-081](#pb-081) | Column lineage view | P1 | Staged roadmap | M | Accepted |
| [PB-082](#pb-082) | Versioned data contracts with consumer declarations | P2 | Staged roadmap | M | Accepted |
| [PB-083](#pb-083) | Endorsed items scope in the catalog | P2 | Master product (unassigned) | S | Accepted |
| [PB-084](#pb-084) | One late-table alert, not two | P2 | Workload management | S | In review |
| [PB-108](#pb-108) | Best-practice checks and governance templates | P1 | Enterprise governance foundation | L | Accepted |
| [PB-111](#pb-111) | Check now covers SQL scripts and scheduled notebooks | P2 | Enterprise governance foundation | S | Accepted |
| [PB-112](#pb-112) | Semantic model best-practice checks in governance policies | P2 | Best Practice Analyzer | M | Accepted |
| [PB-115](#pb-115) | Run the Fabric Best Practice Analyzer live | P2 | Best Practice Analyzer | M | Proposed |

### PB-056

**Enterprise policy registry** · P2 (score 2.38) · M · confidence Medium · Accepted

- **Problem:** Policies (naming, schema, quality, classification, ownership) live in project files with no central, versioned source.
- **Proposed capability:** Owned and specified by the Enterprise governance foundation thread (GOV-001).
- **Current-state evidence:** No policy registry in the repo; PR #119 merged 2026-10-17; awaiting implementation review; No implementation review; checked against product data and main in the acceptance batch of 2026-10-28 (product-management/in-review-acceptance-batch.html): Accepted (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4)
- **Inventory features:** [FEAT-GOV-05](FEATURE_INVENTORY.md#feat-gov-05)
- **Dependencies:** Overlaps HD-031 label Block as not enforced (governance) (Codebase audit and hardening backlog, PB-101)
- **Owner:** Enterprise governance foundation (GOV-001)
- **Pull requests:** [#119](https://github.com/KratosDataGod/KratosForge/pull/119)
- **Acceptance criteria:**
    - As defined in GOV-001 by its owning thread

### PB-057

**Offline policy validation** · P1 (score 2.8) · M · confidence Medium · Accepted

- **Problem:** Policy checks need Fabric today or don't exist.
- **Proposed capability:** Owned and specified by the Enterprise governance foundation thread (GOV-002; GOV-003..005 follow there).
- **Current-state evidence:** No offline validator; GOV-002 built: hash-checked policy packages and offline Check (#882); GOV-003 enforcement is issue #903 on main at b19b7456; No implementation review; checked against product data and main in acceptance batch 2 of 2026-10-29 (product-management/in-review-acceptance-batch-2.html): Accepted with Documented Limitations (Chris chose 'Accept all as recommended' on the decision card, 2026-10-30, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpKnZygxfq5AA9Hd5e1zTC9b); limitations: Packages are hash-checked, not signed. An offline check can't read catalog text, product terms, endorsement or run logs, so those rules say unknown. No screen to pick a package.
- **Inventory features:** [FEAT-GOV-05](FEATURE_INVENTORY.md#feat-gov-05)
- **Dependencies:** [PB-056](#pb-056)
- **Owner:** Enterprise governance foundation (GOV-002, GOV-003..005)
- **Pull requests:** [#882](https://github.com/KratosDataGod/KratosForge/pull/882), [#924](https://github.com/KratosDataGod/KratosForge/pull/924)
- **Story issues:** [#855](https://github.com/KratosDataGod/KratosForge/issues/855), [#903](https://github.com/KratosDataGod/KratosForge/issues/903)
- **Acceptance criteria:**
    - As defined in GOV-002 by its owning thread

### PB-059

**Governance exception requests and approvals** · P2 (score 1.96) · M · confidence Medium · Accepted

- **Problem:** Exceptions are lines in a project file with no request, approval or history.
- **Proposed capability:** Request, approve and expire exceptions with history, aligned with the policy registry.
- **Current-state evidence:** meta/project.py exceptions.toml; PR #603 shows approved exceptions and warns before they end; #605 adds exception requests on the Governance tab; Implementation review docs/product/reviews/PB-059-605.md: FD-129 narrowed to policy exceptions and Accepted with Documented Limitations (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4); limitation: exceptions.toml entries have no request, approval or audit entry, and their approver is free text
- **Inventory features:** [FEAT-GOV-03](FEATURE_INVENTORY.md#feat-gov-03)
- **Dependencies:** [PB-056](#pb-056)
- **Owner:** Enterprise governance foundation (FD-129)
- **Pull requests:** [#603](https://github.com/KratosDataGod/KratosForge/pull/603), [#605](https://github.com/KratosDataGod/KratosForge/pull/605), [#838](https://github.com/KratosDataGod/KratosForge/pull/838)
- **Acceptance criteria:**
    - As defined in FD-129 by the Enterprise governance foundation thread (moved from Enterprise readiness on 2026-10-17), built on the GOV-001 policy registry

### PB-060

**Sensitive data classification that complements Purview** · P2 (score 1.8) · L · confidence Low · Proposed

- **Problem:** Columns carry no classification; a UDF exists with no caller.
- **Proposed capability:** Classification captured in contracts and carried to products, reading and writing Purview labels rather than replacing them.
- **Current-state evidence:** central/udf.py set_classification has no caller
- **Inventory features:** [FEAT-GOV-06](FEATURE_INVENTORY.md#feat-gov-06)
- **Dependencies:** [PB-056](#pb-056), Direction (Chris's unit testing, 2026-10-18, item 27): integrate with Purview, Collibra and other catalogs rather than build catalog features
- **Owner:** Enterprise readiness (FD-131, GOV-00x)
- **Acceptance criteria:**
    - As defined in FD-131 and the governance foundation thread

### PB-081

**Column lineage view** · P1 (score 3.06) · M · confidence High · Accepted

- **Problem:** Column lineage is computed but no screen shows it.
- **Proposed capability:** A lineage view after a mockup, as the staged roadmap plans.
- **Current-state evidence:** dataops.js calls meta_lineage; PR #124 merged 2026-10-17; awaiting implementation review; No implementation review; checked against product data and main in the acceptance batch of 2026-10-28 (product-management/in-review-acceptance-batch.html): Accepted with Documented Limitations (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4); limitations: Columns added by Python code can't be traced.
- **Inventory features:** [FEAT-META-17](FEATURE_INVENTORY.md#feat-meta-17)
- **Dependencies:** none
- **Owner:** Staged roadmap (FD-128)
- **Pull requests:** [#124](https://github.com/KratosDataGod/KratosForge/pull/124)
- **Acceptance criteria:**
    - As defined in FD-128 by its owning program

### PB-082

**Versioned data contracts with consumer declarations** · P2 (score 2.38) · M · confidence Medium · Accepted

- **Problem:** Breaking changes are blocked, but consumers can't declare what they read or see deprecations.
- **Proposed capability:** Consumer declarations and deprecation windows on product ports.
- **Current-state evidence:** meta/mesh.py breaking-change check; PR #701 (FD-127 8.2): port version history, accepted versions on links, deprecation dates with Teams notices, in the engine; Implementation review docs/product/reviews/PB-082-701.md: Accepted with Documented Limitations (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4); limitations: Every finding is fixed (#817, #836).
- **Inventory features:** [FEAT-MESH-05](FEATURE_INVENTORY.md#feat-mesh-05)
- **Dependencies:** none
- **Owner:** Staged roadmap (FD-127)
- **Pull requests:** [#701](https://github.com/KratosDataGod/KratosForge/pull/701), [#836](https://github.com/KratosDataGod/KratosForge/pull/836)
- **Acceptance criteria:**
    - As defined in FD-127 by its owning program

### PB-083

**Endorsed items scope in the catalog** · P2 (score 1.9) · S · confidence High · Accepted

- **Problem:** The Endorsed filter is always unavailable and its reason text points at an embedded catalog that doesn't exist.
- **Proposed capability:** Fix the text now; read endorsements when a supported API is confirmed.
- **Current-state evidence:** service.py endorsements returns available:false; docs/troubleshooting.md; Endorsement badges and Purview term links show on data products (#627, #642); the catalog filter is still missing; The Endorsed filter's reason text and troubleshooting.md say what happens (#889) on main at 188d5a93; No implementation review; checked against product data and main in acceptance batch 2 of 2026-10-29 (product-management/in-review-acceptance-batch-2.html): Accepted (Chris chose 'Accept all as recommended' on the decision card, 2026-10-30, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpKnZygxfq5AA9Hd5e1zTC9b)
- **Inventory features:** [FEAT-ONE-02](FEATURE_INVENTORY.md#feat-one-02)
- **Dependencies:** none
- **Owner:** Master product (unassigned)
- **Pull requests:** [#889](https://github.com/KratosDataGod/KratosForge/pull/889)
- **Story issues:** [#856](https://github.com/KratosDataGod/KratosForge/issues/856)
- **Acceptance criteria:**
    - Reason text and troubleshooting.md describe what actually happens

### PB-084

**One late-table alert, not two** · P2 (score 1.84) · S · confidence High · In review

- **Problem:** Freshness alerts and incidents can both post the same late table to Teams.
- **Proposed capability:** Freshness evaluated as incidents with one routing path.
- **Current-state evidence:** docs/agent.md notes duplicates; meta/freshness.py and workloads/incidents.py; PR #127 merged 2026-10-17; awaiting implementation review; acceptance batch of 2026-10-28 (product-management/in-review-acceptance-batch.html): kept In review (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4): The criterion, one Teams message per late table, isn't met yet.; acceptance batch 2 of 2026-10-29 (product-management/in-review-acceptance-batch-2.html): kept In review (Chris chose 'Accept all as recommended' on the decision card, 2026-10-30, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpKnZygxfq5AA9Hd5e1zTC9b): The criterion isn't met yet.
- **Inventory features:** [FEAT-GOV-01](FEATURE_INVENTORY.md#feat-gov-01), [FEAT-OPS-02](FEATURE_INVENTORY.md#feat-ops-02)
- **Dependencies:** none
- **Owner:** Workload management (FD-123)
- **Pull requests:** [#127](https://github.com/KratosDataGod/KratosForge/pull/127)
- **Acceptance criteria:**
    - A late table produces one Teams message

### PB-108

**Best-practice checks and governance templates** · P1 (score 2.83) · L · confidence Medium · Accepted

- **Problem:** Teams have no shared, editable best-practice rules that check notebooks, SQL and semantic models before they reach Fabric.
- **Proposed capability:** Governance owns templates (policies with Check now, user-added rules, locks, exceptions); the Best Practice Analyzer thread owns the checkers (notebook NB001-012, SQL SQ001-010, semantic model BPA through semantic-link-labs in Fabric).
- **Current-state evidence:** Chris 2026-10-24: 'more on governance and best practices. Allow for people to add their own templates'; Draft PRs #560 (BPA engine), #643 (notebook rules), #648 (templates and Check now), #649 (SQL rules), #651 (RE2 naming checks); Implementation review docs/product/reviews/PB-108-643.md: Accepted with Documented Limitations (Chris 'Go on all', 2026-10-26, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpVYtS9wxoAC4YeAMMvSdTtB); limitations tracked in PB-111 and PB-112
- **Inventory features:** [FEAT-GOV-05](FEATURE_INVENTORY.md#feat-gov-05), [FEAT-GOV-09](FEATURE_INVENTORY.md#feat-gov-09)
- **Dependencies:** [PB-056](#pb-056)
- **Owner:** Enterprise governance foundation (NB001-012, SQ001-010, GOV templates)
- **Pull requests:** [#643](https://github.com/KratosDataGod/KratosForge/pull/643), [#648](https://github.com/KratosDataGod/KratosForge/pull/648), [#649](https://github.com/KratosDataGod/KratosForge/pull/649), [#651](https://github.com/KratosDataGod/KratosForge/pull/651), [#664](https://github.com/KratosDataGod/KratosForge/pull/664), [#671](https://github.com/KratosDataGod/KratosForge/pull/671), [#713](https://github.com/KratosDataGod/KratosForge/pull/713)
- **Acceptance criteria:**
    - Each rule set registers through governance_checks.register and is off unless a policy names it
    - Check now runs offline for notebooks and SQL; model checks say they run in Fabric and use capacity

### PB-111

**Check now covers SQL scripts and scheduled notebooks** · P2 (score 2.43) · S · confidence Medium · Accepted

- **Problem:** Check now has no SQL scripts to give the SQL rules SQ001-SQ010, and checks notebooks as unscheduled, so a policy naming SQ rules or NB004/NB008 always shows not checked.
- **Proposed capability:** Check now collects this PC's SQL scripts (project .sql files and saved queries) for the SQL rules, and marks notebooks that have a schedule so the scheduled-only rules NB004 and NB008 run on them.
- **Current-state evidence:** PB-108 implementation review, findings 2 and 5 (docs/product/reviews/PB-108-643.md); docs/governance.md: Check now has no SQL scripts yet; NB004 and NB008 are for scheduled runs; PR #798: Check now gives the SQL rules this PC's SQL scripts and runs NB004 and NB008 on notebooks with a PC schedule; a Fabric-only schedule isn't read yet; Implementation review docs/product/reviews/PB-111-798.md: Accepted with Documented Limitations (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4); limitations: A notebook run only by a Fabric schedule isn't checked. #844 now covers notebooks run by a pipeline scheduled on this PC (the review's finding 1).
- **Inventory features:** [FEAT-GOV-09](FEATURE_INVENTORY.md#feat-gov-09)
- **Dependencies:** [PB-108](#pb-108)
- **Owner:** Enterprise governance foundation (central/checks_service.py governance_check_now)
- **Pull requests:** [#798](https://github.com/KratosDataGod/KratosForge/pull/798)
- **Acceptance criteria:**
    - A policy naming SQ rules shows pass or findings from this PC's SQL scripts, never not checked when scripts exist
    - NB004 and NB008 run in Check now on notebooks that have a schedule

### PB-112

**Semantic model best-practice checks in governance policies** · P2 (score 1.8) · M · confidence Medium · Accepted

- **Problem:** No semantic model checker is registered, so a policy can't name Best Practice Analyzer rules and Check now can't run them.
- **Proposed capability:** The BPA engine (#560) registers through governance_checks.register with runs_in="fabric"; Check now says those checks run in Fabric and use capacity, and stores their findings with store_fabric_findings.
- **Current-state evidence:** PB-108 implementation review, finding 1 (docs/product/reviews/PB-108-643.md); PR #753 registers SM001-SM007 so a policy can name them; they read the model on this PC, so no capacity is used, unlike the criterion's Fabric run; PR #560 merged: the Fabric BPA run with semantic-link-labs (engine); not yet run live; Implementation review docs/product/reviews/PB-112-753.md: Accepted with Documented Limitations (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4); limitations: The Fabric half of the criterion (BPA rules that need VertiPaq statistics) isn't proven live.
- **Inventory features:** [FEAT-GOV-09](FEATURE_INVENTORY.md#feat-gov-09), [FEAT-META-13](FEATURE_INVENTORY.md#feat-meta-13)
- **Dependencies:** [PB-108](#pb-108), Chris pastes the BPA cell error from the 2026-10-23 live run
- **Owner:** Best Practice Analyzer (#560 model_bpa)
- **Pull requests:** [#560](https://github.com/KratosDataGod/KratosForge/pull/560), [#753](https://github.com/KratosDataGod/KratosForge/pull/753)
- **Acceptance criteria:**
    - A policy can name semantic model rules; Check now says they run in Fabric and use capacity before running them

### PB-115

**Run the Fabric Best Practice Analyzer live** · P2 (score 1.8) · M · confidence Medium · Proposed

- **Problem:** The Fabric half of PB-112 (BPA rules that need VertiPaq statistics, run on the deployed model with a capacity warning) has not run live; #560's engine run failed on 2026-10-23.
- **Proposed capability:** Check now runs the Fabric BPA (#560) on a deployed model after the capacity warning and stores its findings.
- **Current-state evidence:** Split from PB-112 when it was accepted with limitations on 2026-10-28 (PB-112 review, finding 1); PR #560 merged; not yet run live
- **Inventory features:** [FEAT-GOV-09](FEATURE_INVENTORY.md#feat-gov-09), [FEAT-META-13](FEATURE_INVENTORY.md#feat-meta-13)
- **Dependencies:** [PB-112](#pb-112), Chris pastes the BPA cell error from the live run
- **Owner:** Best Practice Analyzer (docs/product/reviews/PB-112-753.md finding 1)
- **Pull requests:** [#560](https://github.com/KratosDataGod/KratosForge/pull/560)
- **Acceptance criteria:**
    - A policy naming a VertiPaq-based BPA rule runs it in Fabric after the capacity warning and shows its findings

## Developer Experience and Productivity

| Id | Item | Priority | Owner | Effort | State |
| --- | --- | --- | --- | --- | --- |
| [PB-034](#pb-034) | Start the notebook wizard from a table | P2 | Prove | S | Proposed |
| [PB-062](#pb-062) | Save a support bundle from Settings | P2 | Enterprise readiness | S | In progress |
| [PB-077](#pb-077) | User and agent docs match the code | P2 | Master product (unassigned) | S | Accepted |
| [PB-078](#pb-078) | Preview tags match the docs | P1 | Master product (unassigned) | S | Accepted |
| [PB-085](#pb-085) | Declare cell dependencies and mark stale on new table versions | P2 | Prove | S | Proposed |
| [PB-086](#pb-086) | Ready to work checklist | P1 | Enterprise readiness | S | Accepted |
| [PB-087](#pb-087) | Clean up downloaded preview files | P2 | Master product (unassigned) | S | Accepted |
| [PB-100](#pb-100) | Fuller What's new dialog | P3 | Fuller What's new dialog | S | Accepted |
| [PB-102](#pb-102) | UX and simplicity program | P1 | UX and simplicity oversight | XL | In progress |
| [PB-113](#pb-113) | Action-first experience program | P2 | Action-first experience | L | In progress |
| [PB-119](#pb-119) | Learn: lessons, labs and guidance modes | P2 | Learning experience (LX) | L | In progress |

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

**Save a support bundle from Settings** · P2 (score 2.3) · S · confidence High · In progress

- **Problem:** The support bundle is CLI only, so most users and help desks can't produce one.
- **Proposed capability:** Settings > Help > Save support bundle with a contents preview, as FD-138 asks.
- **Current-state evidence:** support_bundle.py has no RPC or UI; Smoke: CLI wrote about.json and CONTENTS.txt; Slice 1 merged: about.json records the policy state, secret settings show only whether they are set, and support_bundle.preview() and `support-bundle --preview` list the contents (#1177) on main at b3c5b223. The Settings button follows a mockup
- **Inventory features:** [FEAT-SHL-11](FEATURE_INVENTORY.md#feat-shl-11)
- **Dependencies:** none
- **Owner:** Enterprise readiness (FD-138)
- **Pull requests:** [#1177](https://github.com/KratosDataGod/KratosForge/pull/1177)
- **Acceptance criteria:**
    - The bundle is saved from Settings after showing what it contains
    - Contents match the CLI bundle

### PB-077

**User and agent docs match the code** · P2 (score 2.45) · S · confidence High · Accepted

- **Problem:** Several docs describe behavior the code doesn't have (Report a problem adds the tenant id; workload views read agent status; the mailbox is unbuilt; incidents are a Delta table; %%sql publish).
- **Proposed capability:** Each mismatch fixed in the docs, with a doc-claims checklist added to the review template.
- **Current-state evidence:** See FEATURE_INVENTORY.md limitations for FEAT-SHL-10, FEAT-ORC-04, FEAT-ORC-05, FEAT-OPS-02, FEAT-FAB-06; The package's mismatches corrected (#876) on main at b19b7456; No implementation review; checked against product data and main in acceptance batch 2 of 2026-10-29 (product-management/in-review-acceptance-batch-2.html): Accepted (Chris chose 'Accept all as recommended' on the decision card, 2026-10-30, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpKnZygxfq5AA9Hd5e1zTC9b)
- **Inventory features:** [FEAT-SHL-10](FEATURE_INVENTORY.md#feat-shl-10), [FEAT-ORC-04](FEATURE_INVENTORY.md#feat-orc-04), [FEAT-ORC-05](FEATURE_INVENTORY.md#feat-orc-05), [FEAT-OPS-02](FEATURE_INVENTORY.md#feat-ops-02), [FEAT-FAB-06](FEATURE_INVENTORY.md#feat-fab-06)
- **Dependencies:** Overlaps UX-002 broken help links and a test for every link; UX-019 help on every screen (UX and simplicity backlog, PB-102), Theme (Chris's unit testing, 2026-10-18, items 9, 14, 18, 28, 34): a docs page per area, linked from each page's in-app guidance
- **Owner:** Master product (unassigned)
- **Pull requests:** [#876](https://github.com/KratosDataGod/KratosForge/pull/876)
- **Story issues:** [#859](https://github.com/KratosDataGod/KratosForge/issues/859)
- **Implementation package:** [pr-ready/PB-077.md](pr-ready/PB-077.md)
- **Acceptance criteria:**
    - Every mismatch listed in the package is corrected
    - zensical build --strict passes

### PB-078

**Preview tags match the docs** · P1 (score 2.45) · S · confidence High · Accepted

- **Problem:** Docs call batch_by, release records, who-can-promote, objectives and column lineage Preview, but the app has no Preview id for them, so they can't be hidden by policy.
- **Proposed capability:** A PREVIEW_FEATURES id for each, or the docs changed where the feature is not exposed in the UI.
- **Current-state evidence:** app/ui/features.js PREVIEW_FEATURES has 19 ids; docs/metadata-guide.md Preview labels; Re-checked 2026-10-25: the docs Preview table is the app's list (UX-019, ui.test.cjs); only metadata-guide.md's engine-behavior Preview labels are left; Every docs Preview heading maps to a PREVIEW_FEATURES id or a stated reason, kept by a UI test (#874) on main at b19b7456; No implementation review; checked against product data and main in acceptance batch 2 of 2026-10-29 (product-management/in-review-acceptance-batch-2.html): Accepted (Chris chose 'Accept all as recommended' on the decision card, 2026-10-30, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpKnZygxfq5AA9Hd5e1zTC9b)
- **Inventory features:** [FEAT-SHL-05](FEATURE_INVENTORY.md#feat-shl-05)
- **Dependencies:** Overlaps UX-019 one generated Preview list (UX and simplicity backlog, PB-102)
- **Owner:** Master product (unassigned)
- **Mandatory (required):** AGENTS.md requires untested Fabric integrations to keep Preview labels; enterprises rely on hiding Preview by policy.
- **Pull requests:** [#874](https://github.com/KratosDataGod/KratosForge/pull/874)
- **Story issues:** [#858](https://github.com/KratosDataGod/KratosForge/issues/858)
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

**Ready to work checklist** · P1 (score 2.75) · S · confidence High · Accepted

- **Problem:** First-run failures (no capacity, no workspace access) appear as errors later.
- **Proposed capability:** Built by the enterprise program as FD-136 (PR #94).
- **Current-state evidence:** PR #94 merged; awaiting implementation review; No implementation review; checked against product data and main in the acceptance batch of 2026-10-28 (product-management/in-review-acceptance-batch.html): Accepted (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4)
- **Inventory features:** [FEAT-ADM-04](FEATURE_INVENTORY.md#feat-adm-04)
- **Dependencies:** Overlaps UX-005 one first-run card on Home (Enterprise readiness) (UX and simplicity backlog, PB-102)
- **Owner:** Enterprise readiness (FD-136)
- **Pull requests:** [#94](https://github.com/KratosDataGod/KratosForge/pull/94)
- **Acceptance criteria:**
    - Accepted through the implementation review process

### PB-087

**Clean up downloaded preview files** · P2 (score 1.9) · S · confidence Medium · Accepted

- **Problem:** File previews download whole into the app data folder and are never removed (inference).
- **Proposed capability:** Size limit and cleanup on close.
- **Current-state evidence:** preview_service.py (code reading); Previews download to their own folder, removed once read; 200 MB cap; leftovers go at next start (#885) on main at b19b7456; No implementation review; checked against product data and main in acceptance batch 2 of 2026-10-29 (product-management/in-review-acceptance-batch-2.html): Accepted with Documented Limitations (Chris chose 'Accept all as recommended' on the decision card, 2026-10-30, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpKnZygxfq5AA9Hd5e1zTC9b); limitations: A file over 200 MB isn't previewed at all, rather than previewing its head.
- **Inventory features:** [FEAT-FAB-04](FEATURE_INVENTORY.md#feat-fab-04)
- **Dependencies:** none
- **Owner:** Master product (unassigned)
- **Pull requests:** [#885](https://github.com/KratosDataGod/KratosForge/pull/885)
- **Story issues:** [#877](https://github.com/KratosDataGod/KratosForge/issues/877)
- **Acceptance criteria:**
    - Preview files are removed when the panel closes or on next start
    - Files over a limit preview their head only

### PB-100

**Fuller What's new dialog** · P3 (score 1.65) · S · confidence Medium · Accepted

- **Problem:** The What's new dialog shows only the current build, so people who skip updates miss what changed.
- **Proposed capability:** Owned and specified by the Fuller What's new dialog thread: list the last 30 days of builds, show the 10 newest, and link to the repository. Not restated here.
- **Current-state evidence:** app/ui/settings.js showWhatsNew (What's new menu item and Settings link); PR #132 merged 2026-10-17; awaiting implementation review; No implementation review; checked against product data and main in the acceptance batch of 2026-10-28 (product-management/in-review-acceptance-batch.html): Accepted (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4)
- **Inventory features:** [FEAT-SHL-09](FEATURE_INVENTORY.md#feat-shl-09)
- **Dependencies:** none
- **Owner:** Fuller What's new dialog
- **Pull requests:** [#132](https://github.com/KratosDataGod/KratosForge/pull/132)
- **Acceptance criteria:**
    - As defined by its owning thread

### PB-102

**UX and simplicity program** · P1 (score 2.66) · XL · confidence Medium · In progress

- **Problem:** Features built by separate programs don't yet share navigation, errors, first-run, help or confirmation patterns, and some screens ask for typed ids or claim all is well when they couldn't check.
- **Proposed capability:** Owned and specified by the UX and simplicity oversight thread (UX-001..021, specs in docs/ux/backlog.md, PR #166); not restated here. UX-021 (PR-UX-13, P1) adds Preview tags to untested Fabric writes and absorbs QA-020; UX-018 absorbs QA-019. Items it routes to other programs are built by those owners.
- **Current-state evidence:** UX audit by the oversight thread, 2026-10-18 (PR #166)
- **Inventory features:** [FEAT-SHL-04](FEATURE_INVENTORY.md#feat-shl-04), [FEAT-SHL-06](FEATURE_INVENTORY.md#feat-shl-06)
- **Dependencies:** Theme (Chris's unit testing, 2026-10-18, items 9, 12, 13, 17, 18, 20): best-practice actions and in-app guidance on every page (item 18 routed to this thread)
- **Owner:** UX and simplicity oversight (UX-001..021, PR-UX-13)
- **Merged duplicates:** Overlaps PB-063, PB-072, PB-077, PB-078, PB-086 and PB-099 (noted on each item)
- **Pull requests:** [#684](https://github.com/KratosDataGod/KratosForge/pull/684), [#699](https://github.com/KratosDataGod/KratosForge/pull/699), [#702](https://github.com/KratosDataGod/KratosForge/pull/702), [#703](https://github.com/KratosDataGod/KratosForge/pull/703), [#704](https://github.com/KratosDataGod/KratosForge/pull/704), [#705](https://github.com/KratosDataGod/KratosForge/pull/705), [#719](https://github.com/KratosDataGod/KratosForge/pull/719), [#747](https://github.com/KratosDataGod/KratosForge/pull/747), [#769](https://github.com/KratosDataGod/KratosForge/pull/769), [#801](https://github.com/KratosDataGod/KratosForge/pull/801), [#802](https://github.com/KratosDataGod/KratosForge/pull/802), [#803](https://github.com/KratosDataGod/KratosForge/pull/803), [#804](https://github.com/KratosDataGod/KratosForge/pull/804), [#805](https://github.com/KratosDataGod/KratosForge/pull/805), [#806](https://github.com/KratosDataGod/KratosForge/pull/806), [#814](https://github.com/KratosDataGod/KratosForge/pull/814), [#825](https://github.com/KratosDataGod/KratosForge/pull/825), [#826](https://github.com/KratosDataGod/KratosForge/pull/826), [#827](https://github.com/KratosDataGod/KratosForge/pull/827), [#831](https://github.com/KratosDataGod/KratosForge/pull/831), [#842](https://github.com/KratosDataGod/KratosForge/pull/842), [#843](https://github.com/KratosDataGod/KratosForge/pull/843), [#865](https://github.com/KratosDataGod/KratosForge/pull/865), [#932](https://github.com/KratosDataGod/KratosForge/pull/932), [#943](https://github.com/KratosDataGod/KratosForge/pull/943), [#974](https://github.com/KratosDataGod/KratosForge/pull/974), [#987](https://github.com/KratosDataGod/KratosForge/pull/987), [#988](https://github.com/KratosDataGod/KratosForge/pull/988), [#990](https://github.com/KratosDataGod/KratosForge/pull/990), [#991](https://github.com/KratosDataGod/KratosForge/pull/991), [#998](https://github.com/KratosDataGod/KratosForge/pull/998), [#1088](https://github.com/KratosDataGod/KratosForge/pull/1088), [#1131](https://github.com/KratosDataGod/KratosForge/pull/1131)
- **Story issues:** [#928](https://github.com/KratosDataGod/KratosForge/issues/928), [#929](https://github.com/KratosDataGod/KratosForge/issues/929), [#948](https://github.com/KratosDataGod/KratosForge/issues/948), [#955](https://github.com/KratosDataGod/KratosForge/issues/955), [#956](https://github.com/KratosDataGod/KratosForge/issues/956), [#961](https://github.com/KratosDataGod/KratosForge/issues/961), [#971](https://github.com/KratosDataGod/KratosForge/issues/971)
- **Acceptance criteria:**
    - As defined in UX-001..021 by its owning thread
    - Routed items follow the owner named in docs/ux/backlog.md

### PB-113

**Action-first experience program** · P2 (score 2.48) · L · confidence Medium · In progress

- **Problem:** Pages open on information and settings, so people hunt for the next thing to do.
- **Proposed capability:** Owned and specified by the action-first thread (AF-1..AF-5); not restated here. AF-1 adds a shared action framework, the action-first standard and a baseline.
- **Current-state evidence:** AF-1 merged as #722
- **Inventory features:** [FEAT-SHL-04](FEATURE_INVENTORY.md#feat-shl-04)
- **Dependencies:** [PB-102](#pb-102)
- **Owner:** Action-first experience (AF-1..AF-5)
- **Pull requests:** [#722](https://github.com/KratosDataGod/KratosForge/pull/722), [#760](https://github.com/KratosDataGod/KratosForge/pull/760), [#776](https://github.com/KratosDataGod/KratosForge/pull/776), [#865](https://github.com/KratosDataGod/KratosForge/pull/865), [#1147](https://github.com/KratosDataGod/KratosForge/pull/1147)
- **Acceptance criteria:**
    - As defined in AF-1..AF-5 by its owning thread

### PB-119

**Learn: lessons, labs and guidance modes** · P2 (score 2.34) · L · confidence Medium · In progress

- **Problem:** People new to Fabric, Databricks or the app have no guided way to learn them inside KratosForge.
- **Proposed capability:** A Learn page (Preview) with lessons, paths and labs that run on this PC with generated data, guidance modes, and Learn evidence in Profile.
- **Current-state evidence:** LX-01 to LX-08 merged (#1063 to #1140): docs/learn.md, learn_service.py, learn.test.cjs and test_learn_*.py on main at 02118fa4; No backlog item tracked it before this check; Guidance levels Expert, Professional (default) and Guided (GD-01, #1161), Try the fix and Compare beside SQL findings (GD-02, #1162), Explain and Lesson buttons beside findings (#1160), a one-time guidance question (#1158) and the Microsoft Fabric path (#1159) merged on main at 0c96c9ce; Rule guides carry importance, principle, impact and how to check (GD-03, #1175); a fix applied after a matching Compare is recorded in Profile (GD-06, #1186); first lessons for the Snowflake, Databricks and Python paths (GD-10, #1233) on main at b3c5b223
- **Inventory features:** [FEAT-DEV-04](FEATURE_INVENTORY.md#feat-dev-04), [FEAT-GOV-09](FEATURE_INVENTORY.md#feat-gov-09)
- **Dependencies:** none
- **Owner:** Learning experience (LX) (LX-01..08)
- **Pull requests:** [#1063](https://github.com/KratosDataGod/KratosForge/pull/1063), [#1076](https://github.com/KratosDataGod/KratosForge/pull/1076), [#1083](https://github.com/KratosDataGod/KratosForge/pull/1083), [#1137](https://github.com/KratosDataGod/KratosForge/pull/1137), [#1138](https://github.com/KratosDataGod/KratosForge/pull/1138), [#1139](https://github.com/KratosDataGod/KratosForge/pull/1139), [#1140](https://github.com/KratosDataGod/KratosForge/pull/1140), [#1158](https://github.com/KratosDataGod/KratosForge/pull/1158), [#1159](https://github.com/KratosDataGod/KratosForge/pull/1159), [#1160](https://github.com/KratosDataGod/KratosForge/pull/1160), [#1161](https://github.com/KratosDataGod/KratosForge/pull/1161), [#1162](https://github.com/KratosDataGod/KratosForge/pull/1162), [#1175](https://github.com/KratosDataGod/KratosForge/pull/1175), [#1186](https://github.com/KratosDataGod/KratosForge/pull/1186), [#1233](https://github.com/KratosDataGod/KratosForge/pull/1233)
- **Acceptance criteria:**
    - Owned and specified by the Learning experience program (LX)

## Enterprise Administration and FinOps

| Id | Item | Priority | Owner | Effort | State |
| --- | --- | --- | --- | --- | --- |
| [PB-017](#pb-017) | Local vs Fabric execution advisor | P1 | Administration and FinOps sprint | M | Accepted |
| [PB-047](#pb-047) | Incident screens | P2 | Workload management | M | Accepted |
| [PB-050](#pb-050) | Label every CU number measured, estimated, scenario or unknown | P1 | Administration and FinOps sprint | M | Accepted |
| [PB-088](#pb-088) | Capacity inventory and health | P2 | Administration and FinOps sprint | M | Accepted |
| [PB-089](#pb-089) | Measured CU import and reconciliation | P2 | Administration and FinOps sprint | L | Accepted |
| [PB-090](#pb-090) | Preflight risk analyzer | P1 | Administration and FinOps sprint | M | Accepted |
| [PB-091](#pb-091) | Agent dashboard | P2 | Workload management | M | Accepted |
| [PB-092](#pb-092) | Shared workload ownership | P3 | Workload management | M | Proposed |
| [PB-093](#pb-093) | Telemetry decision | P3 | Enterprise readiness | M | Accepted |
| [PB-094](#pb-094) | Measure the process kernel, not the engine | P2 | Prove | S | Accepted |
| [PB-104](#pb-104) | Optimize central lakehouse program | P1 | Optimize central lakehouse | XL | In progress |

### PB-017

**Local vs Fabric execution advisor** · P1 (score 3.15) · M · confidence Medium · Accepted

- **Problem:** Choosing This PC or Fabric is a manual guess.
- **Proposed capability:** Owned and specified by the Administration and FinOps sprint (FINOPS-005), using the compatibility analyzer and measured costs.
- **Current-state evidence:** Runs on is a saved choice; no recommendation; No implementation review; checked against product data and main in the acceptance batch of 2026-10-28 (product-management/in-review-acceptance-batch.html): Accepted with Documented Limitations (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4); limitations: The advice is only offered to AI tools (fabric_desktop_placement_advice) and has no screen.
- **Inventory features:** [FEAT-NB-07](FEATURE_INVENTORY.md#feat-nb-07), [FEAT-META-14](FEATURE_INVENTORY.md#feat-meta-14), [FEAT-ADM-09](FEATURE_INVENTORY.md#feat-adm-09)
- **Dependencies:** [PB-050](#pb-050), [PB-014](#pb-014)
- **Owner:** Administration and FinOps sprint (FINOPS-005)
- **Pull requests:** [#276](https://github.com/KratosDataGod/KratosForge/pull/276)
- **Acceptance criteria:**
    - As defined in FINOPS-005 by its owning thread

### PB-047

**Incident screens** · P2 (score 2.27) · M · confidence High · Accepted

- **Problem:** The incident engine runs only in the agent; the app has six incident methods and no screen.
- **Proposed capability:** Incident list and detail with acknowledge, assign, resolve, after a mockup.
- **Current-state evidence:** incidents.js calls the incident RPCs (Monitor > KratosForge > Operations > Incidents); PR #125 merged 2026-10-17; awaiting implementation review; No implementation review; checked against product data and main in the acceptance batch of 2026-10-28 (product-management/in-review-acceptance-batch.html): Accepted with Documented Limitations (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4); limitations: Numbers, priority and SLAs exist in the engine but aren't shown in the list.
- **Inventory features:** [FEAT-OPS-02](FEATURE_INVENTORY.md#feat-ops-02), [FEAT-GOV-07](FEATURE_INVENTORY.md#feat-gov-07), [FEAT-SHL-06](FEATURE_INVENTORY.md#feat-shl-06)
- **Dependencies:** Simplified incident management (PB-105, Chris's unit testing, 2026-10-18, item 33) extends these screens
- **Owner:** Workload management (FD-123, Workloads stage 2.3B)
- **Pull requests:** [#125](https://github.com/KratosDataGod/KratosForge/pull/125)
- **Acceptance criteria:**
    - As defined in FD-123 by its owning program

### PB-050

**Label every CU number measured, estimated, scenario or unknown** · P1 (score 3.48) · M · confidence High · Accepted

- **Problem:** Cost panel figures are estimates from fixed defaults and aren't calibrated against the Capacity Metrics app.
- **Proposed capability:** Owned and specified by the Administration and FinOps sprint (FINOPS-001).
- **Current-state evidence:** costs.py defaults: $0.18/CU-hour, Python 1 CU, Spark 8 CU; PR #117 merged 2026-10-17; awaiting implementation review; No implementation review; checked against product data and main in the acceptance batch of 2026-10-28 (product-management/in-review-acceptance-batch.html): Accepted (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4)
- **Inventory features:** [FEAT-ADM-01](FEATURE_INVENTORY.md#feat-adm-01), [FEAT-PRV-05](FEATURE_INVENTORY.md#feat-prv-05)
- **Dependencies:** Overlaps PR-OPT-003 and PR-OPT-004, whose savings categories reuse these CU labels (Optimize central lakehouse, PB-104), Monitor > Cost moves to Optimize > Cost and savings (Chris's unit testing, 2026-10-18, item 36; PB-104)
- **Owner:** Administration and FinOps sprint (FINOPS-001)
- **Merged duplicates:** Calibrate cost estimates against the Capacity Metrics app (feature inventory, FEAT-ADM-01)
- **Pull requests:** [#117](https://github.com/KratosDataGod/KratosForge/pull/117)
- **Acceptance criteria:**
    - As defined in FINOPS-001 by its owning thread

### PB-088

**Capacity inventory and health** · P2 (score 2.52) · M · confidence Medium · Accepted

- **Problem:** Admins can't see their capacities' size, state and load from the app.
- **Proposed capability:** Owned and specified by the Administration and FinOps sprint (FINOPS-002).
- **Current-state evidence:** fabric_api.py lists capacities only; PR #123 merged 2026-10-17; awaiting implementation review; No implementation review; checked against product data and main in the acceptance batch of 2026-10-28 (product-management/in-review-acceptance-batch.html): Accepted with Documented Limitations (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4); limitations: none recorded; linked to FEAT-ADM-07 at the evening check
- **Inventory features:** [FEAT-ADM-07](FEATURE_INVENTORY.md#feat-adm-07)
- **Dependencies:** [PB-050](#pb-050)
- **Owner:** Administration and FinOps sprint (FINOPS-002)
- **Pull requests:** [#123](https://github.com/KratosDataGod/KratosForge/pull/123)
- **Acceptance criteria:**
    - As defined in FINOPS-002 by its owning thread

### PB-089

**Measured CU import and reconciliation** · P2 (score 2.05) · L · confidence Low · Accepted

- **Problem:** Estimates are never reconciled with what Fabric actually charged.
- **Proposed capability:** Owned and specified by the Administration and FinOps sprint (FINOPS-003).
- **Current-state evidence:** No Capacity Metrics import; No implementation review; checked against product data and main in the acceptance batch of 2026-10-28 (product-management/in-review-acceptance-batch.html): Accepted with Documented Limitations (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4); limitations: Not yet tried with an export from your tenant.
- **Inventory features:** [FEAT-ADM-10](FEATURE_INVENTORY.md#feat-adm-10)
- **Dependencies:** [PB-050](#pb-050), Overlaps PR-OPT-004, whose measured CU savings need this import (Optimize central lakehouse, PB-104)
- **Owner:** Administration and FinOps sprint (FINOPS-003)
- **Pull requests:** [#153](https://github.com/KratosDataGod/KratosForge/pull/153)
- **Acceptance criteria:**
    - As defined in FINOPS-003 by its owning thread

### PB-090

**Preflight risk analyzer** · P1 (score 3.04) · M · confidence Medium · Accepted

- **Problem:** People learn a run was expensive or too big after it runs.
- **Proposed capability:** Owned and specified by the Administration and FinOps sprint (FINOPS-004).
- **Current-state evidence:** meta/preflight.py estimates memory only; No implementation review; checked against product data and main in the acceptance batch of 2026-10-28 (product-management/in-review-acceptance-batch.html): Accepted with Documented Limitations (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4); limitations: The memory preflight (meta_preflight) has no screen of its own.
- **Inventory features:** [FEAT-META-06](FEATURE_INVENTORY.md#feat-meta-06), [FEAT-ADM-08](FEATURE_INVENTORY.md#feat-adm-08)
- **Dependencies:** [PB-050](#pb-050)
- **Owner:** Administration and FinOps sprint (FINOPS-004)
- **Pull requests:** [#167](https://github.com/KratosDataGod/KratosForge/pull/167)
- **Acceptance criteria:**
    - As defined in FINOPS-004 by its owning thread

### PB-091

**Agent dashboard** · P2 (score 2.4) · M · confidence High · Accepted

- **Problem:** Agent status records are written but nothing shows them.
- **Proposed capability:** Agent list, queue and availability view.
- **Current-state evidence:** agent_status.py; workloads/agents.py read only by incidents; PR #133 merged 2026-10-17; awaiting implementation review; No implementation review; checked against product data and main in the acceptance batch of 2026-10-28 (product-management/in-review-acceptance-batch.html): Accepted (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4)
- **Inventory features:** [FEAT-ORC-05](FEATURE_INVENTORY.md#feat-orc-05)
- **Dependencies:** none
- **Owner:** Workload management (FD-122)
- **Pull requests:** [#133](https://github.com/KratosDataGod/KratosForge/pull/133)
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

**Telemetry decision** · P3 (score 1.15) · M · confidence Low · Accepted

- **Problem:** There is no usage data, and whether to collect any is undecided.
- **Proposed capability:** Decision D-C1, then build only what it allows.
- **Current-state evidence:** No telemetry code; PR #121 merged 2026-10-17; awaiting implementation review; No implementation review; checked against product data and main in the acceptance batch of 2026-10-28 (product-management/in-review-acceptance-batch.html): Accepted (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4)
- **Inventory features:** [FEAT-ADM-06](FEATURE_INVENTORY.md#feat-adm-06)
- **Dependencies:** Product-owner decision D-C1
- **Owner:** Enterprise readiness (FD-137)
- **Pull requests:** [#121](https://github.com/KratosDataGod/KratosForge/pull/121)
- **Acceptance criteria:**
    - D-C1 recorded
    - Nothing is collected without consent and a policy switch

### PB-094

**Measure the process kernel, not the engine** · P2 (score 2.48) · S · confidence Medium · Accepted

- **Problem:** With the process kernel on, CPU and memory are measured from the engine process (inference), so scorecards can be wrong.
- **Proposed capability:** Measure the child kernel's resources.
- **Current-state evidence:** measure.py RunMeter; RunMeter reads the kernel process's CPU and memory (#895) on main at b19b7456; No implementation review; checked against product data and main in acceptance batch 2 of 2026-10-29 (product-management/in-review-acceptance-batch-2.html): Accepted with Documented Limitations (Chris chose 'Accept all as recommended' on the decision card, 2026-10-30, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpKnZygxfq5AA9Hd5e1zTC9b); limitations: Processes a cell starts aren't counted.
- **Inventory features:** [FEAT-PRV-05](FEATURE_INVENTORY.md#feat-prv-05)
- **Dependencies:** none
- **Owner:** Prove (FD-119)
- **Pull requests:** [#895](https://github.com/KratosDataGod/KratosForge/pull/895)
- **Story issues:** [#846](https://github.com/KratosDataGod/KratosForge/issues/846)
- **Acceptance criteria:**
    - Scorecard resources match the kernel process in a test

### PB-104

**Optimize central lakehouse program** · P1 (score 1.27) · XL · confidence Low · In progress

- **Problem:** Optimization findings, CU measurements and local execution savings stay on each PC, so an organization can't see them across developers and workspaces or show what local execution saved.
- **Proposed capability:** Owned and specified by the Optimize central lakehouse thread from Chris's brief of 2026-10-18 (PR-OPT-001..009); not restated here. It covers initializing or attaching a shared optimization lakehouse (fdopt_ tables), the central data model, local execution savings tracking (Chris calls it a flagship feature), optimization savings tracking, reporting, offline sync, multiple lakehouse management, security and governance, and the optimization system's own cost accounting. Its Cost and savings tab replaces Monitor > Cost, without Fabric's Monitoring hub (Chris's unit testing, 2026-10-18, item 36).
- **Current-state evidence:** Chris's Optimize brief, 2026-10-18 08:55Z; No code or plan merged yet
- **Inventory features:** [FEAT-ADM-01](FEATURE_INVENTORY.md#feat-adm-01)
- **Dependencies:** none
- **Owner:** Optimize central lakehouse (PR-OPT-001..009)
- **Priority set by the product owner:** P1 (Chris (product owner), 2026-10-18): Local execution savings tracking is a flagship feature; its score (1.27) alone would put it at P3
- **Merged duplicates:** Overlaps PB-050 and PB-089 (CU labels and measured CU import, Administration and FinOps sprint); noted on each item
- **Acceptance criteria:**
    - As defined in PR-OPT-001..009 by its owning thread
    - Savings follow the brief's rule: no CU savings without a defensible baseline, and estimated savings are never labelled realized

## Extensibility, APIs, CLI, and MCP

| Id | Item | Priority | Owner | Effort | State |
| --- | --- | --- | --- | --- | --- |
| [PB-095](#pb-095) | MCP server and local API for external AI tools | P2 | Bring your own AI integration | XL | Accepted |
| [PB-096](#pb-096) | Run the AI client checklist for each named tool | P1 | Bring your own AI integration | S | In review |
| [PB-097](#pb-097) | Extensions | P3 | Git-native development | XL | Proposed |
| [PB-098](#pb-098) | One list of engine methods | P1 | Bring your own AI integration | S | In review |
| [PB-109](#pb-109) | VS Code developer integration | P2 | Bring VS Code developers to KratosForge | XL | In progress |
| [PB-120](#pb-120) | Platform support levels and one engine contract | P1 | Fabric and Databricks unified development | L | In progress |

### PB-095

**MCP server and local API for external AI tools** · P2 (score 1.95) · XL · confidence Medium · Accepted

- **Problem:** External AI tools can't see or drive the app's capabilities.
- **Proposed capability:** The bring-your-own-AI stack PR-AI-001..012, built and specified by its program.
- **Current-state evidence:** PRs #98-#108, #110, #112 merged (PR-AI-001..012); awaiting implementation review; No implementation review; checked against product data and main in the acceptance batch of 2026-10-28 (product-management/in-review-acceptance-batch.html): Accepted (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4)
- **Inventory features:** [FEAT-EXT-02](FEATURE_INVENTORY.md#feat-ext-02)
- **Dependencies:** Overlaps HD-006 run-notebook approval shows and binds the code (P0); HD-014 workspace limits on every capability (Codebase audit and hardening backlog, PB-101)
- **Owner:** Bring your own AI integration (PR-AI-001..012)
- **Pull requests:** [#99](https://github.com/KratosDataGod/KratosForge/pull/99), [#100](https://github.com/KratosDataGod/KratosForge/pull/100), [#101](https://github.com/KratosDataGod/KratosForge/pull/101), [#102](https://github.com/KratosDataGod/KratosForge/pull/102), [#103](https://github.com/KratosDataGod/KratosForge/pull/103), [#104](https://github.com/KratosDataGod/KratosForge/pull/104), [#105](https://github.com/KratosDataGod/KratosForge/pull/105), [#106](https://github.com/KratosDataGod/KratosForge/pull/106), [#107](https://github.com/KratosDataGod/KratosForge/pull/107), [#108](https://github.com/KratosDataGod/KratosForge/pull/108), [#110](https://github.com/KratosDataGod/KratosForge/pull/110), [#112](https://github.com/KratosDataGod/KratosForge/pull/112)
- **Acceptance criteria:**
    - As defined in PR-AI-001..012
    - Each PR passes the implementation review

### PB-096

**Run the AI client checklist for each named tool** · P1 (score 2.75) · S · confidence High · In review

- **Problem:** Only the MCP SDK client and plain HTTP were tested; named AI tools are marked Untested.
- **Proposed capability:** Each client run through docs/product/ai-integration-client-checklist.md and recorded.
- **Current-state evidence:** AI integration thread notes; Claude Code and curl ran the client checklist step by step, and Codex CLI reads the setup and starts the server; the results are in ai-integration-compatibility.md, and other clients stay Untested (#1185) on main at 0c96c9ce. Run on Linux with no Fabric sign-in
- **Inventory features:** [FEAT-EXT-02](FEATURE_INVENTORY.md#feat-ext-02)
- **Dependencies:** [PB-095](#pb-095)
- **Owner:** Bring your own AI integration (PR-AI-011)
- **Pull requests:** [#1185](https://github.com/KratosDataGod/KratosForge/pull/1185)
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

**One list of engine methods** · P1 (score 2.6) · S · confidence Medium · In review

- **Problem:** server.py METHODS and the capability registry can drift apart.
- **Proposed capability:** METHODS generated from, or checked against, the capability registry.
- **Current-state evidence:** server.py METHODS
- **Inventory features:** [FEAT-SHL-02](FEATURE_INVENTORY.md#feat-shl-02)
- **Dependencies:** [PB-095](#pb-095)
- **Owner:** Bring your own AI integration (PR-AI-003)
- **Pull requests:** [#1152](https://github.com/KratosDataGod/KratosForge/pull/1152)
- **Acceptance criteria:**
    - A test fails when a method is in one list and not the other

### PB-109

**VS Code developer integration** · P2 (score 1.9) · XL · confidence Low · In progress

- **Problem:** Developers who live in VS Code can't use KratosForge's local runs without leaving their editor.
- **Proposed capability:** Owned by the 'Bring VS Code developers to KratosForge' thread: phase 0 on-disk change guard (#650, merged), MCP in VS Code and an Add to VS Code button; later phases need Chris's go and three decisions.
- **Current-state evidence:** Proposal 2026-10-24; #650 merged: the app never saves over a file another program changed
- **Inventory features:** [FEAT-EXT-01](FEATURE_INVENTORY.md#feat-ext-01), [FEAT-EXT-02](FEATURE_INVENTORY.md#feat-ext-02), [FEAT-EXT-06](FEATURE_INVENTORY.md#feat-ext-06), [FEAT-NB-17](FEATURE_INVENTORY.md#feat-nb-17)
- **Dependencies:** Chris decides paired-editor runs without per-run approval, .vsix first then Marketplace (publisher account is Chris-only), DuckDB extension later
- **Owner:** Bring VS Code developers to KratosForge (VS phases 0-4)
- **Pull requests:** [#650](https://github.com/KratosDataGod/KratosForge/pull/650), [#652](https://github.com/KratosDataGod/KratosForge/pull/652), [#670](https://github.com/KratosDataGod/KratosForge/pull/670), [#695](https://github.com/KratosDataGod/KratosForge/pull/695), [#710](https://github.com/KratosDataGod/KratosForge/pull/710), [#712](https://github.com/KratosDataGod/KratosForge/pull/712), [#742](https://github.com/KratosDataGod/KratosForge/pull/742), [#754](https://github.com/KratosDataGod/KratosForge/pull/754), [#795](https://github.com/KratosDataGod/KratosForge/pull/795)
- **Acceptance criteria:**
    - As defined in the thread's phases 0-4

### PB-120

**Platform support levels and one engine contract** · P1 (score 2.62) · L · confidence Medium · In progress

- **Problem:** Each engine claimed its own capabilities, and nothing said how far each platform was really proven.
- **Proposed capability:** A conformance check every engine adapter passes, one manifest per platform with support levels from evidence, SQL portability per statement, and comparing one query on two engines.
- **Current-state evidence:** KUDP-01, 02, 05 and 06 merged; docs/platforms.md records Fabric at level 2 from the 2026-10-29 live run on main at 02118fa4
- **Inventory features:** [FEAT-EXT-05](FEATURE_INVENTORY.md#feat-ext-05), [FEAT-SQL-03](FEATURE_INVENTORY.md#feat-sql-03)
- **Dependencies:** none
- **Owner:** Fabric and Databricks unified development (KUDP-01..06)
- **Pull requests:** [#1056](https://github.com/KratosDataGod/KratosForge/pull/1056), [#1078](https://github.com/KratosDataGod/KratosForge/pull/1078), [#1115](https://github.com/KratosDataGod/KratosForge/pull/1115), [#1118](https://github.com/KratosDataGod/KratosForge/pull/1118), [#1124](https://github.com/KratosDataGod/KratosForge/pull/1124), [#1125](https://github.com/KratosDataGod/KratosForge/pull/1125), [#1142](https://github.com/KratosDataGod/KratosForge/pull/1142), [#1180](https://github.com/KratosDataGod/KratosForge/pull/1180), [#1183](https://github.com/KratosDataGod/KratosForge/pull/1183), [#1191](https://github.com/KratosDataGod/KratosForge/pull/1191), [#1201](https://github.com/KratosDataGod/KratosForge/pull/1201), [#1202](https://github.com/KratosDataGod/KratosForge/pull/1202), [#1228](https://github.com/KratosDataGod/KratosForge/pull/1228), [#1238](https://github.com/KratosDataGod/KratosForge/pull/1238), [#1263](https://github.com/KratosDataGod/KratosForge/pull/1263), [#1271](https://github.com/KratosDataGod/KratosForge/pull/1271), [#1276](https://github.com/KratosDataGod/KratosForge/pull/1276), [#1277](https://github.com/KratosDataGod/KratosForge/pull/1277), [#1279](https://github.com/KratosDataGod/KratosForge/pull/1279), [#1292](https://github.com/KratosDataGod/KratosForge/pull/1292), [#1323](https://github.com/KratosDataGod/KratosForge/pull/1323), [#1329](https://github.com/KratosDataGod/KratosForge/pull/1329), [#1380](https://github.com/KratosDataGod/KratosForge/pull/1380), [#1388](https://github.com/KratosDataGod/KratosForge/pull/1388), [#1400](https://github.com/KratosDataGod/KratosForge/pull/1400), [#1412](https://github.com/KratosDataGod/KratosForge/pull/1412), [#1415](https://github.com/KratosDataGod/KratosForge/pull/1415), [#1420](https://github.com/KratosDataGod/KratosForge/pull/1420), [#1439](https://github.com/KratosDataGod/KratosForge/pull/1439), [#1450](https://github.com/KratosDataGod/KratosForge/pull/1450), [#1484](https://github.com/KratosDataGod/KratosForge/pull/1484), [#1485](https://github.com/KratosDataGod/KratosForge/pull/1485), [#1486](https://github.com/KratosDataGod/KratosForge/pull/1486), [#1488](https://github.com/KratosDataGod/KratosForge/pull/1488), [#1491](https://github.com/KratosDataGod/KratosForge/pull/1491), [#1495](https://github.com/KratosDataGod/KratosForge/pull/1495), [#1496](https://github.com/KratosDataGod/KratosForge/pull/1496), [#1505](https://github.com/KratosDataGod/KratosForge/pull/1505), [#1506](https://github.com/KratosDataGod/KratosForge/pull/1506), [#1517](https://github.com/KratosDataGod/KratosForge/pull/1517)
- **Acceptance criteria:**
    - Owned and specified by the unified platform program (KUDP)

## Security and Enterprise Readiness

| Id | Item | Priority | Owner | Effort | State |
| --- | --- | --- | --- | --- | --- |
| [PB-001](#pb-001) | Sign the app and engine | P0 | Enterprise readiness | S | Proposed |
| [PB-005](#pb-005) | Encrypt the token cache | P1 | Enterprise readiness | S | In review |
| [PB-006](#pb-006) | Content Security Policy for the main window | P1 | Enterprise readiness | M | Accepted |
| [PB-007](#pb-007) | Audit trail of runs and changes | P1 | Enterprise readiness | M | Accepted |
| [PB-008](#pb-008) | Keep the embedded Fabric view on Fabric hosts | P1 | Enterprise readiness | S | Accepted |
| [PB-009](#pb-009) | Local data retention | P1 | Enterprise readiness | S | Accepted |
| [PB-010](#pb-010) | Security and network docs match the shipped app | P1 | Codebase audit and hardening | S | Accepted |
| [PB-061](#pb-061) | Missing policy keys | P2 | Enterprise readiness | M | In progress |
| [PB-063](#pb-063) | Assistive technology audit | P1 | Enterprise readiness | M | In progress |
| [PB-065](#pb-065) | Separation of duties by signed-in identity | P2 | Enterprise readiness | S | Accepted |
| [PB-101](#pb-101) | Codebase audit and hardening program | P0 | Codebase audit and hardening | XL | In progress |

### PB-001

**Sign the app and engine** · P0 (score 2.48) · S · confidence High · Proposed

- **Problem:** Unsigned binaries are blocked by Smart App Control and WDAC on many PCs, so the engine doesn't start.
- **Proposed capability:** Azure Trusted Signing in the release workflow for the exe and engine binaries.
- **Current-state evidence:** ci.yml signs only when Trusted Signing secrets exist; PR #28 blocked
- **Inventory features:** [FEAT-SEC-01](FEATURE_INVENTORY.md#feat-sec-01), [FEAT-SHL-09](FEATURE_INVENTORY.md#feat-shl-09)
- **Dependencies:** Chris creates the Azure Trusted Signing account (D-C3), Overlaps HD-021 enforce signing (waits on Chris, D-C3) (Codebase audit and hardening backlog, PB-101)
- **Owner:** Enterprise readiness (FD-103, FD-104)
- **Mandatory (blocker):** Without signing the app does not run on many managed PCs; the launch audience is about 5,000 people.
- **Pull requests:** [#28](https://github.com/KratosDataGod/KratosForge/pull/28)
- **Acceptance criteria:**
    - Released exe and engine DLLs carry a valid signature
    - A Smart App Control PC starts the engine

### PB-005

**Encrypt the token cache** · P1 (score 1.96) · S · confidence High · In review

- **Problem:** The MSAL token cache is a plain file on Windows.
- **Proposed capability:** DPAPI-protected cache through msal-extensions.
- **Current-state evidence:** auth.py writes a plain cache file; PR #593 encrypts the cache with DPAPI (HD-013); merged and promoted to Stable 3633b4a; acceptance batch of 2026-10-28 (product-management/in-review-acceptance-batch.html): kept In review (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4): Quality's blocking finding is still open. A small fix PR (lock and merge on save) would let this go to Accept with limits.; acceptance batch 2 of 2026-10-29 (product-management/in-review-acceptance-batch-2.html): kept In review (Chris chose 'Accept all as recommended' on the decision card, 2026-10-30, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpKnZygxfq5AA9Hd5e1zTC9b): Needs a small fix PR (lock and merge on save).
- **Inventory features:** [FEAT-AUTH-03](FEATURE_INVENTORY.md#feat-auth-03)
- **Dependencies:** Decision D-C2, Overlaps HD-013 DPAPI token cache (waits on Chris, D-C2) (Codebase audit and hardening backlog, PB-101)
- **Owner:** Enterprise readiness (FD-131b)
- **Mandatory (required):** Refresh tokens on disk unencrypted are a security finding any enterprise review will raise.
- **Pull requests:** [#593](https://github.com/KratosDataGod/KratosForge/pull/593)
- **Acceptance criteria:**
    - The cache is unreadable to another Windows user
    - Scheduled runs and the app share it safely

### PB-006

**Content Security Policy for the main window** · P1 (score 1.47) · M · confidence Medium · Accepted

- **Problem:** The main webview runs with csp: null and the Tauri global, so escaping is the only defense against injected HTML.
- **Proposed capability:** Move notebook HTML output to another origin, then set a CSP.
- **Current-state evidence:** tauri.conf.json csp: null, withGlobalTauri: true; Implementation review docs/product/reviews/PB-006-250.md: Accepted with Documented Limitations (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4); limitations: No committed test renders HTML output and proves the frame can't reach the app. Windows check still to do.
- **Inventory features:** [FEAT-SEC-02](FEATURE_INVENTORY.md#feat-sec-02)
- **Dependencies:** Overlaps HD-020 Content Security Policy (Codebase audit and hardening backlog, PB-101)
- **Owner:** Enterprise readiness (FD-102b)
- **Mandatory (required):** Defense in depth for a webview that renders user and notebook content.
- **Pull requests:** [#250](https://github.com/KratosDataGod/KratosForge/pull/250), [#445](https://github.com/KratosDataGod/KratosForge/pull/445)
- **Acceptance criteria:**
    - A CSP is set and the UI test suite passes
    - Notebook HTML output can't call Tauri commands

### PB-007

**Audit trail of runs and changes** · P1 (score 2.08) · M · confidence High · Accepted

- **Problem:** Nothing records who ran or changed what in a tamper-evident way.
- **Proposed capability:** Hash-chained audit lines, built in PR #89.
- **Current-state evidence:** PR #89 merged; awaiting implementation review; No implementation review; checked against product data and main in the acceptance batch of 2026-10-28 (product-management/in-review-acceptance-batch.html): Accepted with Documented Limitations (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4); limitations: Proof receipts have no list screen.
- **Inventory features:** [FEAT-ADM-02](FEATURE_INVENTORY.md#feat-adm-02), [FEAT-PRV-03](FEATURE_INVENTORY.md#feat-prv-03)
- **Dependencies:** Overlaps HD-007 log redaction before JSON encoding (P0); HD-017 audit chain across processes (Codebase audit and hardening backlog, PB-101)
- **Owner:** Enterprise readiness (FD-125)
- **Mandatory (required):** Enterprise compliance needs an audit record of changes to shared data.
- **Pull requests:** [#89](https://github.com/KratosDataGod/KratosForge/pull/89)
- **Acceptance criteria:**
    - As defined in FD-125
    - Accepted through the implementation review

### PB-008

**Keep the embedded Fabric view on Fabric hosts** · P1 (score 1.84) · S · confidence Medium · Accepted

- **Problem:** The embedded view's allow-list accepts any *.microsoft.com and *.live.com page.
- **Proposed capability:** Navigation limited to Fabric, Power BI and sign-in hosts; others open in the browser.
- **Current-state evidence:** main.rs fabric_view host check (code reading); The Fabric view opens only Fabric and sign-in hosts (SH-12, #259; #455); readiness gate 1 lists SH-12 closed; Implementation review docs/product/reviews/PB-008-455.md: Accepted with Documented Limitations (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4); limitations: Only top-level pages and pop-ups are filtered. A refused link does nothing visible.
- **Inventory features:** [FEAT-FAB-05](FEATURE_INVENTORY.md#feat-fab-05)
- **Dependencies:** [PB-071](#pb-071), Overlaps HD-027 Fabric view navigation filter (Codebase audit and hardening backlog, PB-101)
- **Owner:** Enterprise readiness (FD-102)
- **Mandatory (required):** The embedded view holds saved Fabric sign-in cookies.
- **Pull requests:** [#259](https://github.com/KratosDataGod/KratosForge/pull/259), [#455](https://github.com/KratosDataGod/KratosForge/pull/455)
- **Acceptance criteria:**
    - A link to another Microsoft site opens outside the app
    - Covered by a Rust test run in CI

### PB-009

**Local data retention** · P1 (score 1.72) · S · confidence High · Accepted

- **Problem:** Copies of lakehouse data stay on PCs indefinitely.
- **Proposed capability:** Built in PR #95 (FD-131b).
- **Current-state evidence:** PR #95 merged; awaiting implementation review; No implementation review; checked against product data and main in the acceptance batch of 2026-10-28 (product-management/in-review-acceptance-batch.html): Accepted (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4)
- **Inventory features:** [FEAT-ADM-05](FEATURE_INVENTORY.md#feat-adm-05)
- **Dependencies:** none
- **Owner:** Enterprise readiness (FD-131b)
- **Mandatory (required):** Sensitive data protection policy needs bounded local copies.
- **Pull requests:** [#95](https://github.com/KratosDataGod/KratosForge/pull/95)
- **Acceptance criteria:**
    - As defined in FD-131b
    - A dry run deletes nothing

### PB-010

**Security and network docs match the shipped app** · P1 (score 2.2) · S · confidence High · Accepted

- **Problem:** docs/security.md says there is no redaction, scanning, SBOM or provenance (all exist); enterprise-install.md's host list omits hosts the app calls and misattributes one.
- **Proposed capability:** Corrected security and network documentation that IT can rely on.
- **Current-state evidence:** docs/security.md:41; ci.yml audit and SBOM jobs; docs/enterprise-install.md hosts table; security.md and enterprise-install.md corrected (#917); #925 closed as a duplicate; a test for the host list is #959 on main at b19b7456; A test keeps the firewall host list in step with the code (#1047, issue #959) on main at 188d5a93; No implementation review; checked against product data and main in acceptance batch 2 of 2026-10-29 (product-management/in-review-acceptance-batch-2.html): Accepted (Chris chose 'Accept all as recommended' on the decision card, 2026-10-30, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpKnZygxfq5AA9Hd5e1zTC9b)
- **Inventory features:** [FEAT-SEC-03](FEATURE_INVENTORY.md#feat-sec-03), [FEAT-SHL-02](FEATURE_INVENTORY.md#feat-shl-02)
- **Dependencies:** Overlaps HD-009 correct false statements in the security docs (P0) (Codebase audit and hardening backlog, PB-101)
- **Owner:** Codebase audit and hardening (HD-009)
- **Mandatory (required):** Security reviewers and firewall teams act on these documents; wrong docs block or mislead deployment.
- **Pull requests:** [#917](https://github.com/KratosDataGod/KratosForge/pull/917), [#1047](https://github.com/KratosDataGod/KratosForge/pull/1047)
- **Story issues:** [#886](https://github.com/KratosDataGod/KratosForge/issues/886), [#925](https://github.com/KratosDataGod/KratosForge/issues/925), [#959](https://github.com/KratosDataGod/KratosForge/issues/959)
- **Implementation package:** [pr-ready/PB-010.md](pr-ready/PB-010.md)
- **Acceptance criteria:**
    - Every claim in security.md matches the code at the time of the PR
    - The hosts list matches every host in fabric_api.py, auth.py, updater.py and main.rs

### PB-061

**Missing policy keys** · P2 (score 1.96) · M · confidence High · In progress

- **Problem:** Docs describe policy keys (allowed hosts, retention days, telemetry, three-value allowed compute) the app doesn't read.
- **Proposed capability:** Implement or remove each documented key.
- **Current-state evidence:** app_settings.py key list; docs/enterprise-install.md; The three remaining allow_ switches fail closed, and a test keeps every allow_ key in RESTRICTED (#1150) on main at 0c96c9ce. The policy keys FD-105 lists (approved endpoints, execution location, updates, retention, telemetry) are still to decide
- **Inventory features:** [FEAT-SHL-08](FEATURE_INVENTORY.md#feat-shl-08)
- **Dependencies:** Overlaps HD-010 policy fails closed; HD-057 ADMX policy (Codebase audit and hardening backlog, PB-101)
- **Owner:** Enterprise readiness (FD-105)
- **Pull requests:** [#1150](https://github.com/KratosDataGod/KratosForge/pull/1150)
- **Story issues:** [#960](https://github.com/KratosDataGod/KratosForge/issues/960)
- **Acceptance criteria:**
    - As defined in FD-105

### PB-063

**Assistive technology audit** · P1 (score 2.21) · M · confidence Medium · In progress

- **Problem:** Accessibility is checked statically only; no screen-reader or contrast audit is recorded.
- **Proposed capability:** A Narrator and keyboard audit on Windows with fixes.
- **Current-state evidence:** app/tests/ui.test.cjs static checks; Part 1 merged: automated axe-core, keyboard and focus audit of every page (#907); findings in #905 and #945; Narrator pass not done on main at b19b7456; The audit's findings fixed (#926), and every check axe can't decide has a verdict (#951) on main at 188d5a93; no Narrator pass on Windows yet; The automated audit covers Preview screens and common dialogs, and axe checks wait for animations to end (#1294, #1334, #1339) on main at b3c5b223; no Narrator pass on Windows yet
- **Inventory features:** [FEAT-SHL-07](FEATURE_INVENTORY.md#feat-shl-07), [FEAT-SHL-04](FEATURE_INVENTORY.md#feat-shl-04)
- **Dependencies:** Overlaps UX-012 keyboard access for rows, menus and tabs (Enterprise readiness) (UX and simplicity backlog, PB-102), Overlaps HD-042..044 UI dialog, test and accessibility fixes (Codebase audit and hardening backlog, PB-101)
- **Owner:** Enterprise readiness (FD-139)
- **Mandatory (required):** Enterprise procurement often requires an accessibility conformance statement.
- **Pull requests:** [#907](https://github.com/KratosDataGod/KratosForge/pull/907), [#926](https://github.com/KratosDataGod/KratosForge/pull/926), [#951](https://github.com/KratosDataGod/KratosForge/pull/951), [#1294](https://github.com/KratosDataGod/KratosForge/pull/1294), [#1297](https://github.com/KratosDataGod/KratosForge/pull/1297), [#1334](https://github.com/KratosDataGod/KratosForge/pull/1334), [#1339](https://github.com/KratosDataGod/KratosForge/pull/1339)
- **Story issues:** [#880](https://github.com/KratosDataGod/KratosForge/issues/880), [#905](https://github.com/KratosDataGod/KratosForge/issues/905), [#945](https://github.com/KratosDataGod/KratosForge/issues/945), [#962](https://github.com/KratosDataGod/KratosForge/issues/962)
- **Acceptance criteria:**
    - Audit results recorded per page
    - Blocking findings fixed

### PB-065

**Separation of duties by signed-in identity** · P2 (score 2.05) · S · confidence High · Accepted

- **Problem:** separate_promoter compares PC login names, not signed-in identities.
- **Proposed capability:** Compare Entra object ids.
- **Current-state evidence:** duties.py; PR #721 (HD-030): the separate promoter compares Entra object ids; Implementation review docs/product/reviews/PB-065-721.md: Accepted with Documented Limitations (Chris 'I accept all 4', 2026-10-26, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpREfN9JPWyocAdUEQR2tzj9); limitations: not a security boundary, the switch is still in environments.toml, not tried with two PCs and one Entra account
- **Inventory features:** [FEAT-GOV-04](FEATURE_INVENTORY.md#feat-gov-04)
- **Dependencies:** Overlaps HD-030 separation of duties by Entra id (staged roadmap) (Codebase audit and hardening backlog, PB-101)
- **Owner:** Enterprise readiness (FD-130)
- **Pull requests:** [#721](https://github.com/KratosDataGod/KratosForge/pull/721)
- **Acceptance criteria:**
    - The same Entra user on two PCs is recognized as one person

### PB-101

**Codebase audit and hardening program** · P0 (score 1.75) · XL · confidence Medium · In progress

- **Problem:** An enterprise audit of main at 1248ccd found data-safety, security and update defects: a table rewrite after commit, Delta profile checks after writes, unguarded OneLake paths, approvals that don't bind the code, redaction after JSON encoding, and updates without checksums.
- **Proposed capability:** Owned and specified by the Codebase audit and hardening thread (HD-001..057, backlog in docs/hardening/backlog.md on its branch); not restated here. Its P0s are HD-001..009; P1 HD-010..035; P2 HD-036..054; P3 HD-050, 055..057.
- **Current-state evidence:** Audit of main at 1248ccd by the hardening thread, 2026-10-17; Backlog not yet pushed (push freeze)
- **Inventory features:** [FEAT-SHL-09](FEATURE_INVENTORY.md#feat-shl-09), [FEAT-ONE-05](FEATURE_INVENTORY.md#feat-one-05)
- **Dependencies:** none
- **Owner:** Codebase audit and hardening (HD-001..057)
- **Mandatory (blocker):** HD-001..009 fix data-loss, security and update-integrity defects; AGENTS.md requires protecting OneLake data and update provenance.
- **Merged duplicates:** Overlaps PB-001, PB-002, PB-003, PB-005..PB-008, PB-010, PB-042, PB-051, PB-064..PB-066, PB-068, PB-071, PB-073, PB-074 and others (noted on each item)
- **Pull requests:** [#593](https://github.com/KratosDataGod/KratosForge/pull/593), [#618](https://github.com/KratosDataGod/KratosForge/pull/618), [#619](https://github.com/KratosDataGod/KratosForge/pull/619), [#621](https://github.com/KratosDataGod/KratosForge/pull/621), [#623](https://github.com/KratosDataGod/KratosForge/pull/623), [#624](https://github.com/KratosDataGod/KratosForge/pull/624), [#626](https://github.com/KratosDataGod/KratosForge/pull/626), [#659](https://github.com/KratosDataGod/KratosForge/pull/659), [#674](https://github.com/KratosDataGod/KratosForge/pull/674), [#676](https://github.com/KratosDataGod/KratosForge/pull/676), [#679](https://github.com/KratosDataGod/KratosForge/pull/679), [#689](https://github.com/KratosDataGod/KratosForge/pull/689), [#691](https://github.com/KratosDataGod/KratosForge/pull/691), [#698](https://github.com/KratosDataGod/KratosForge/pull/698), [#708](https://github.com/KratosDataGod/KratosForge/pull/708), [#715](https://github.com/KratosDataGod/KratosForge/pull/715), [#717](https://github.com/KratosDataGod/KratosForge/pull/717), [#721](https://github.com/KratosDataGod/KratosForge/pull/721), [#757](https://github.com/KratosDataGod/KratosForge/pull/757), [#768](https://github.com/KratosDataGod/KratosForge/pull/768), [#786](https://github.com/KratosDataGod/KratosForge/pull/786), [#911](https://github.com/KratosDataGod/KratosForge/pull/911), [#916](https://github.com/KratosDataGod/KratosForge/pull/916), [#918](https://github.com/KratosDataGod/KratosForge/pull/918), [#930](https://github.com/KratosDataGod/KratosForge/pull/930), [#934](https://github.com/KratosDataGod/KratosForge/pull/934), [#938](https://github.com/KratosDataGod/KratosForge/pull/938), [#952](https://github.com/KratosDataGod/KratosForge/pull/952), [#989](https://github.com/KratosDataGod/KratosForge/pull/989), [#992](https://github.com/KratosDataGod/KratosForge/pull/992), [#1003](https://github.com/KratosDataGod/KratosForge/pull/1003), [#1011](https://github.com/KratosDataGod/KratosForge/pull/1011), [#1026](https://github.com/KratosDataGod/KratosForge/pull/1026), [#1030](https://github.com/KratosDataGod/KratosForge/pull/1030), [#1032](https://github.com/KratosDataGod/KratosForge/pull/1032), [#1048](https://github.com/KratosDataGod/KratosForge/pull/1048), [#1050](https://github.com/KratosDataGod/KratosForge/pull/1050), [#1055](https://github.com/KratosDataGod/KratosForge/pull/1055), [#1089](https://github.com/KratosDataGod/KratosForge/pull/1089), [#1122](https://github.com/KratosDataGod/KratosForge/pull/1122), [#1153](https://github.com/KratosDataGod/KratosForge/pull/1153), [#1156](https://github.com/KratosDataGod/KratosForge/pull/1156), [#1208](https://github.com/KratosDataGod/KratosForge/pull/1208), [#1209](https://github.com/KratosDataGod/KratosForge/pull/1209), [#1211](https://github.com/KratosDataGod/KratosForge/pull/1211), [#1212](https://github.com/KratosDataGod/KratosForge/pull/1212), [#1214](https://github.com/KratosDataGod/KratosForge/pull/1214), [#1215](https://github.com/KratosDataGod/KratosForge/pull/1215), [#1231](https://github.com/KratosDataGod/KratosForge/pull/1231), [#1245](https://github.com/KratosDataGod/KratosForge/pull/1245), [#1359](https://github.com/KratosDataGod/KratosForge/pull/1359), [#1377](https://github.com/KratosDataGod/KratosForge/pull/1377), [#1378](https://github.com/KratosDataGod/KratosForge/pull/1378), [#1379](https://github.com/KratosDataGod/KratosForge/pull/1379), [#1381](https://github.com/KratosDataGod/KratosForge/pull/1381), [#1382](https://github.com/KratosDataGod/KratosForge/pull/1382), [#1383](https://github.com/KratosDataGod/KratosForge/pull/1383), [#1385](https://github.com/KratosDataGod/KratosForge/pull/1385), [#1387](https://github.com/KratosDataGod/KratosForge/pull/1387), [#1392](https://github.com/KratosDataGod/KratosForge/pull/1392), [#1399](https://github.com/KratosDataGod/KratosForge/pull/1399), [#1402](https://github.com/KratosDataGod/KratosForge/pull/1402), [#1406](https://github.com/KratosDataGod/KratosForge/pull/1406), [#1411](https://github.com/KratosDataGod/KratosForge/pull/1411), [#1416](https://github.com/KratosDataGod/KratosForge/pull/1416), [#1429](https://github.com/KratosDataGod/KratosForge/pull/1429), [#1430](https://github.com/KratosDataGod/KratosForge/pull/1430), [#1431](https://github.com/KratosDataGod/KratosForge/pull/1431), [#1433](https://github.com/KratosDataGod/KratosForge/pull/1433), [#1434](https://github.com/KratosDataGod/KratosForge/pull/1434), [#1451](https://github.com/KratosDataGod/KratosForge/pull/1451), [#1454](https://github.com/KratosDataGod/KratosForge/pull/1454), [#1471](https://github.com/KratosDataGod/KratosForge/pull/1471), [#1501](https://github.com/KratosDataGod/KratosForge/pull/1501), [#1504](https://github.com/KratosDataGod/KratosForge/pull/1504), [#1508](https://github.com/KratosDataGod/KratosForge/pull/1508), [#1509](https://github.com/KratosDataGod/KratosForge/pull/1509), [#1511](https://github.com/KratosDataGod/KratosForge/pull/1511), [#1513](https://github.com/KratosDataGod/KratosForge/pull/1513), [#1516](https://github.com/KratosDataGod/KratosForge/pull/1516), [#1521](https://github.com/KratosDataGod/KratosForge/pull/1521), [#1522](https://github.com/KratosDataGod/KratosForge/pull/1522), [#1523](https://github.com/KratosDataGod/KratosForge/pull/1523)
- **Story issues:** [#910](https://github.com/KratosDataGod/KratosForge/issues/910), [#916](https://github.com/KratosDataGod/KratosForge/issues/916), [#927](https://github.com/KratosDataGod/KratosForge/issues/927), [#935](https://github.com/KratosDataGod/KratosForge/issues/935), [#936](https://github.com/KratosDataGod/KratosForge/issues/936), [#963](https://github.com/KratosDataGod/KratosForge/issues/963), [#964](https://github.com/KratosDataGod/KratosForge/issues/964), [#965](https://github.com/KratosDataGod/KratosForge/issues/965), [#968](https://github.com/KratosDataGod/KratosForge/issues/968), [#1049](https://github.com/KratosDataGod/KratosForge/issues/1049), [#1087](https://github.com/KratosDataGod/KratosForge/issues/1087)
- **Acceptance criteria:**
    - As defined in HD-001..057 by its owning thread
    - Overlapping items here follow the owner named in that backlog

## Reliability, Testing, and Performance

| Id | Item | Priority | Owner | Effort | State |
| --- | --- | --- | --- | --- | --- |
| [PB-002](#pb-002) | Tenant smoke test of ten core flows | P0 | Master product (unassigned) | M | In progress |
| [PB-003](#pb-003) | Prove OneLake leases and MERGE writes live | P1 | Enterprise readiness | S | In progress |
| [PB-064](#pb-064) | Per-build release assets, pinning and rollback | P1 | Enterprise readiness | M | Accepted |
| [PB-066](#pb-066) | Backup and restore of local state | P2 | Enterprise readiness | S | Accepted |
| [PB-069](#pb-069) | Compare large tables in batches | P2 | Staged roadmap | M | Accepted |
| [PB-070](#pb-070) | UI tests for the notebook editor and command bar | P2 | Master product (unassigned) | M | Accepted |
| [PB-071](#pb-071) | Run the Rust tests in CI | P1 | Codebase audit and hardening | S | Accepted |
| [PB-072](#pb-072) | Long engine calls stop blocking the app | P1 | Master product (unassigned) | L | Accepted |
| [PB-073](#pb-073) | Process kernel by default with limits | P2 | Prove | M | In progress |
| [PB-074](#pb-074) | Release pipeline hardening | P1 | Enterprise readiness | S | Accepted |
| [PB-076](#pb-076) | Retire side by side into Prove | P2 | Prove | S | Proposed |
| [PB-103](#pb-103) | Engineering quality program | P0 | Engineering quality review | XL | In progress |

### PB-002

**Tenant smoke test of ten core flows** · P0 (score 2.76) · M · confidence High · In progress

- **Problem:** Almost every Fabric-facing feature has only been tested with Fabric faked; the live test harness has never run.
- **Proposed capability:** A scripted ten-flow smoke test on the maintainer's non-production workspace, plus one live-test harness run, with results recorded in the inventory.
- **Current-state evidence:** livetest.py never run; docs/README.md lists features tried on the maintainer's tenant; The ten-flow smoke-test harness merged (#884) on main at 188d5a93; the live run waits on Chris's yes
- **Inventory features:** [FEAT-SHL-01](FEATURE_INVENTORY.md#feat-shl-01), [FEAT-AUTH-01](FEATURE_INVENTORY.md#feat-auth-01), [FEAT-AUTH-04](FEATURE_INVENTORY.md#feat-auth-04), [FEAT-FAB-01](FEATURE_INVENTORY.md#feat-fab-01), [FEAT-FAB-06](FEATURE_INVENTORY.md#feat-fab-06), [FEAT-FAB-07](FEATURE_INVENTORY.md#feat-fab-07), [FEAT-ONE-05](FEATURE_INVENTORY.md#feat-one-05), [FEAT-META-14](FEATURE_INVENTORY.md#feat-meta-14), [FEAT-OPS-04](FEATURE_INVENTORY.md#feat-ops-04), [FEAT-PRV-01](FEATURE_INVENTORY.md#feat-prv-01)
- **Dependencies:** Chris runs it on his non-production workspace, Overlaps HD-029 live harness run (Chris); HD-053 OneLake contract tests; HD-054 T-SQL harness and live job (Codebase audit and hardening backlog, PB-101)
- **Owner:** Master product (unassigned) (FD-101, FD-140)
- **Mandatory (blocker):** Launch to about 5,000 people needs evidence the core flows work on a real tenant.
- **Merged duplicates:** Ten-flow smoke test (honest review, 2026-10-16); Running the FD-101 harness once (enterprise readiness)
- **Pull requests:** [#884](https://github.com/KratosDataGod/KratosForge/pull/884)
- **Story issues:** [#849](https://github.com/KratosDataGod/KratosForge/issues/849)
- **Implementation package:** [pr-ready/PB-002.md](pr-ready/PB-002.md)
- **Acceptance criteria:**
    - Ten flows pass or have recorded defects
    - The inventory's verification moves only for flows that passed
    - No production workspace is touched

### PB-003

**Prove OneLake leases and MERGE writes live** · P1 (score 2.08) · S · confidence Medium · In progress

- **Problem:** Leases depend on OneLake honoring conditional requests, which has never been tried.
- **Proposed capability:** The live test extended to leases, two writers and MERGE.
- **Current-state evidence:** leases.py; livetest.py lacks lease and MERGE checks; Live-test checks for a second writer refused by the lease and a Fabric-safe MERGE merged (#892) on main at 188d5a93; not run on a tenant
- **Inventory features:** [FEAT-ORC-03](FEATURE_INVENTORY.md#feat-orc-03), [FEAT-ONE-05](FEATURE_INVENTORY.md#feat-one-05)
- **Dependencies:** [PB-002](#pb-002), Overlaps HD-016 cross-process leases; HD-029 live harness run (Codebase audit and hardening backlog, PB-101)
- **Owner:** Enterprise readiness (FD-101)
- **Mandatory (required):** If OneLake ignores conditional requests, concurrent writers can overwrite data.
- **Pull requests:** [#892](https://github.com/KratosDataGod/KratosForge/pull/892)
- **Story issues:** [#887](https://github.com/KratosDataGod/KratosForge/issues/887)
- **Acceptance criteria:**
    - A second writer is refused while a lease is held on OneLake
    - A MERGE write on OneLake passes the profile check

### PB-064

**Per-build release assets, pinning and rollback** · P1 (score 1.72) · M · confidence Medium · Accepted

- **Problem:** Only the newest build per channel exists, so IT can't pin or roll back.
- **Proposed capability:** Keep each build's assets and let policy pin a version.
- **Current-state evidence:** ci.yml release job replaces assets; promote.yml; No implementation review; checked against product data and main in the acceptance batch of 2026-10-28 (product-management/in-review-acceptance-batch.html): Accepted with Documented Limitations (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4); limitations: There's no policy setting to pin one build. IT turns off auto_update and installs the build it wants instead. The inventory's REL-01 limitations are out of date.
- **Inventory features:** [FEAT-REL-01](FEATURE_INVENTORY.md#feat-rel-01), [FEAT-SHL-09](FEATURE_INVENTORY.md#feat-shl-09)
- **Dependencies:** Overlaps HD-022 no downgrade, keep the previous exe; HD-028 per-build releases for pinning and rollback (Codebase audit and hardening backlog, PB-101)
- **Owner:** Enterprise readiness (FD-104)
- **Mandatory (required):** Enterprise update control needs rollback.
- **Pull requests:** [#241](https://github.com/KratosDataGod/KratosForge/pull/241)
- **Acceptance criteria:**
    - A previous build can be installed and pinned by policy
    - Promote to stable names the build it promotes

### PB-066

**Backup and restore of local state** · P2 (score 1.84) · S · confidence High · Accepted

- **Problem:** Settings, schedules, queue and receipts can't be backed up.
- **Proposed capability:** Built in PR #91 (FD-126).
- **Current-state evidence:** PR #91 merged; awaiting implementation review; No implementation review; checked against product data and main in the acceptance batch of 2026-10-28 (product-management/in-review-acceptance-batch.html): Accepted (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4)
- **Inventory features:** [FEAT-ADM-03](FEATURE_INVENTORY.md#feat-adm-03)
- **Dependencies:** Overlaps HD-005 restore removes the stale WAL and verifies (P0) (Codebase audit and hardening backlog, PB-101)
- **Owner:** Enterprise readiness (FD-126)
- **Pull requests:** [#91](https://github.com/KratosDataGod/KratosForge/pull/91)
- **Acceptance criteria:**
    - As defined in FD-126

### PB-069

**Compare large tables in batches** · P2 (score 1.99) · M · confidence Medium · Accepted

- **Problem:** Environment compare reads whole tables.
- **Proposed capability:** Batched or sampled compare.
- **Current-state evidence:** meta/compare.py; Above 5,000,000 rows the compare streams counts, totals and a row checksum through DuckDB (#897) on main at b19b7456; No implementation review; checked against product data and main in acceptance batch 2 of 2026-10-29 (product-management/in-review-acceptance-batch-2.html): Accepted with Documented Limitations (Chris chose 'Accept all as recommended' on the decision card, 2026-10-30, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpKnZygxfq5AA9Hd5e1zTC9b); limitations: Above 5,000,000 rows a compare says whether tables differ (counts, totals, a row checksum), not which rows.
- **Inventory features:** [FEAT-META-08](FEATURE_INVENTORY.md#feat-meta-08)
- **Dependencies:** none
- **Owner:** Staged roadmap (FD-114)
- **Pull requests:** [#897](https://github.com/KratosDataGod/KratosForge/pull/897)
- **Story issues:** [#888](https://github.com/KratosDataGod/KratosForge/issues/888)
- **Acceptance criteria:**
    - Compare of a table larger than memory finishes

### PB-070

**UI tests for the notebook editor and command bar** · P2 (score 2.07) · M · confidence High · Accepted

- **Problem:** UI tests check parsing, names and a few behaviors; the editor and keyboard flows have none.
- **Proposed capability:** Behavior tests for the editor, command mode and command bar.
- **Current-state evidence:** app/tests/ui.test.cjs has 23 tests; command-bar.test.cjs adds Ctrl+K browser tests; editor and command mode tests already existed on main at b19b7456; No implementation review; checked against product data and main in acceptance batch 2 of 2026-10-29 (product-management/in-review-acceptance-batch-2.html): Accepted (Chris chose 'Accept all as recommended' on the decision card, 2026-10-30, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpKnZygxfq5AA9Hd5e1zTC9b)
- **Inventory features:** [FEAT-NB-01](FEATURE_INVENTORY.md#feat-nb-01), [FEAT-SHL-04](FEATURE_INVENTORY.md#feat-shl-04)
- **Dependencies:** Overlaps HD-052 browser tests in CI (Codebase audit and hardening backlog, PB-101)
- **Owner:** Master product (unassigned)
- **Pull requests:** [#879](https://github.com/KratosDataGod/KratosForge/pull/879)
- **Story issues:** [#860](https://github.com/KratosDataGod/KratosForge/issues/860)
- **Acceptance criteria:**
    - Editor, command mode and Ctrl+K have tests that fail on regressions

### PB-071

**Run the Rust tests in CI** · P1 (score 2.45) · S · confidence High · Accepted

- **Problem:** The Rust shell has tests (watchdog, sign-in, integrity) that CI never runs.
- **Proposed capability:** A Windows cargo test job.
- **Current-state evidence:** ci.yml has cargo audit and cargo build, no cargo test; cargo test failed to build on Linux (gdk-3.0); No implementation review; checked against product data and main in the acceptance batch of 2026-10-28 (product-management/in-review-acceptance-batch.html): Accepted (Chris 'I approve all of these move these forward', 2026-10-28, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpFeQHvbSrE9FwPZyTzr8ao4)
- **Inventory features:** [FEAT-SHL-03](FEATURE_INVENTORY.md#feat-shl-03), [FEAT-SHL-09](FEATURE_INVENTORY.md#feat-shl-09), [FEAT-FAB-05](FEATURE_INVENTORY.md#feat-fab-05), [FEAT-REL-01](FEATURE_INVENTORY.md#feat-rel-01)
- **Dependencies:** Overlaps HD-011 cargo test runs in CI (Codebase audit and hardening backlog, PB-101)
- **Owner:** Codebase audit and hardening (HD-011)
- **Mandatory (required):** The watchdog, sign-in and update integrity code is security- and reliability-critical and untested in CI.
- **Pull requests:** [#244](https://github.com/KratosDataGod/KratosForge/pull/244)
- **Implementation package:** [pr-ready/PB-071.md](pr-ready/PB-071.md)
- **Acceptance criteria:**
    - cargo test runs on windows-latest on every PR
    - Existing Rust tests pass or failures are fixed

### PB-072

**Long engine calls stop blocking the app** · P1 (score 1.48) · L · confidence Medium · Accepted

- **Problem:** All engine calls share one pipe behind a mutex; anything off the watchdog's quick list has no time limit, so a slow Fabric call freezes every page.
- **Proposed capability:** Concurrent or cancellable long calls with time limits.
- **Current-state evidence:** main.rs rpc mutex; watchdog.rs QUICK list; The engine runs quick calls in their own lane (server.py, test_server_lanes.py, #508), so a slow Fabric call doesn't block them on main at b19b7456; #898 bounds four more calls (open); Acceptance changed 2026-10-28 from 'Every call has a limit': runs can take hours by design (watchdog.rs). Needs Chris's acceptance of the change; #898 bounds Monitor's status refresh (30 s), the drift card's read and the sign-in window (10 min) on main at 188d5a93. The changed acceptance (quick calls 60 s, long calls end with Stop) still needs Chris; acceptance batch 2 of 2026-10-29 (product-management/in-review-acceptance-batch-2.html): changed criterion 2 (quick calls 60 s, long calls end with Stop) approved and Accepted (Chris chose 'Accept all as recommended' on the decision card, 2026-10-30, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpKnZygxfq5AA9Hd5e1zTC9b)
- **Inventory features:** [FEAT-SHL-01](FEATURE_INVENTORY.md#feat-shl-01), [FEAT-SHL-03](FEATURE_INVENTORY.md#feat-shl-03)
- **Dependencies:** [PB-071](#pb-071), Overlaps UX-013 show when the engine is busy and let people stop it (Codebase audit and hardening) (UX and simplicity backlog, PB-102), Overlaps HD-041 RPC reply ids (Codebase audit and hardening backlog, PB-101)
- **Owner:** Master product (unassigned)
- **Mandatory (required):** A frozen app with no way out is the most visible reliability failure.
- **Pull requests:** [#508](https://github.com/KratosDataGod/KratosForge/pull/508), [#898](https://github.com/KratosDataGod/KratosForge/pull/898)
- **Story issues:** [#862](https://github.com/KratosDataGod/KratosForge/issues/862)
- **Acceptance criteria:**
    - A slow Fabric call doesn't block a quick call (test)
    - Quick calls have a 60-second limit; long calls (runs, publishing, Fabric) have no limit and Stop ends them

### PB-073

**Process kernel by default with limits** · P2 (score 1.86) · M · confidence Medium · In progress

- **Problem:** The in-engine kernel restarts the whole engine on Stop; the process kernel is opt-in with no limits.
- **Proposed capability:** Process kernel default after a Windows trial, with time and memory limits.
- **Current-state evidence:** kernel_proc.py; Settings > Preview
- **Inventory features:** [FEAT-NB-03](FEATURE_INVENTORY.md#feat-nb-03)
- **Dependencies:** Overlaps HD-012 kernel environment allowlist; HD-037 stop kills the whole process tree (Prove) (Codebase audit and hardening backlog, PB-101)
- **Owner:** Prove (FD-110)
- **Pull requests:** [#996](https://github.com/KratosDataGod/KratosForge/pull/996), [#1084](https://github.com/KratosDataGod/KratosForge/pull/1084)
- **Story issues:** [#896](https://github.com/KratosDataGod/KratosForge/issues/896)
- **Acceptance criteria:**
    - As defined in FD-110

### PB-074

**Release pipeline hardening** · P1 (score 2.2) · S · confidence High · Accepted

- **Problem:** Release doesn't wait for the audit job; docs.yml and codex-review.yml use tag-pinned actions; zensical installs unpinned.
- **Proposed capability:** Release depends on audit; all actions SHA-pinned; tool versions pinned.
- **Current-state evidence:** ci.yml; docs.yml; codex-review.yml; Release needs the audit job, with dated exceptions in .github/audit-exceptions.txt (#867); actions are SHA-pinned on main at b19b7456; No implementation review; checked against product data and main in acceptance batch 2 of 2026-10-29 (product-management/in-review-acceptance-batch-2.html): Accepted (Chris chose 'Accept all as recommended' on the decision card, 2026-10-30, project message cmsg_018T4o2pQdpbLBNhHpgVNNGpKnZygxfq5AA9Hd5e1zTC9b)
- **Inventory features:** [FEAT-SEC-03](FEATURE_INVENTORY.md#feat-sec-03)
- **Dependencies:** Overlaps HD-008 update checksums, no Stable-to-Preview fallback (P0); HD-024 pin actions; HD-025 agent installer constraints and ACL; HD-026 no bare python fallback (Codebase audit and hardening backlog, PB-101)
- **Owner:** Enterprise readiness (FD-103)
- **Mandatory (required):** Supply-chain controls are only as strong as the weakest workflow.
- **Pull requests:** [#867](https://github.com/KratosDataGod/KratosForge/pull/867)
- **Story issues:** [#861](https://github.com/KratosDataGod/KratosForge/issues/861)
- **Acceptance criteria:**
    - Release needs audit
    - No tag-pinned action remains

### PB-076

**Retire side by side into Prove** · P2 (score 1.8) · S · confidence Medium · Proposed

- **Problem:** Side by side writes real tables twice; Prove does the same check in sandboxes.
- **Proposed capability:** Keep the benchmark, retire the correctness path.
- **Current-state evidence:** side_by_side.py; PR #114 merged
- **Inventory features:** [FEAT-PRV-06](FEATURE_INVENTORY.md#feat-prv-06)
- **Dependencies:** none
- **Owner:** Prove (FD-112)
- **Acceptance criteria:**
    - As defined in FD-112

### PB-103

**Engineering quality program** · P0 (score 1.85) · XL · confidence Medium · In progress

- **Problem:** An independent quality review of main at 3bee0db found defects that lose or overwrite data while reporting success, fail-open checks, and services re-solved per feature: Spark writer options are ignored so replaceWhere replaces the whole table, a failed notebook save shows Saved and closing drops the edits.
- **Proposed capability:** Owned and specified by the Engineering quality review thread (findings QA-001..030 and remediation PR-QA-01..14 in docs/quality/, PR #176); not restated here. Its P0s are PR-QA-01 and PR-QA-02. Routed owners: QA-001, QA-002, QA-009 Prove; QA-003, QA-004, QA-006, QA-007, QA-010 Codebase audit and hardening; QA-005 Enterprise governance; QA-008 wording FinOps first (PR #174), then Prove, Local data engine, governance and Codebase audit and hardening for their own files, and UX and simplicity oversight for the README and top-level docs; QA-012, QA-016 Enterprise readiness; QA-015 Release management; QA-019 folded into UX-018 and QA-020 into UX-021 (PB-102).
- **Current-state evidence:** Quality review by the oversight thread, 2026-10-18 (PR #176): release decision FAIL for main at 3bee0db; QA-001, QA-006 and QA-016 reproduced against the engine code by that thread; not re-run here; Real-browser tests now reach the remaining screens and their writes (42 PRs from #1381 to #1523) on main at 063b84ed
- **Inventory features:** [FEAT-NB-04](FEATURE_INVENTORY.md#feat-nb-04), [FEAT-NB-05](FEATURE_INVENTORY.md#feat-nb-05), [FEAT-NB-06](FEATURE_INVENTORY.md#feat-nb-06)
- **Dependencies:** none
- **Owner:** Engineering quality review (QA-001..030, PR-QA-01..14)
- **Mandatory (blocker):** QA-001 (Critical) and QA-002 lose data while reporting success; AGENTS.md requires protecting OneLake data. The quality review's release decision for main is FAIL on QA-001.
- **Merged duplicates:** Overlaps the hardening and UX backlogs where docs/quality/ cites HD- or UX- ids; those owners keep them
- **Pull requests:** [#585](https://github.com/KratosDataGod/KratosForge/pull/585), [#616](https://github.com/KratosDataGod/KratosForge/pull/616), [#661](https://github.com/KratosDataGod/KratosForge/pull/661), [#680](https://github.com/KratosDataGod/KratosForge/pull/680), [#711](https://github.com/KratosDataGod/KratosForge/pull/711), [#733](https://github.com/KratosDataGod/KratosForge/pull/733), [#738](https://github.com/KratosDataGod/KratosForge/pull/738), [#766](https://github.com/KratosDataGod/KratosForge/pull/766), [#775](https://github.com/KratosDataGod/KratosForge/pull/775), [#778](https://github.com/KratosDataGod/KratosForge/pull/778), [#781](https://github.com/KratosDataGod/KratosForge/pull/781), [#784](https://github.com/KratosDataGod/KratosForge/pull/784), [#975](https://github.com/KratosDataGod/KratosForge/pull/975), [#981](https://github.com/KratosDataGod/KratosForge/pull/981), [#1012](https://github.com/KratosDataGod/KratosForge/pull/1012), [#1018](https://github.com/KratosDataGod/KratosForge/pull/1018), [#1021](https://github.com/KratosDataGod/KratosForge/pull/1021), [#1024](https://github.com/KratosDataGod/KratosForge/pull/1024), [#1025](https://github.com/KratosDataGod/KratosForge/pull/1025), [#1029](https://github.com/KratosDataGod/KratosForge/pull/1029), [#1075](https://github.com/KratosDataGod/KratosForge/pull/1075), [#1086](https://github.com/KratosDataGod/KratosForge/pull/1086), [#1093](https://github.com/KratosDataGod/KratosForge/pull/1093), [#1095](https://github.com/KratosDataGod/KratosForge/pull/1095), [#1097](https://github.com/KratosDataGod/KratosForge/pull/1097), [#1107](https://github.com/KratosDataGod/KratosForge/pull/1107), [#1154](https://github.com/KratosDataGod/KratosForge/pull/1154), [#1155](https://github.com/KratosDataGod/KratosForge/pull/1155), [#1164](https://github.com/KratosDataGod/KratosForge/pull/1164), [#1230](https://github.com/KratosDataGod/KratosForge/pull/1230), [#1234](https://github.com/KratosDataGod/KratosForge/pull/1234), [#1240](https://github.com/KratosDataGod/KratosForge/pull/1240), [#1258](https://github.com/KratosDataGod/KratosForge/pull/1258), [#1290](https://github.com/KratosDataGod/KratosForge/pull/1290), [#1298](https://github.com/KratosDataGod/KratosForge/pull/1298), [#1299](https://github.com/KratosDataGod/KratosForge/pull/1299), [#1302](https://github.com/KratosDataGod/KratosForge/pull/1302), [#1332](https://github.com/KratosDataGod/KratosForge/pull/1332), [#1336](https://github.com/KratosDataGod/KratosForge/pull/1336), [#1364](https://github.com/KratosDataGod/KratosForge/pull/1364), [#1384](https://github.com/KratosDataGod/KratosForge/pull/1384), [#1389](https://github.com/KratosDataGod/KratosForge/pull/1389), [#1390](https://github.com/KratosDataGod/KratosForge/pull/1390), [#1394](https://github.com/KratosDataGod/KratosForge/pull/1394), [#1395](https://github.com/KratosDataGod/KratosForge/pull/1395), [#1403](https://github.com/KratosDataGod/KratosForge/pull/1403), [#1405](https://github.com/KratosDataGod/KratosForge/pull/1405), [#1410](https://github.com/KratosDataGod/KratosForge/pull/1410), [#1414](https://github.com/KratosDataGod/KratosForge/pull/1414), [#1418](https://github.com/KratosDataGod/KratosForge/pull/1418), [#1421](https://github.com/KratosDataGod/KratosForge/pull/1421), [#1432](https://github.com/KratosDataGod/KratosForge/pull/1432), [#1435](https://github.com/KratosDataGod/KratosForge/pull/1435), [#1436](https://github.com/KratosDataGod/KratosForge/pull/1436), [#1441](https://github.com/KratosDataGod/KratosForge/pull/1441), [#1443](https://github.com/KratosDataGod/KratosForge/pull/1443), [#1444](https://github.com/KratosDataGod/KratosForge/pull/1444), [#1446](https://github.com/KratosDataGod/KratosForge/pull/1446), [#1449](https://github.com/KratosDataGod/KratosForge/pull/1449), [#1452](https://github.com/KratosDataGod/KratosForge/pull/1452), [#1457](https://github.com/KratosDataGod/KratosForge/pull/1457), [#1458](https://github.com/KratosDataGod/KratosForge/pull/1458), [#1461](https://github.com/KratosDataGod/KratosForge/pull/1461), [#1463](https://github.com/KratosDataGod/KratosForge/pull/1463), [#1464](https://github.com/KratosDataGod/KratosForge/pull/1464), [#1467](https://github.com/KratosDataGod/KratosForge/pull/1467), [#1468](https://github.com/KratosDataGod/KratosForge/pull/1468), [#1472](https://github.com/KratosDataGod/KratosForge/pull/1472), [#1474](https://github.com/KratosDataGod/KratosForge/pull/1474), [#1477](https://github.com/KratosDataGod/KratosForge/pull/1477), [#1478](https://github.com/KratosDataGod/KratosForge/pull/1478), [#1479](https://github.com/KratosDataGod/KratosForge/pull/1479), [#1480](https://github.com/KratosDataGod/KratosForge/pull/1480), [#1481](https://github.com/KratosDataGod/KratosForge/pull/1481), [#1487](https://github.com/KratosDataGod/KratosForge/pull/1487), [#1492](https://github.com/KratosDataGod/KratosForge/pull/1492), [#1493](https://github.com/KratosDataGod/KratosForge/pull/1493), [#1500](https://github.com/KratosDataGod/KratosForge/pull/1500), [#1514](https://github.com/KratosDataGod/KratosForge/pull/1514), [#1515](https://github.com/KratosDataGod/KratosForge/pull/1515), [#1520](https://github.com/KratosDataGod/KratosForge/pull/1520)
- **Acceptance criteria:**
    - As defined in QA-001..030 and PR-QA-01..14 by its owning thread
    - Routed items follow the owner named in docs/quality/remediation.md
<!-- END GENERATED: items -->
