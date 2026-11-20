# Enterprise Savings Intelligence: review, plan and stories

Status: plan for review. Nothing in this document is built yet unless a row says **Built**.
Program: Optimize > Cost and savings, the optimization ledger and cross-device tracking
(Chris's master prompt, 2026-11-17). Story ids are **ESI-*phase**nn***.

This plan extends what exists. It does not add a second optimization database or a second
savings calculator. The shared store stays the central lakehouse's `fdopt_` table family
(`sidecar/fabric_desktop/optimize/`), the figures stay `measurement.py` measurements, local CU
avoided stays `calibration.py`, opportunities stay `opportunities/`, and assessment findings
come from the Fabric Excellence Assessment (`excellence/`, plan in
`docs/product/fabric-excellence-assessment-plan.md`, PR #2939).

Standards followed: `KratosDataGod/ai-commons` `AGENTS.md`, its active `standards/`, the
KratosForge project context, and the hard gates in
`prompts/kratosforge/mission-and-engineering-philosophy.md` (ai-commons PR #3). Section 9
lists the gates as they apply here.

## 1. Repository review (section 1 of the brief)

### 1.1 What exists and what this program reuses

| Brief area | Where it is today | State | How this program uses it |
| --- | --- | --- | --- |
| Shared lakehouse | `optimize/schema.py`, `setup.py`, `lakehouse.py`, `registry.py`; default name `FabricDesktop_Central`; table families via `schema.register(Family(...))` | **Built** (#186, #228). Tested against lakehouse folders on a PC and a fake Fabric API; not yet run on a live tenant (`docs/optimize/security.md`) | The authoritative store. New tables arrive as additive `fdopt_` migrations (version 2 onward), never a new database |
| Write rules | `optimize/shared.py`: insert-only MERGE on a content-derived `record_id`, batches of 5,000, retry after a lost commit race, run row written last, latest event wins | **Built** (#189) | Every ledger, lifecycle and sync row follows these rules. They already give idempotency and retry safety |
| Offline queue | `optimize/outbox.py`: SQLite on the PC, bounded (200 items, 30 days), backoff 60 s to 1 h, secrets removed from errors, dropped count shown | **Built** (OPT-006, #1726, #1733). Two kinds: `analysis`, `local_executions` | Becomes the durable local event queue. New kinds are added for ledger and lifecycle events |
| Installation identity | `optimize/installation.py`: random UUID per installation, optional label (80 chars), OS family only | **Built** | The installation id and the friendly device name ("Laptop", "Desktop"). No machine name, no hardware fingerprint |
| Local execution rows | `optimize/local_runs.py` → `fdopt_local_execution`: notebook writes, scheduled runs, metadata runs, Prove runs (CPU and peak memory only where measured, else empty) | **Built**, engine only (OPT-003, #356, #2040). Sent only on an explicit share (`optimize_share_local_runs`, approve + confirm); the Share button waits for a mockup | The execution record. Gains identity and definition-version columns (ESI-102) |
| Measurement shape | `measurement.py`: measured, estimated, scenario, unknown; unknown is `None`, never 0; scenario never summed; units kept apart; a price is never a charge | **Built** (FINOPS-001) | Every figure in the ledger and on the page is one of these. Evidence status on every metric comes from here |
| Local CU avoided | `calibration.py`: Fabric runs of the same workload only, measured preferred, median of 5 most recent within 90 days, scaled by rows when both sides have them; otherwise Unknown | **Built** (FINOPS-005, #257) | The only source of "CU avoided by running locally". Time on the PC is never turned into CU |
| Cost report | `costs.py` `report` via RPC `cost_report` (`estate_service.py`); UI `costPanel` in `app/ui/estate.js`, shown on Optimize > Cost and savings with badges and a "how it was worked out" drawer | **Built** (Preview) | Kept working through every phase. The redesign reads the same evidence, then adds the shared ledger |
| Pricing | `costs.DEFAULTS`: one `price_per_cu_hour` (0.18), `currency`, `python_cu`, `spark_cu`; SKU sizes F2 to F2048, P1 to P5 | **Built**, single price. Versioned scenarios are built, engine only (ESI-401, `savings/pricing.py`) | Replaced by versioned pricing scenarios (ESI-401). The single price becomes the first scenario, labelled as an assumption |
| Measured CU and billing | `usage/`: Capacity Metrics app exports (timepoint and compute pages) and Azure Cost Management exports (actual cost and FOCUS), stored on the PC, reconciled to runs by name and time | **Built** (FINOPS-003) | Measured CU for baselines; Cost Management rows are the evidence for **realized** savings; timepoints feed the capacity planner |
| Opportunities | `opportunities/` (model, inventory, analyzers, impact, assess, share), RPC `optimization_assess`; impact from measured job history and imported CU; one opportunity counted per workload by `ACTION_ORDER` | **Built** (OE-001, OE-003, OE-004 #2607) | Source of optimization candidates. Its overlap rule is the first layer of the double-count control |
| Recommendations lifecycle | `fdopt_recommendations` events: open, accepted, applied, dismissed, verified, superseded (`shared.TRANSITIONS`) | **Built** | Extended, not replaced, to the brief's lifecycle (section 4.4) |
| Assessment | Fabric Excellence Assessment, plan PR #2939, stories FEA-101 to FEA-604; findings keyed by control id plus resource fingerprint | **Built** in part (FEA-101 to FEA-105, FEA-109, FEA-203, FEA-301, FEA-302; Preview) | Findings create optimization records through the existing fingerprint, so repeat runs never duplicate them (ESI-601) |
| Settings history | `tenant_history.py` (`fdten_`), `capacity_history.py` (`fdcap_`): capacity SKU snapshots and changes from `GET /v1/capacities` | **Built** | Evidence that a capacity was actually resized or removed, for realized savings |
| Sync overhead | OPT-009: seconds, rows and commits per sync written as Measured rows in `fdopt_measurements` | **Built** except bytes | The "Fabric overhead incurred" line of net benefit. Bytes and any CU for OneLake I/O stay Unknown until measured |
| Capacity planner | OE-040 (demand model from imported timepoints), OE-041 (consolidation simulator) in `docs/optimize/engine-plan.md` | Planned | Section 12 integrates with these. This program does not build its own simulator |
| Sign-in | `auth.py`: MSAL with the Fabric CLI public client and WAM, shared token cache | **Built** | User identity comes from the signed-in token's tenant and object id claims. No app registration |
| Controlled endpoint precedent | `central/udf.py`, `central/client.py`: UDF_Platform records changes "as the person who called it, taken from Fabric's own context" | **Built**; deployed in live run 8 (`kf_tenant_test`) | Pattern for an optional collector (section 4.2, mode C) |
| Tests | 30+ `test_optimize_*`, `test_calibration*`, `test_costs_mutation`, `test_measurement*`, `test_usage_*`, `test_opportunities*` | **Built** | New tests extend these files' fixtures (local lakehouse folders, fake Fabric API) |

### 1.2 What the review found missing

1. **No user or tenant on any row.** `PROVENANCE` is `record_id, installation_id, created_at, app_version, schema_version`. Nothing says who ran or optimized something, which tenant it belongs to, or which capacity a local run would have used.
2. **No definition version on executions.** `calibration.py` accepts a `version`, but `fdopt_local_execution` has no column for it, so cross-device comparison can't check "same definition".
3. **`fdopt_savings`, `fdopt_cu_usage`, `fdopt_actions`, `fdopt_dependencies` are created empty.** OPT-004 (measurements and savings rows) and OPT-005 (dashboard) are planned, not built. There is no ledger, no attribution, no reporting period, no correction entry.
4. **No optimization record with a baseline.** A recommendation has states but no baseline measurement, post-change measurement, implementation reference, rollback or responsible user.
5. **No attribution across optimization and local execution.** `counts_for` stops several local runs counting one cloud run twice, and `ACTION_ORDER` stops overlapping opportunities counting twice, but nothing caps optimization benefit plus local avoidance at the original baseline.
6. **One price.** No SKU, region, effective date, reserved versus pay-as-you-go, or source. The 0.18 default is a typical published pay-as-you-go rate, not the customer's rate.
7. **Local runs leave the PC only on a confirmed share.** There is no ongoing sync of every execution.
8. **Shared reads are not a security boundary.** `shared.py` hides workspaces the person can't open, but anyone with Read all data reads every row, and every writer (Contributor) can read every row too. The brief's "contributing telemetry must not grant enterprise-wide view" cannot hold with direct writes alone (section 4.3).
9. **No cross-device read on the page.** Cost and savings reads only this PC (`cost_report`); nothing reads other installations' executions back.
10. **No reporting model or export** for the shared tables.

## 2. Decisions this plan takes, and the ones that need Chris

| # | Decision | Recommendation | Who decides |
| --- | --- | --- | --- |
| D1 | Data model | Extend `fdopt_` with additive migrations; publish the brief's star schema (`dim_*`, `fact_*`) as a documented reporting layer over those tables (section 4.1), not as a second set of physical tables | This plan (reuse rule) |
| D2 | Ingestion | Keep direct insert-only MERGE from each desktop (mode A). Add a per-installation staging mode (B) only if the live check shows commit contention. A collector User Data Function (C) for organizations that must separate writers from readers | A: this plan. C: Chris (new Fabric item, typed go) |
| D3 | User identity | Store the signed-in token's tenant id and Entra object id (GUIDs). Never email or UPN. Display names only when an organization turns on contributor recognition | Chris, with a Security read |
| D4 | Automatic sync of executions | Off by default. One opt-in per installation per lakehouse (showing exactly what is sent, as the share confirm does today); after that, executions sync automatically | Chris (changes the confirm-each-share rule from #2040) |
| D5 | Leaderboard | Teams, workspaces and capacities by verified benefit only. Individual names off unless the organization turns them on | Chris |
| D6 | Power BI model | A documented reporting schema in Phase 5; a published semantic model is a separate go (OPT-005 already lists it as Chris's decision) | Chris |

Everything else in this plan follows existing rules and needs no new decision.

## 3. Principles carried from the existing code

- Unknown stays Unknown (`None`), never 0. Missing evidence never becomes zero savings.
- **One totals rule** (the one `measurement.total` already applies): measured and estimated
  figures of the same metric may be added, and the total takes the weaker label (any estimated
  part makes it Estimated) and shows how much of it is measured. Scenario figures (capacity
  value, avoided future cost) are never added to either. Unknown parts are left out and the total
  is marked Incomplete with how many were left out. So the brief's headline "Total CU-hours
  avoided" is measured plus estimated, labelled accordingly, with its verified (measured) part
  beside it.
- CU is never derived from elapsed time. Local CU avoided only from Fabric runs of the same workload (`calibration.py`).
- A price is not a charge. Capacity value is not cash. Realized savings need billing or capacity-change evidence.
- Rows are insert-only and idempotent. Corrections are new rows that point at what they correct.
- No SQL or code text leaves the PC; only fingerprints. No email addresses, tokens or table contents.
- The app works fully without the shared lakehouse and without any AI service.

## 4. Architecture

```
KratosForge Desktop (each installation)
  local_runs.py / Prove / metadata run_log / schedules   ← execution events (immutable)
  opportunities/, excellence/, X-ray                     ← findings → optimization records
  savings/ (new, pure): baselines, attribution waterfall, pricing scenarios, ledger events
        │
        ▼
  optimize/outbox.py (SQLite, durable, bounded, backoff) ← queue, sync states
        │  mode A: insert-only MERGE per table (today)
        │  mode B: per-installation staged files + merge step (only if needed)
        │  mode C: collector UDF writes as the caller (optional, needs Chris's go)
        ▼
Central lakehouse (FabricDesktop_Central), fdopt_ family, schema v2+
  executions, optimizations, baselines, measurements, savings ledger, attribution, pricing, sync, audit
        │
        ▼
Reporting layer: documented star schema over fdopt_ (views or semantic model), CSV export
Optimize > Cost and savings (redesigned), capacity planner (OE-040/041), Assessment
```

`savings/` is the one new package. It holds only what doesn't exist anywhere today (baseline
versioning, the attribution waterfall, pricing scenarios and ledger event construction), is
pure like `calibration.py`, and writes through `optimize/shared.py`. It doesn't compute CU
itself: it takes `measurement.py` figures from `calibration.py`, `usage/` and Prove.

### 4.1 Data model: the brief's tables mapped onto `fdopt_`

New tables and columns arrive as `fdopt_` migrations. Each step only adds a table or a nullable
column, as `schema.py` requires. Reporting names (`dim_`, `fact_`) are the documented layer over
them (ESI-501).

| Brief table | Physical table | New or existing |
| --- | --- | --- |
| `dim_organization`, `dim_tenant` | `fdopt_config` (organization scope key) plus new `tenant_id` provenance column | Column added (v2) |
| `dim_user` | New `user_id` (Entra object id) and `tenant_id` columns on rows a person causes; optional `fdopt_contributors` (opt-in display names) | Columns v2; table v3, only with D5 |
| `dim_device` | `fdopt_installations` (label = device alias, os, app version) | Existing |
| `dim_capacity`, `dim_workspace` | `fdcap_snapshot` (capacity SKU, region, state), workspace ids on rows; names from `fdopt_artifacts` | Existing (read, never written by this family) |
| `dim_artifact` | `fdopt_artifacts` + `artifact_key` | Existing |
| `dim_execution_engine` | `engine` column values (documented list) | Existing column |
| `dim_optimization_category` | Documented list (brief section 5 categories mapped to `opportunities.model.CATEGORIES`) | Reporting layer only |
| `dim_pricing_scenario`, `pricing_history` | New `fdopt_pricing` (scenario id, SKU, region, currency, rate, model reserved/PAYG/actual, effective from/to, source, confidence) | New (v2) |
| `dim_date` | Reporting layer | Reporting layer only |
| `fact_execution`, `fact_execution_resource_usage` | `fdopt_local_execution` plus new columns: `user_id`, `tenant_id`, `workspace_id`, `capacity_id`, `definition_version`, `input_bytes`, `output_bytes`, `reads`, `writes`, `transfer_seconds` | Existing table, columns v2 |
| `fact_fabric_comparison` | `fdopt_measurements` rows of metric `fabric_job_cu_avoided` (calibration output, with baseline run ids in assumptions) plus new `comparison_id`, `baseline_version` | Existing table, columns v2 |
| `fact_optimization`, `optimization_status_history` | `fdopt_recommendations` events, extended: `optimization_id`, `category`, `original_ref`, `optimized_ref`, `rollback`, `user_id`, `installation_id`, `version` (optimistic concurrency) | Existing table, columns v2 |
| `optimization_artifact_link` | New `fdopt_optimization_link` (optimization id → artifact, workspace, capacity) | New (v2) |
| `optimization_dependency` | `fdopt_dependencies` (relation = `optimization_order`, `supersedes`) | Existing, first used |
| `optimization_baseline`, `fact_optimization_measurement` | New `fdopt_baseline` (baseline id, version, workload key, definition version, window, runs, measurement ids, superseded by) | New (v2) |
| `optimization_validation` | New `fdopt_validation` (validation id, optimization id, before and after measurement ids, output equivalence check, result) | New (v2) |
| `fact_savings_ledger` | `fdopt_savings`, re-specified as the append-only ledger (section 4.5). It has never been written, so its columns can be fixed before first use | Existing empty table, columns v2 |
| `savings_attribution` | New `fdopt_attribution` (execution or period, workload, baseline version, layer, CU, ledger event id) | New (v2) |
| `fact_capacity_utilization` | `fdopt_cu_usage` (from imported timepoints) | Existing empty table, first written |
| `fact_sync_event`, `device_sync_state` | New `fdopt_sync` (installation, kind, rows, commits, seconds, outcome) beside the existing sync `fdopt_measurements`; local state stays in `outbox_state` | New (v2) |
| `fact_assessment_finding` | `fdopt_findings` (fingerprint) | Existing |
| `fact_remediation_action` | `fdopt_actions` | Existing empty table, first written |
| `schema_version`, `audit_event` | `fdopt_schema_version`, `fdopt_audit` | Existing |

### 4.2 Ingestion: why direct MERGE stays the default

The brief asks for a supported method with safe concurrent ingestion, idempotency, retries,
durable delivery, schema validation, access control, conflict handling and auditability.

- **Mode A, direct insert-only MERGE (today).** Each desktop writes Delta commits through
  OneLake's ADLS-compatible API with delta-rs. A commit is a create-if-absent write of the next
  log file, so two writers can't both win the same version; the loser retries
  (`shared.py`, plus the Windows log-race fixes #204, #214, #221). Rows are idempotent by
  `record_id`, so retries and second devices add nothing. Delivery is durable through the
  outbox. Schema is checked before writing (`setup.py`; a newer schema is read-only).
  Costs: needs Contributor on the workspace, so writers can read everything (4.3), and many
  small commits make many small files (maintenance is Lakehouse optimize's existing job).
  **Not yet run on a live tenant.** ESI-108 is that check.
- **Mode B, per-installation staging.** Each installation writes Parquet batches to
  `Files/inbox/<installation_id>/`; a merge step folds them into the tables with the same MERGE.
  No contention between installations. The merge step needs somewhere to run (a desktop with
  rights, or a Fabric notebook that uses capacity). Built only if ESI-108 shows contention.
- **Mode C, collector User Data Function.** Desktops call a function with a batch; it validates
  the schema and tenant, stamps the caller's identity from Fabric's own context (as
  `UDF_Platform` does) and writes. Writers then need only Execute on the function, not
  Contributor, which is the only way the brief's access rule fully holds. It is a new Fabric
  item and uses capacity on every call, and whether a UDF can write Delta tables to a
  lakehouse at the needed volume must be checked first. **Needs Chris's typed go** (ESI-109).

Every mode records its own overhead (OPT-009 rows) so net benefit subtracts it. The shared
lakehouse is never described as free to run.

### 4.3 Identity, tenancy and access

- **Installation**: the existing random UUID; label = device alias. Several people on one
  installation and one person on several installations both work, because the user id is per
  row, not per installation.
- **User**: `tenant_id` (`tid`) and `user_id` (`oid`) read from the signed-in token's claims at
  write time, never typed. No email, UPN or display name on rows (D3).
- **Tenant isolation**: a lakehouse records its tenant on setup; an installation signed in to
  another tenant refuses to write to it and reads nothing from it (ESI-104, test scenario 15).
- **Who sees enterprise-wide data**: Fabric's own permissions. With mode A, anyone who can write
  can read every row; the docs and the page say so plainly. Restricting readers uses the
  existing OneLake security path (`roles.py`) for read-only consumers; separating writers
  from readers needs mode C. The page shows enterprise totals only when the person can read
  them, and shows "your installations" otherwise.
- **Local queue**: the outbox stays in the user's profile; errors are stored with secrets
  removed; tokens are never logged (existing `redact`).

### 4.4 Optimization lifecycle

The brief's states map onto the existing recommendation events, extended additively. Old
values stay readable.

| Brief state | Stored as | Today |
| --- | --- | --- |
| Discovered | `open` | Exists |
| Proposed | `proposed` | New |
| Approved | `accepted` | Exists |
| Implemented | `applied` | Exists |
| Validating | `validating` | New |
| Verified | `verified` | Exists |
| Monitoring | `monitoring` | New |
| Rejected | `dismissed` (with reason) | Exists |
| Reverted | `reverted` | New |
| Superseded | `superseded` | Exists |
| Accepted risk | `accepted_risk` | New |

Each state change carries `version` (the version it was made against). A change made against
an older version than the latest is refused with a conflict that shows the newer change, so
one developer never silently overwrites another (optimistic concurrency, test scenario 9).

### 4.5 Savings ledger and the attribution waterfall

**Ledger event** (`fdopt_savings`, append-only): event id, event type (`accrual`,
`correction`, `reversal`, `realization`), optimization id, execution id, reporting period
start and end, scope (tenant, workspace, capacity, artifact), measurement source,
classification, CU-hours, money, currency, confidence, pricing version, baseline version,
attribution category (`optimization`, `local_execution`, `capacity`), `corrects` (event id),
created at, user id, installation id. The `record_id` is derived from (event type, execution
id, workload key, attribution layer (the optimization id, or `local`), baseline version, pricing
version, `corrects`), so a second device or a retry produces the same row, while two different
corrections, or a re-valuation under a new pricing version, stay separate rows.

**One grain.** Every accrual is per execution: one observed run of a workload, in Fabric or on a
PC, by its stable execution id. A reporting period (day, week, month) is only a grouping of
execution accruals by run time; nothing is accrued per period on its own, so a local run can
never be counted once as an execution and again as part of a monthly figure. The "estimated
recurring monthly benefit" is a projection computed at read time from recent executions and run
frequency; it is shown as Estimated and is never written to the ledger.

**Waterfall, per execution:**

1. Baseline B = the workload's original cloud CU per run, from a versioned `fdopt_baseline`.
2. Optimizations are applied in a fixed order (implementation time, then `ACTION_ORDER`).
   Each takes its measured reduction from what remains: O₁ = B − after₁, O₂ = after₁ − after₂,
   and so on. The remaining optimized demand R = B − ΣOᵢ.
3. If the run executed locally, local avoidance L ≤ R, taken from `calibration.py` against the
   **optimized** baseline (R), not the original one.
4. Gross avoided ≤ B, always. Fabric overhead (OneLake I/O, sync, ingestion) is subtracted to give
   net. Unknown overhead keeps net marked incomplete rather than equal to gross.
5. Each layer is one `fdopt_attribution` row and one ledger event. A layer whose input is
   Unknown records Unknown and is left out of totals.

Edge cases:

- **Regression.** If an optimization's measured after is higher than its before (Oᵢ < 0), the
  layer is recorded as a negative measured value with event type `regression`, the optimization
  moves back to Validating, and later layers start from the higher figure. Gross avoided is still
  never more than B and never less than 0.
- **Local run with only pre-optimization Fabric runs.** `calibration.py` then estimates against
  the original baseline. If the optimization's after is measured, L = min(calibrated, R). If it
  isn't measured yet, the split between optimization and local is unknown: the run records one
  gross figure, capped at B, under attribution `unattributed`, and is re-attributed (correction
  events) once the after is measured. The total is the same either way, so nothing is counted
  twice.
- **Reversal.** A reverted optimization stops accruing from its revert time. Accruals for runs
  before the revert stay, because those runs really used less. A reversal event is written only
  to undo accruals that were wrong (for example an optimization that never actually deployed),
  each one pointing at what it undoes through `corrects`.

**Critical acceptance test (ESI-303)**: B = 10 CU-h; an optimization measured at 6 CU-h gives
O = 4; the next run executes locally, L = up to 6; gross = 10, never 14. The same execution
synced from a second desktop has the same record ids and adds nothing. A later assessment
detects the same fingerprint, finds the optimization already verified, and writes no new
accrual.

**Continuing benefit**: accruals come only from observed runs (the one grain above), never from
projected lifetime totals. When a workload stops running, retires or its
definition changes materially, accruals stop and the baseline is re-evaluated (new baseline
version; old events stay, tied to the old version).

### 4.6 Financial measures

| Measure | Classification rule | Source |
| --- | --- | --- |
| A. Fabric CU avoided | Measured when both before and after are measured; else Estimated from calibration; else Unknown | `fdopt_measurements`, ledger |
| B. Capacity headroom | Estimated: CU avoided as a share of capacity over time, from timepoints, never from totals alone | `fdopt_cu_usage`, OE-040 |
| C. Estimated capacity value | Scenario: CU × a named pricing scenario. Never labelled savings | `fdopt_pricing` |
| D. Avoided future capacity cost | Scenario from the planner's forecast, with its assumptions | OE-041 |
| E. Realized savings | Measured only: a Cost Management export showing lower billed cost, or a capacity SKU change or removal in `fdcap_` history, recorded at capacity scope with a person's confirmation of what caused it | `usage/cost_management.py`, `capacity_history.py` |
| F. Net financial benefit | E minus attributable costs (sync and ingestion overhead, collector calls, any staged-merge runs, organization-entered KratosForge costs) | Ledger |

**Allocating realized savings.** Realized savings are recorded once, at the capacity and period
they were billed for. By default they are not split across optimizations. When an organization
wants a split, it is pro rata to verified (measured) CU avoided on that capacity in that period,
the method is recorded on each allocation row, and the allocations always add up to exactly the
realized figure, never more.

Developer time saved is kept apart and never turned into money unless the organization supplies
a valuation model.

## 5. Experience

Optimize > Cost and savings keeps its place in the rail and its light shell. Every new screen
starts as a mockup reviewed by the UX thread, then Chris reacts before it is built, and it
ships behind Preview until tested. The page answers four questions in order: what did we save,
how, how sure are we, what next.

- **Overview**: the brief's ten headline figures, each with its evidence badge, grouped so
  every total follows the one totals rule in section 3 (measured and estimated add under the
  weaker label, scenario never adds, unknown is left out and marks the total Incomplete).
- **Savings over time**: daily, weekly, monthly, cumulative; local versus optimization;
  estimated versus verified; 7, 30, 90 days, year to date, custom.
- **By source**: device, user (only when allowed), workspace, capacity, artifact, engine,
  category, period.
- **Optimization impact**: before, after, change, CU reduction, runs per month, monthly benefit,
  verification status, developer (when allowed).
- **Leaderboard**: verified benefit only, so inflated estimates and repeated low-value runs earn
  nothing (D5).
- **Verification drawer**: baseline runs, comparison runs, source telemetry, method, pricing,
  confidence, attribution layers, validation history. Every total opens to its ledger rows.

## 6. Stories

Every story is one PR unless it says otherwise. "Gate" lists what must happen before merge
beyond the standard flow (owner → coordinator → Quality → Release management). Files owned by
another thread are changed through that thread: `optimize/shared.py` and `optimize/schema.py`
(Optimize lakehouse), `opportunities/` (Optimization engine), `excellence/` (Fabric Excellence
Assessment), `calibration.py` and `costs.py` (FinOps rules, Quality reads the contract).

### Phase 1: Shared telemetry foundation

**ESI-101 Schema v2 for `fdopt_`.** **Built** (#2962): the new nullable columns and the six new tables; writes use the columns of the version the lakehouse is at, and other families no longer wait for Optimize's upgrade. The provenance `tenant_id` and other identity columns wait for ESI-102's decision. Additive migration: provenance `tenant_id`; execution,
optimization, ledger and sync columns from 4.1; new tables `fdopt_pricing`,
`fdopt_optimization_link`, `fdopt_baseline`, `fdopt_validation`, `fdopt_attribution`,
`fdopt_sync`. Owner: Optimize lakehouse thread (schema.py), specified here.
- Acceptance: a v1 lakehouse upgrades to v2 with only added tables and nullable columns; v1 rows
  read back unchanged; a v1 app opens a v2 lakehouse read-only; the migration is safe to rerun.
- Tests: scenario 20 (schema upgrade), half-finished migration finished by a rerun.
- Rollout found in review: today a lakehouse at an older `fdopt_` version is in the Upgrade state,
  and `central_family` refuses other families (access cache, `fdcap_`, `fdten_`) until someone
  confirms the upgrade, while an app at version 2 builds rows with version 2 columns. So ESI-101
  also makes writes use the columns of the version the lakehouse is at (or queue them with a
  clear "upgrade needed" state), keeps other families working on an older core version, and
  documents that installations still on version 1 open an upgraded lakehouse read-only.
- Gate: Security read (Lakehouse write).

**ESI-102 Identity on rows.** Read `tid` and `oid` from the token claims (`auth.py`), never from
settings; add them, plus workspace, capacity and definition version, to rows written by
`local_runs.py` and `shared.py`. No email, UPN or name.
- Acceptance: rows carry ids from the token; a changed display name changes nothing; two
  people on one installation produce rows with different user ids; one person on two
  installations produces the same user id.
- Tests: scenarios 1, 2, 3.
- Gate: Security read (tokens), D3.

**ESI-103 Durable execution events.** Every supported local execution is written to the outbox
as it finishes (new kind `execution`), once the installation has opted in (D4). Before
opt-in, nothing leaves the PC; the existing share path keeps working.
- Acceptance: executions survive an app restart while offline; sync after reconnect sends each
  once; a crash between send and acknowledge resends with the same record ids and adds
  nothing; the opt-in text lists exactly the columns sent.
- Tests: scenarios 6, 7, 8.
- Gate: Security read (file I/O, sync), D4.

**ESI-104 Tenant and scope guard.** The lakehouse's tenant recorded at setup; writes and reads
refused for another tenant; organization scope key in `fdopt_config`.
- Acceptance: a token from tenant B can't write to or read from a tenant A lakehouse through the
  app; the refusal is audited without the token.
- Tests: scenario 15, partial permissions (scenario 14: read without write shows read-only).
- Gate: Security read.

**ESI-105 Sync states.** **Built** (engine and RPC, no screen yet). Map outbox state to Synced, Pending, Synchronizing, Failed, Conflict,
Offline, per kind and lakehouse, through the existing `optimize_status` RPC.
- Acceptance: every state reachable in tests; bounded backoff unchanged; dropped items still
  shown.

**ESI-106 Cross-device read.** RPC that reads executions from every installation in the
person's visible workspaces, grouped by workload, with per-device comparison.
- Acceptance: a laptop's run appears on the desktop after sync; the same run from two devices
  appears once; another workspace's runs stay hidden.
- Tests: scenarios 2, 4, 8.

**ESI-107 Security and data docs.** Update `docs/optimize/security.md` for the new columns
(user and tenant ids are identifying), the writer-can-read limit of mode A, and retention.

**ESI-108 Live concurrency check.** Two installations writing to a lakehouse in
`kf_tenant_test` at once: commits, retries, file counts, timings, overhead.
- Gate: Chris's typed "yes, run it" (creates or writes a Fabric resource).

**ESI-109 Collector decision.** Feasibility note for mode C (UDF writing Delta, payload limits,
CU per call) from documentation, then a recommendation. Building it needs Chris's typed go.
**Note written**: [esi-109-collector-feasibility.md](esi-109-collector-feasibility.md). Building it still needs Chris's typed go.

### Phase 2: Unified execution savings

**ESI-201 Comparison rules.** **Built** (engine only, no writes). `calibration.py` (`calibrate_comparable`, `enrich`). Extend the calibration inputs with definition version, input scope
and data volume checks: a Fabric run counts as a baseline only when the logical workload,
definition version and input scope match and its metrics are valid. Change through FinOps
rules in `calibration.py` (additive).
- Acceptance: a definition change makes older baselines not count; no baseline gives Unknown
  with its reason; a later Fabric run enriches earlier Unknown executions with a new
  measurement row (the old row stays).
- Tests: scenario 13, baseline version change (12).

**ESI-202 Overhead per execution.** **Built** (engine only, no writes). `savings/overhead.py`. Data transfer seconds and bytes, sync overhead, and any
OneLake operations, as separate measurements; Unknown where not measured.

**ESI-203 Execution ledger accruals.** Gross, overhead and net CU avoided per execution as
ledger events, through the attribution waterfall (single layer until Phase 3). This is the
ledger's only accrual grain (section 4.5).
- Gate: the 2026-11-06 decision stands: no savings rows are written until a measured Fabric
  baseline source exists for the workload (imported Capacity Metrics CU matched to its runs, or
  a measured Prove run). Until then the engine computes and shows estimates on this PC and writes
  nothing to the ledger.
- Acceptance: net ≤ gross; Unknown overhead marks net incomplete; resync adds nothing.

### Phase 3: Optimization ledger

**ESI-301 Optimization records.** Lifecycle extension (4.4) with version-aware updates; links
to artifacts, workspaces and capacities; original and optimized references (fingerprints,
never code text); rollback information; responsible user and device. Through the Optimize
lakehouse thread (`shared.py`).
- Tests: scenario 9 (concurrent updates), 10 (reversal), 11 (retirement).

**ESI-302 Baselines and validation.** **Built** (engine only, no writes). `savings/baselines.py`. Versioned baselines with measurement windows; before and
after comparison on equivalent inputs; output equivalence where measurable; validation result
moves Validating → Verified or back. CU change only from measured CU or a labelled estimate,
never from time alone.

**ESI-303 Attribution waterfall.** **Built** (engine only, no writes). `savings/attribution.py`: ordered layers (4.5), caps at the
baseline, incremental allocation across several optimizations.
- Acceptance: the critical acceptance test passes exactly; overlapping optimizations never sum
  past the baseline; a reversal writes reversal events that net the earlier accruals to zero.
- Tests: scenarios 16, 17, 10, 19, plus a property test that gross ≤ baseline for any order.

**ESI-304 Recurring benefit.** **Built** (engine only, never written). `savings/recurring.py`. Read-time projection of monthly benefit from recent executions and
run frequency, labelled Estimated and never written to the ledger; period figures are groupings
of ESI-203 execution accruals. Stop projecting on retirement; re-baseline on material change.
- Tests: scenarios 11, 12.

### Phase 4: Financial intelligence

**ESI-401 Pricing scenarios.** **Built** (engine only; nothing writes `fdopt_pricing` yet). `savings/pricing.py`. `fdopt_pricing` and a local equivalent: SKU, region, currency,
effective dates, reserved or pay-as-you-go or actual billing, customer rate, source and
confidence. The current single price becomes a scenario labelled as an assumption.
- Tests: scenario 18 (price change applies only from its effective date; history keeps its
  pricing version).

**ESI-402 Capacity value and headroom.** Measures B and C from 4.6, with time-based headroom
from imported timepoints.

**ESI-403 Realized savings.** Measure E from Cost Management exports and `fdcap_` SKU changes,
linked to optimizations by a confirmed person action; measure F.
- Acceptance: nothing is labelled realized without billing or capacity-change evidence; an F64
  with lower utilization shows value and headroom, not realized savings.

**ESI-404 Corrections.** **Built** (engine only, no writes). `savings/corrections.py`. Writing its events waits on an additive `fdopt_savings` change (layer, CU hours, classification, result CU hours and classification, layer removed, reason), co-signed with Lakehouse optimize. Correction and reversal events with `corrects`; reconciliation report
that rebuilds totals from the ledger and compares.
- Tests: scenario 19.

### Phase 5: Enterprise reporting

**ESI-501 Reporting schema.** **Documented** in [savings-reporting-schema.md](savings-reporting-schema.md). Documented star schema (`dim_*`, `fact_*`) over `fdopt_`, with
measure definitions; SQL views or a semantic model only after D6.

**ESI-502 Cost and savings mockup.** Section 5's screens, light shell, via the UX thread, then
Chris's reaction. No build before that.

**ESI-503 to ESI-506 Build (Preview), one PR each**: overview and over-time charts; by source
and optimization impact; verification drawer; leaderboard (after D5). The existing cost panel
and its browser tests keep working.

**ESI-507 Exports.** CSV for the eight reports in the brief (executive summary, workspace,
capacity, developer contribution, monthly statement, audit history, estimated versus
verified, capacity readiness), from the same ledger queries the page uses.

### Phase 6: Continuous optimization

**ESI-601 Assessment to optimization.** An assessment finding (FEA) or opportunity (OE) creates
an optimization record keyed by its fingerprint; a repeat detection links to the existing record
instead of creating one; resolution after verification is reported back to the assessment.
Coordinated with the Fabric Excellence Assessment thread.

**ESI-602 Capacity planner inputs.** Verified and estimated optimization demand fed to OE-040 and
OE-041 as separate inputs (verified first). The planner, not this program, makes any
right-sizing recommendation, and it needs peak, concurrency and throttling evidence.

**ESI-603 What to optimize next.** Rank open opportunities by evidence-backed benefit, using the
existing `ACTION_ORDER` and impact figures.

## 7. Test matrix (section 16 of the brief)

| # | Scenario | Story |
| --- | --- | --- |
| 1 | One user, one desktop | ESI-102 |
| 2 | One user, two desktops | ESI-102, ESI-106 |
| 3 | Several users, one lakehouse | ESI-102 |
| 4 | Several workspaces | ESI-106 |
| 5 | Several capacities | ESI-402 |
| 6 | Repeated sync | ESI-103 |
| 7 | Offline then sync | ESI-103 |
| 8 | Duplicate submissions | ESI-103, ESI-106 |
| 9 | Concurrent optimization updates | ESI-301 |
| 10 | Optimization reversal | ESI-301, ESI-303 |
| 11 | Workload retirement | ESI-301, ESI-304 |
| 12 | Baseline version change | ESI-201, ESI-304 |
| 13 | Missing Fabric comparison | ESI-201 |
| 14 | Partial API permissions | ESI-104 |
| 15 | Cross-tenant access | ESI-104 |
| 16 | Local and optimization overlap | ESI-303 |
| 17 | Several optimizations on one workload | ESI-303 |
| 18 | Price change | ESI-401 |
| 19 | Historical correction | ESI-404 |
| 20 | Schema upgrade | ESI-101 |

All run against local lakehouse folders and the fake Fabric API, as the existing Optimize tests
do. Only ESI-108 touches a tenant.

## 8. Risks

| Risk | Mitigation |
| --- | --- |
| Inflated savings from overlapping sources | Waterfall capped at the baseline; property test; reconciliation report |
| Missing evidence shown as zero or as a saving | `measurement.py` rules; Unknown kept out of totals and shown as such |
| Commit contention with many desktops | ESI-108 live check; mode B ready as a fallback |
| Writers can read everything (mode A) | Stated plainly in docs and on the page; mode C for organizations that need separation |
| Personal data in a shared table | Ids only; no email, UPN, names, code text; contributor names only by organization choice |
| Small-file growth from many commits | Existing table maintenance (Lakehouse optimize thread) |
| Ownership overlap | Changes to owned files go through their threads; this program owns `savings/` and the plan |
| Shared lakehouse costs capacity | Overhead measured (OPT-009) and subtracted; never described as free |

## 9. Gates before any of this touches Fabric

- Creating or writing the shared Optimization Lakehouse in a tenant, and any live run, needs
  Chris's typed "yes, run it", in `kf_tenant_test` only.
- No new Fabric item (collector UDF, staged-merge notebook, semantic model) without Chris's go.
- No app registration: sign-in stays the Fabric CLI public client with WAM.
- Usage telemetry to KratosForge stays opt-in and off; this program sends nothing to KratosForge,
  only to the organization's own lakehouse, and only after the opt-in in D4.
- No savings figure appears in docs or release notes until it is measured in the test workspace.
