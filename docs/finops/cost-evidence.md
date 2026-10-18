# Capacity and cost evidence

Every capacity unit (CU) and cost figure in **Optimize > Cost and savings** (Preview) says where it came from. A badge next to the number gives its classification, and clicking the number shows how it was worked out: the method, the source, the period, how many runs it covers and every assumption behind it.

## The four classifications

| Badge | Means | Example |
| --- | --- | --- |
| **Measured** | Read from a source that observed it. | A run on this PC started no Fabric job, so its Fabric job CU is 0. |
| **Estimated** | Worked out from stated assumptions, not read from Fabric's billing. | A notebook ran in Fabric for 30 minutes as a Python notebook at 1 CU: 0.5 CU hours. |
| **Scenario** | What something would have used if it had run differently. Nothing was charged or saved for certain. | A run on this PC took 40 minutes, so as a Python notebook in Fabric it would have used about 0.67 CU hours (the rough figure kept for older callers; see below). |
| **Unknown** | There isn't enough reliable information. The figure is left out of totals and never counted as 0. | A Fabric job is still running, or a run log has no run time. |

A total is as weak as its weakest part: one estimated run makes the total estimated. When some runs' figures are unknown, the total covers the rest and shows an **Incomplete** badge, and the panel says how many runs are missing.

## What is counted

- **Fabric capacity used** is Fabric *job* CU: run time times the configured CUs of what ran (Python notebook 1 CU, Spark job 8 CU, set under **Prices**). Run time for jobs started from the app runs from when the app started the job to when Fabric reported its end, so it includes queueing and session start. Metadata runs in Fabric are counted per table from the run log, without the runner's session start.
- **Fabric use avoided on this PC** is estimated from Fabric runs of the same table, never from how long the run took on this PC. See [How savings on this PC are estimated](#how-savings-on-this-pc-are-estimated).
- **Share of a capacity** is estimated CU divided by the capacity's CUs over the period. It needs the capacity's SKU (read only if you already granted capacity access) and counts only runs started from this app, not everything on the capacity.
- **Money** is CU hours times your price per CU hour. A price is not a charge: on a reserved or already-running capacity the invoice doesn't change with each run, so running on this PC frees capacity rather than cutting the bill.

Not counted: OneLake reads and writes (they use capacity too, including those made from this PC), Fabric session start for metadata runs, and anything not started from Fabric Desktop by KratosBI. The CU your capacity actually recorded is in Fabric's Capacity Metrics app.

## How savings on this PC are estimated

A run on this PC starts no Fabric job. What that job would have used in Fabric is worked out only from Fabric runs of the same workload: for a metadata table, its own runs in Fabric (FINOPS-005).

- **Which Fabric runs.** Runs of the same table in the last 90 days, with the same definition (a changed table definition makes older runs not count). Runs in the same environment are used first; when there are none, runs in another environment are used and the figure says so.
- **Measured first.** When an imported Capacity Metrics export matched a Fabric run ([Measured usage](usage-import.md)), its measured CU is used. Otherwise its estimated CU (run time times the configured CUs) is used, and the figure says it rests on an estimate.
- **The figure.** The median of the five most recent of those runs. When both sides recorded the rows they read, it is scaled by rows read; otherwise it assumes this run read about as much.
- **Never from time on this PC.** How long a run took here says nothing about what it uses in Fabric, so a run whose time wasn't recorded can still be estimated.
- **Otherwise Not known.** A run with no such Fabric run (for example a table that only ever ran on this PC, or a notebook write) is **Unknown**: it is left out of the total, never counted as 0, and the panel says how many runs were left out. Running the table once in Fabric, or importing a Capacity Metrics export, gives later runs here something to compare with.

The result is always **Estimated**: nothing was charged or saved for certain, and OneLake reads and writes from this PC still use capacity and aren't counted. The older rough figure (time on this PC as a Python notebook) is still in the report as `scenario_cu` and `scenario_cost`, and in the plain `saved_cu_hours` and `saved` numbers, as a **Scenario**; it is never added to the estimate.

## Units

CU-seconds and CU-hours are the same thing at different scales (3,600 CU-seconds is 1 CU hour). Capacity utilization is a percent of a capacity over a period and needs the capacity size to work out. Money needs a price. The app never converts between these without saying which assumption it used.

## For developers

The schema, validation and unit rules are in `sidecar/fabric_desktop/measurement.py`. `costs.report` returns the plain numbers it always did (`cu_hours`, `cost`, `saved_cu_hours`, `saved`) plus an `evidence` dict on every total with the classified measurements (`avoided_cu` and `avoided_cost` from `calibration.py`, the rough `scenario_cu` and `scenario_cost` apart), `unknown_runs`, `uncalibrated_runs` (runs on this PC with nothing to compare with), and `capacity_share_evidence` per environment. A measurement looks like this:

```json
{"id": "m-…", "classification": "estimated", "metric": "fabric_job_cu", "unit": "cu_seconds", "value": 1800.0,
 "currency": null, "workload_id": "fabric:<workspace>:<item>", "run_id": "<job id>", "workspace_id": "<workspace>",
 "capacity_id": null, "source": "Fabric job API", "method": "run time x configured CUs",
 "collected_at": null, "period": {"start": "2026-10-14T13:00:00+00:00", "end": null},
 "assumptions": ["…"], "uncertainty": null, "reconciliation": "not_applicable", "reason": ""}
```

`measurement.validate` refuses a measured value without a source and collection time or with assumptions, an estimate or scenario without a method and assumptions, an unknown value that isn't `None`, negative or non-finite values, money without a currency and utilization over 100%. `measurement.total` never adds scenario figures to measured or estimated ones. Proof scorecards and run receipts carry the same shape: each side of `cost` has a `classification` and a `measurement` of its Fabric job CU (see [how proofs work](../prove/how-proofs-work.md)). Imported Capacity Metrics app and Azure Cost Management exports ([Measured usage](usage-import.md)) use the same shape; see the [program plan](plan.md).
