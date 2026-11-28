# Enterprise optimization engine: first execution (section 19)

The enterprise optimization brief of 2026-11-07.
Audit of `main` at 4a8b34e (2026-11-07). Everything below was read from the code; nothing was run on a tenant.

## 1. Verified inventory of what already exists

| Area | What it does today | Code | Brief section it serves |
| --- | --- | --- | --- |
| Optimize page (Preview) | Overview, Opportunities (X-ray findings + shared recommendations, Effort, Dismiss), Cost and savings, Settings | `app/ui/optimize.js`, `optimize_service.py` | 11 (partly) |
| Central lakehouse | `FabricDesktop_Central`, `fdopt_*` tables, insert-only, record_id MERGE, migrations, families (`fdcap_`, `fdten_`, ...) | `optimize/schema.py`, `setup.py`, `lakehouse.py` | 12 |
| Shared findings and recommendations | Findings by fingerprint, recommendation lifecycle as events, workspace-scoped reads | `optimize/shared.py` | 10, 12 |
| Offline outbox, overhead | Queue when the lakehouse is unreachable; Optimize's own seconds/rows/commits | `optimize/outbox.py`, OPT-009 | 12, 15 |
| Estate X-ray | Small files, stale tables, vacuum due, empty and staging lakehouses, access, costly runs | `xray.py`, `xray_service.py` | 4.4, 3.3 (orphans) |
| Table maintenance | Optimize/V-Order/Vacuum in Fabric; risk check hard-stops on "blocked" | `maintenance.py`, `risk.py` | 4.4 |
| Measurement model | measured / estimated / scenario / unknown; unknown is None never 0; totals as weak as weakest part | `measurement.py` | 9 |
| Cost report | Each run's CU evidence; local "scenario" never summed | `costs.py`, `estate_service.cost_report` | 9 |
| Measured CU import | Capacity Metrics app and Cost Management exports, reconciled with runs | `usage/` | 8, 9 |
| Calibration | Fabric CU avoided only from measured Fabric runs of the same workload | `calibration.py` | 6, 9 |
| Placement advice | RECOMMEND LOCAL / FABRIC / EITHER / INSUFFICIENT, reports only, no screen | `advisor.py` | 6, 7 |
| Capacity | Which capacity each environment is on, health, settings history | `capacity.py`, `capacity_history.py` | 8 |
| Prove, side by side, benchmark | Same notebook on this PC and in Fabric, results compared, RunMeter CPU/memory, receipts, TPC-H | `side_by_side.py`, `proof.py`, `scorecard.py`, `benchmark.py` | 6 |
| Local write timing | Seconds per OneLake write (history.jsonl), skip-unchanged, zstd, write queue | `history.py`, `write_queue.py` | 5, 6 (network) |
| Notebook best practices | NB001-NB020 static rules (overwrite, collect, loop writes, UDF, count) | `notebook_practices.py` | 4.2, 4.3 |
| Notebook dependency graph | Tables and files each cell reads/writes, side effects | `depgraph.py` | 3.1 |
| SQL practices and portability | SQxxx rules; T-SQL / Spark / Snowflake portability | `sqlcompat/practices.py`, `sql_portability.py` | 4.1 |
| Warehouse query insights | WarehouseExecutions from workspace monitoring (Preview) | `warehouse_monitoring.py` | 4.1 |
| Semantic models | BPA (in Fabric), practices on this PC, Direct Lake fallback check, TMDL | `model_bpa.py`, `model_practices.py`, `model_directlake.py`, `tmdl.py` | 4.5, 3.4 |
| Pipelines, dataflows, copy jobs | getDefinition reads, run history, activity runs, local simulation | `workspace_*_service.py`, `orchestration/` | 3.2, 5 |
| Governance checks | One registry of NB/SQ/model rules for policies | `governance_checks.py` | 15 |
| Audit, redaction | Hash-chained audit, secrets redaction | `audit.py`, `audit_chain.py`, `redact.py` | 15 |

## 2. Current architecture and reusable components

- Engine: Python sidecar, `Service` built from ~60 mixins; RPC allowlist `server.METHODS`, reads listed in `audit.READ_ONLY`.
- Fabric reads go through `FabricClient` (`list_items`, `get_item_definition`, `list_job_instances`, `list_schedules`, `list_workspaces`).
- Evidence: every number is a `measurement.make(...)` dict; this is the backbone of "never fabricate savings".
- Persistence: local JSON/SQLite per PC; shared rows to the central lakehouse via `optimize/shared.py` (optional).

## 3. Gap analysis against the brief

| Brief | Status | Gap |
| --- | --- | --- |
| 1 Six-question philosophy | Missing | No engine orders Eliminate > Consolidate > Optimize > Place > Capacity |
| 2 Discovery engine (workspace/capacity levels) | Partial | X-ray covers lakehouses only; no workload inventory of notebooks/pipelines with run history |
| 3.1 Duplicate notebooks | Missing | depgraph exists; no cross-notebook comparison |
| 3.2 Duplicate pipelines | Missing | Definitions are readable; nothing compares them |
| 3.3 Duplicate data assets | Missing | X-ray has table stats per lakehouse; no cross-lakehouse comparison |
| 3.4 Semantic model redundancy | Missing | TMDL parser exists; no cross-model measure comparison |
| 4.1 SQL | Partial | Rules exist per script; no plan/statistics use, no repeated-query detection over query insights |
| 4.2 / 4.3 Python, Spark | Partial | Static rules exist; not tied to run cost or turned into opportunities |
| 4.4 Delta | Mostly there | X-ray; needs the common opportunity shape |
| 4.5 Semantic | Partial | BPA and Direct Lake check; not opportunities |
| 5 Data movement | Missing | No repeated-extraction or cross-workspace copy detection |
| 6 Local execution classes (6) | Partial | advisor gives 4 answers per notebook; no workspace-wide planner, no network model from measured throughput |
| 7 Workload placement | Missing | No ranked alternatives per workload |
| 8 Capacity elimination | Missing | Capacity reads exist; no demand model, smoothing, or consolidation simulator |
| 9 Financial model (6 classes) | Partial | measurement.py has 4 evidence classes; no recovered capacity / avoided expansion / realizable / verified-financial ledger |
| 10 Recommendation record (19 fields) | Partial | `optimize/shared.py` recommendation has a subset; no overlap/supersede model |
| 11 UX areas (8) | Partial | Overview, Opportunities, Cost exist; Explorer filters, Duplicates, Performance, Local planner, Capacity planner, Ledger, Action Center missing |
| 12 Lakehouse | Mostly there | Needs opportunity, inventory and dependency tables |
| 13 Workflow | Partial | Recommendation events exist; Validate/Measure/Verify not wired |
| 14 Continuous | Missing | No scheduled assessments, regression detection, "rejected stays rejected" |

## 4. Prioritized backlog (OE = optimization engine)

Phase 1, foundation
- **OE-001** (this PR, engine only): opportunity record (section 10 fields), workload inventory for a workspace or every workspace on a capacity, three deterministic analyzers (identical notebooks, repeated copy ingestion across pipelines, whole-table overwrite in notebooks that run on a schedule), impact from measured run history and imported measured CU, overlap-aware totals, a ranked next action, and a labelled demonstration fixture. RPC `optimization_assess`. Reads only.
- **OE-002**: mockup of Optimize > Assessment (pick workspace or capacity, workloads, opportunities, evidence drawer, next action). Via UX thread. Chris reacts before build.
- **OE-003**: Assessment screen behind Preview (after OE-002).
- **OE-004**: write opportunities to `fdopt_findings`/`fdopt_recommendations` through `optimize/shared.py` (coordinate with Optimize lakehouse thread, which owns that file).
- **OE-005**: dismiss/reject with reason; never re-raise a rejected opportunity unless its evidence fingerprint changes (section 14).

Phase 2, duplication
- OE-010 near-duplicate notebooks: same normalized reads and writes, different code = "suspected", needs validation.
- OE-011 pipelines: overlapping schedules for the same source; same pipeline run by two schedules.
- OE-012 duplicate data assets across lakehouses (schema + row count + Delta stats from X-ray), shortcuts as the alternative, legitimate isolation recorded.
- OE-013 semantic models: repeated measures (normalized DAX) and duplicate tables across models (TMDL).

Phase 3, performance
- OE-020 notebook rules into opportunities (NB003, NB007, NB010, NB011, NB018) weighted by measured run time.
- OE-021 SQL: repeated queries and heavy scans from warehouse query insights (where monitoring is on), SQ rules.
- OE-022 X-ray Delta findings into the opportunity shape (no duplicate scanning).
- OE-023 BPA and Direct Lake results into opportunities.
- OE-024 full loads that could be incremental: Copy activities without a watermark, dataflow full refresh.

Phase 4, local execution
- OE-030 six placement classes for every notebook (advisor + proof.check compatibility).
- OE-031 network model from measured OneLake write throughput (history.jsonl), "network is the bottleneck" detection, what-if bandwidth.
- OE-032 "Run a benchmark" from an opportunity (side by side), output correctness compared.

Phase 5, capacity
- OE-040 demand model from imported Capacity Metrics timepoints (peak, smoothing, throttling margins).
- OE-041 consolidation and retirement simulator F2-F2048, non-CU constraints (region, workspace settings, features), approved opportunities applied once.

Phase 6, financial
- OE-050 savings ledger classes (estimated compute reduction, verified, recovered capacity, avoided expansion, realizable, verified financial); builds on OPT-004 (Optimize lakehouse thread).
- OE-051 pricing assumptions: pay-as-you-go vs reservation, local infrastructure, network, migration effort.

Phase 7, continuous
- OE-060 scheduled assessments on this PC (local_schedules), diff against the previous one, regressions, new duplicates.

## 5. Dependency map

```
OE-001 engine ──► OE-002 mockup ──► OE-003 screen
   │                                   ▲
   ├──► OE-004 shared rows (needs Optimize lakehouse thread, optimize/shared.py)
   ├──► OE-005 reject/dismiss memory
   ├──► OE-010..013 more duplicate analyzers (same opportunity shape)
   ├──► OE-020..024 performance analyzers (reuse notebook_practices, sqlcompat, X-ray, BPA)
   ├──► OE-030..032 placement (reuse advisor, calibration, side_by_side; Local engine + Local TPC-H threads)
   └──► OE-040 demand model (usage/ imports; Workload mgmt owns Monitor) ──► OE-041 capacity simulator
                                                         OE-050 ledger (after OPT-004) ──► OE-060 continuous
```

Coordinating threads: Optimize lakehouse (fdopt_ tables, OPT-004 savings), BPA (rules), Workload mgmt (Monitor, capacity), Local engine / Local TPC-H (benchmarks, write path), UX (mockups).

## 6. Proposed architecture for the optimization engines

```
sidecar/fabric_desktop/opportunities/
  model.py      opportunity record, categories, actions (section 7), statuses, confidence; stable ids
  inventory.py  workloads from items + definitions + job history (pure; no Fabric calls)
  analyzers.py  deterministic analyzers; each returns opportunities with evidence
  impact.py     impact as measurement.py dicts: measured run history, imported measured CU, else Unknown
  assess.py     run analyzers, apply the six-question order, overlap-aware totals, rank, next action
  fixtures/     demonstration workspace, labelled as a fixture everywhere it appears
opportunities_service.py  RPC optimization_assess: reads only (list items, getDefinition, job history)
```

Rules the engine keeps:
- Deterministic, no AI. Static analysis parses code; it never runs it.
- Each opportunity: source, evidence, confidence, impact (classified), next step, validation, rollback.
- "Proven" only for identical normalized code with the same default lakehouse; otherwise "suspected".
- Overlaps: per workload only the strongest opportunity counts in totals; the others are marked as overlapping.
- Money is never shown as saved: CU at a configured price is "CU value", not realizable until a capacity changes.
- No Fabric job starts. getDefinition and job history are reads (they use no capacity job).

## 7. Initial acceptance tests (in `sidecar/tests/test_opportunities.py`)

| Brief criterion | Test |
| --- | --- |
| 1 identical duplicate processing | two notebooks with the same code but different comments/whitespace are one proven duplicate |
| 1 (no false positive) | same code, different default lakehouse is not proven |
| 2 redundant data movement | two pipelines copying the same source to the same sink |
| 3 unnecessary full refresh | scheduled notebook with whole-table overwrite; an unscheduled one or one with replaceWhere is not flagged |
| 10 no double counting | duplicate + overwrite on the same notebook count once in totals |
| 12 CU vs money | CU value at a price is labelled not realizable |
| 13/14 evidence | every opportunity carries evidence, source, confidence, validation and rollback |
| 16 no AI | engine imports no AI client (test inspects modules) |
| 17 safety | service makes only read calls on the fake client |
| Unknown never 0 | with no usage import, CU impact is Unknown with value None |
| Fixture labelling | demo result says fixture on the result and every opportunity |
