# Savings reporting schema (ESI-501)

The reporting layer for Enterprise Savings Intelligence: the `dim_` and `fact_` names a report,
export or semantic model uses, each defined over the `fdopt_` tables in the shared Optimization
Lakehouse (`sidecar/fabric_desktop/optimize/schema.py`, schema version 2). Nothing here is a new
physical table. Whether this becomes SQL views or a Power BI semantic model waits for Chris's
decision D6 (plan section 2); until then this document is the contract both would implement.

Plan: [enterprise-savings-intelligence-plan.md](enterprise-savings-intelligence-plan.md), sections
4.1 (table mapping), 4.5 (ledger and waterfall) and 4.6 (financial measures).

## Rules every report keeps

1. **One accrual grain.** Savings are accrued per execution (one observed run of a workload, by
   its stable `execution_id`) and per attribution layer. A day, week or month is a grouping of
   those rows by run time, cut in UTC. Nothing is accrued per period, so nothing is counted
   twice.
2. **Classifications never mix silently.** Every figure carries `classification`: Measured,
   Estimated, Scenario or Unknown. Measured and Estimated may be added. The sum is labelled as
   its weakest part and shows the measured part beside it. Scenario figures (prices applied to
   CU) are only ever added to other Scenario figures. Unknown is null, never 0, and is counted
   beside a total ("3 runs not known"), not inside it.
3. **Gross never exceeds the baseline.** Per execution, the layers (optimizations in order, then
   this PC, then unattributed) add up to at most the workload's baseline CU per run.
4. **Append-only.** Ledger rows are never edited. A correction or reversal is a new row naming
   the row it corrects (`corrects`). Each layer's rows form a chain from its accrual through
   each correction. The layer's current value is what the chain's head (the row nothing corrects)
   says it is now: `result_cu_hours` and `result_classification` on a correction or reversal, or
   the accrual's own figure when nothing corrects it. Never add a chain's rows to get the value:
   when a correction withdraws a figure (the layer became Unknown, or was removed), the rows add up
   to 0 while the layer is Unknown, and a sum would show 0 as Measured. The sum of a chain is only
   a check that the ledger adds up (`savings/corrections.py`, `reconcile`).
5. **Realized savings need billing evidence.** Only a Cost Management export or a capacity SKU
   change, confirmed by a person, is labelled realized (measure E). Lower utilization on the same
   SKU is capacity value and headroom, never realized savings.
6. **Ledger only from a measured baseline.** Until a workload has a measured Fabric baseline
   (decision of 2026-11-06), its figures are computed and shown on the PC but not written, so
   they never appear in these facts.
7. **Projections are read-time.** The recurring monthly benefit is computed when a report is
   read (`savings/recurring.py`), always Estimated, and never stored as a fact.

## Common columns

Every `fdopt_` row carries provenance: `record_id` (the insert-only MERGE key), `installation_id`
(which PC wrote it), `created_at`, `app_version`, `schema_version`. Times are ISO 8601 UTC
strings. Who did something (tenant and user ids) is not stored until decision D3; the
`dim_user` and `dim_tenant` rows below are placeholders until then.

## Dimensions

| Dimension | Key | Source | Notes |
| --- | --- | --- | --- |
| `dim_date` | `date` (UTC) | Generated | Day, ISO week, month, quarter, year. Facts join on the UTC date of their time column. |
| `dim_device` | `installation_id` | `fdopt_installations` | `label` (device alias), `os`, `registered_at`. The latest row per installation wins. |
| `dim_workspace` | `workspace_id` | `workspace_id` on rows; names from `fdopt_artifacts` | Name is display only; the id is the key. |
| `dim_capacity` | `capacity_id` | `fdcap_snapshot` (read, never written by this family) | SKU, region, state. |
| `dim_artifact` | `artifact_key` | `fdopt_artifacts` | `artifact_key` is built from workspace and item ids, never display names. |
| `dim_workload` | `workload_key` | `workload_key` on measurements and ledger rows | One logical workload across devices and engines. |
| `dim_execution_engine` | `engine` | `fdopt_local_execution.engine` | Documented list of engine values. |
| `dim_optimization` | `optimization_id` | `fdopt_recommendations` (latest event per optimization by `version`) | `category`, state, `original_ref` and `optimized_ref` are fingerprints, never code. |
| `dim_optimization_category` | `category` | Documented list mapped to `opportunities.model.CATEGORIES` | Reporting layer only. |
| `dim_pricing_scenario` | `pricing_version` | `fdopt_pricing` | Scenario, SKU, region, currency, rate per CU hour, model, `effective_from` (inclusive), `effective_to` (exclusive, empty = still in force), source, confidence. |
| `dim_baseline` | `baseline_version` | `fdopt_baseline` | Workload, definition version, measurement window, runs, `superseded_by`. |
| `dim_user`, `dim_tenant` | (after D3) | (after D3) | Not available. Reports group by device until then. |

## Facts

| Fact | Grain | Source | Measures |
| --- | --- | --- | --- |
| `fact_execution` | One run | `fdopt_local_execution` | `seconds`, `rows`, `bytes`, `cpu_seconds`, `peak_memory_bytes`, `input_bytes`, `output_bytes`, `reads`, `writes`, `transfer_seconds` (null where not measured). |
| `fact_fabric_comparison` | One comparison of a run with Fabric runs of the same workload | `fdopt_measurements` where `metric = 'fabric_job_cu_avoided'` | `value` (CU), `classification`, `baseline_version`, `comparison_id`; the Fabric runs it rests on are in `assumptions`. |
| `fact_savings_ledger` | One event per execution and attribution layer | `fdopt_savings` | `value` (CU hours avoided, signed: regressions and corrections can be negative), `event_type` (accrual, regression, correction, reversal), `attribution` (optimization id, `local`, `unattributed`), `classification`, `baseline_version`, `pricing_version`, `corrects`, `money` and `currency` (Scenario only); on corrections and reversals `result_cu_hours`, `result_classification`, `layer_removed` and `reason`. The last four aren't columns of `fdopt_savings` yet (see "Not in this layer yet"). |
| `fact_attribution` | One waterfall layer of one execution | `fdopt_attribution` | `cu_hours`, `layer`, `layer_order`, `classification`, `ledger_event_id` (joins to the ledger). |
| `fact_optimization_event` | One lifecycle event | `fdopt_recommendations` | State changes by `version`; a change made against an older version is a conflict, not an overwrite. |
| `fact_validation` | One before-and-after comparison | `fdopt_validation` | `result`, `outputs_match`, before and after measurement ids. |
| `fact_capacity_utilization` | One imported timepoint window per item and operation | `fdopt_cu_usage` | `cu_seconds`, `classification` (Measured from Capacity Metrics). |
| `fact_sync_event` | One sync of one installation | `fdopt_sync` | `rows`, `commits`, `seconds`, `outcome`. |
| `fact_assessment_finding` | One observation of a finding | `fdopt_findings` | Count of observations per `fingerprint`, first and last `observed_at`, `severity`, `rule_id`. |

## Measures

The letters match plan section 4.6.

| Measure | Definition | Classification |
| --- | --- | --- |
| **A. Fabric CU avoided, gross** | Per execution, the sum over its layers of each layer's current value (rule 4: the chain head's result), at most the baseline per execution | Weakest of the layers' current classifications; Unknown layers counted beside, never as 0 |
| **A. Fabric CU avoided, net** | Gross minus overhead events (OneLake I/O, sync) for the same execution | Incomplete where overhead is Unknown |
| **A by source** | Gross split by `attribution`: each optimization, `local` (this PC), `unattributed` (split waits for an after measurement) | As above |
| **B. Capacity headroom** | CU avoided as a share of capacity over time, from `fact_capacity_utilization` timepoints, never from totals alone | Estimated |
| **C. Estimated capacity value** | CU avoided × the rate of the `dim_pricing_scenario` version in effect at each run's time | Scenario, never labelled savings |
| **D. Avoided future capacity cost** | From the capacity planner (OE-041), with its assumptions | Scenario |
| **E. Realized savings** | Lower billed cost in a Cost Management export, or a SKU change or removal, at capacity scope, with a person's confirmation of the cause | Measured only |
| **F. Net financial benefit** | E minus attributable costs (sync and ingestion overhead, collector calls, organization-entered costs) | Measured only |
| **Recurring monthly benefit** | Average net CU avoided per recent run (last 30 days, current baseline version, runs with a known figure only) × runs per month | Estimated, read-time, never a fact |
| **Verified share** | Measured gross ÷ all known gross, per period | Shown beside A |

Allocating E across optimizations is off by default. When an organization turns it on, each
allocation is pro rata to measured CU avoided on that capacity in that period, the method is on
each row, and the allocations add up to exactly E.

## Joins

- `fact_savings_ledger.execution_id` to `fact_execution.execution_id` (a run on this PC), or to
  the imported Fabric run for a run in Fabric.
- `fact_savings_ledger.attribution` to `dim_optimization.optimization_id` when it is an
  optimization layer.
- `fact_attribution.ledger_event_id` to `fact_savings_ledger.record_id`.
- `fact_savings_ledger.pricing_version` to `dim_pricing_scenario.pricing_version`.
- `fact_savings_ledger.baseline_version` to `dim_baseline.baseline_version`. Events tied to a
  superseded baseline keep it; they are not re-valued.
- Every fact's `installation_id` to `dim_device`.

## Not in this layer yet

- Writing corrections and reversals: `fdopt_savings` has no `layer`, `cu_hours`, `classification`,
  `result_cu_hours`, `result_classification`, `layer_removed` or `reason` columns yet. Adding them
  is an additive schema change co-signed with the Lakehouse optimize owners, before ESI-404 events
  are written.

- Per-person and per-tenant reporting, the leaderboard and developer contribution: after D3 and D5.
- Organization-wide totals collected through a central UDF: after the mode C decision.
- Anything about a workload with no measured Fabric baseline: shown on the PC only (rule 6).
