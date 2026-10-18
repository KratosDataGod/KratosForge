# Capacity and cost evidence

Every capacity unit (CU) and cost figure in **Monitor > Cost** (Preview) says where it came from. A badge next to the number gives its classification, and clicking the number shows how it was worked out: the method, the source, the period, how many runs it covers and every assumption behind it.

## The four classifications

| Badge | Means | Example |
| --- | --- | --- |
| **Measured** | Read from a source that observed it. | A run on this PC started no Fabric job, so its Fabric job CU is 0. |
| **Estimated** | Worked out from stated assumptions, not read from Fabric's billing. | A notebook ran in Fabric for 30 minutes as a Python notebook at 1 CU: 0.5 CU hours. |
| **Scenario** | What something would have used if it had run differently. Nothing was charged or saved for certain. | A run on this PC took 40 minutes, so as a Python notebook in Fabric it would have used about 0.67 CU hours. |
| **Unknown** | There isn't enough reliable information. The figure is left out of totals and never counted as 0. | A Fabric job is still running, or a run log has no run time. |

A total is as weak as its weakest part: one estimated run makes the total estimated. When some runs' figures are unknown, the total covers the rest and shows an **Incomplete** badge, and the panel says how many runs are missing.

## What is counted

- **Fabric capacity used** is Fabric *job* CU: run time times the configured CUs of what ran (Python notebook 1 CU, Spark job 8 CU, set under **Prices**). Run time for jobs started from the app runs from when the app started the job to when Fabric reported its end, so it includes queueing and session start. Metadata runs in Fabric are counted per table from the run log, without the runner's session start.
- **Fabric use avoided on this PC** is a scenario: the time a run took on this PC, as a Python notebook in Fabric. It assumes the run would take as long in Fabric.
- **Share of a capacity** is estimated CU divided by the capacity's CUs over the period. It needs the capacity's SKU (read only if you already granted capacity access) and counts only runs started from this app, not everything on the capacity.
- **Money** is CU hours times your price per CU hour. A price is not a charge: on a reserved or already-running capacity the invoice doesn't change with each run, so running on this PC frees capacity rather than cutting the bill.

Not counted: OneLake reads and writes (they use capacity too, including those made from this PC), Fabric session start for metadata runs, and anything not started from Fabric Desktop by KratosBI. The CU your capacity actually recorded is in Fabric's Capacity Metrics app.

## Units

CU-seconds and CU-hours are the same thing at different scales (3,600 CU-seconds is 1 CU hour). Capacity utilization is a percent of a capacity over a period and needs the capacity size to work out. Money needs a price. The app never converts between these without saying which assumption it used.

## For developers

The schema, validation and unit rules are in `sidecar/fabric_desktop/measurement.py`. `costs.report` returns the plain numbers it always did (`cu_hours`, `cost`, `saved_cu_hours`, `saved`) plus an `evidence` dict on every total with the classified measurements, `unknown_runs`, and `capacity_share_evidence` per environment. A measurement looks like this:

```json
{"id": "m-…", "classification": "estimated", "metric": "fabric_job_cu", "unit": "cu_seconds", "value": 1800.0,
 "currency": null, "workload_id": "fabric:<workspace>:<item>", "run_id": "<job id>", "workspace_id": "<workspace>",
 "capacity_id": null, "source": "Fabric job API", "method": "run time x configured CUs",
 "collected_at": null, "period": {"start": "2026-10-14T13:00:00+00:00", "end": null},
 "assumptions": ["…"], "uncertainty": null, "reconciliation": "not_applicable", "reason": ""}
```

`measurement.validate` refuses a measured value without a source and collection time or with assumptions, an estimate or scenario without a method and assumptions, an unknown value that isn't `None`, negative or non-finite values, money without a currency and utilization over 100%. `measurement.total` never adds scenario figures to measured or estimated ones. Imported Capacity Metrics exports ([Measured usage](usage-import.md)) use the same shape; see the [program plan](plan.md).
