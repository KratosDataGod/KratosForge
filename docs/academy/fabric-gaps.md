# Academy: risks and gaps that need Fabric

## What can't be practised on this PC

The coverage gap records G-01 to G-11 are listed in [DP-700 coverage](dp-700-coverage.md#coverage-gap-records).
In short: KQL, Eventstream and Eventhouse; T-SQL execution on Warehouse; workspace, domain and tenant
settings; sensitivity labels; mirroring; Apache Airflow jobs; Spark engine tuning; the monitoring
hub, alerts and semantic model refresh; item behavior only the service shows, such as Git sync,
deployment, triggers and V-Order; and Power Query (M). Those 36 objectives stay incomplete in readiness
until a FABRIC VERIFIED exercise passes in the learner's own workspace.

## Fabric runs Academy will need

| Area | Fabric items | Capacity use | Notes |
| --- | --- | --- | --- |
| Lakehouse and Spark | Lakehouse, notebook, environment | Spark sessions (CU) | Starter pool sessions; teardown deletes the tagged lakehouse |
| Warehouse T-SQL | Warehouse | Warehouse queries (CU) | Database project deploy, RLS, DDM, error drills |
| Real-Time Intelligence | Eventstream, Eventhouse, KQL database, Activator | Continuous while running | Must stop and delete on teardown; highest cost risk |
| Pipelines and dataflows | Data pipeline, Dataflow Gen2 | Per run | Copy from a public sample source |
| Lifecycle | Git connection, deployment pipeline | Small | Needs a Git repo the learner owns and two or three workspaces |
| Governance | Sensitivity labels, endorsement | None | Labels exist only if the tenant publishes them |
| Mirroring | Mirrored database | Replication | Needs a source database: a cost decision |

**Cost.** No cost or CU figures will be shown until there is a credible method. The plan is to
measure each Fabric exercise once on the Trial capacity test workspace (`kf_tenant_test`) and show
the measured CU with its date, labelled as a measurement on one capacity, not a promise. Those
measurement runs touch Fabric and need Chris's typed "yes, run it" each time.

## Safety rules for Fabric exercises (ACD-014)

- The learner's own identity; never tenant admin for normal training.
- Preflight: role in the workspace, capacity attached and running, required features on.
- Every created item carries an Academy tag in its description (exercise id and run id) and is
  recorded locally; teardown deletes only items that are both tagged and recorded.
- Never delete or change items Academy didn't create. Pre-existing items are listed as the user's.
- A capacity or workspace that looks like production needs its name typed to continue.
- Continuous items (eventstreams, eventhouses) are stopped and deleted at teardown, with a reminder
  if the app closes first.

## Risks

| Risk | Impact | Mitigation |
| --- | --- | --- |
| Local results read as Fabric results | Learners overrate their Fabric skill | Classes on every result; `does_not_verify` sentences; Fabric-required objectives stay incomplete |
| DuckDB differs from Spark or T-SQL in edge cases (for example decimal division rounding) | Wrong lesson | Parity evidence links only; exercises avoid behaviors where the engines differ, or teach the difference |
| Study guide changes | Stale curriculum | ACD-020 weekly check, crosswalks, versioned results |
| The 19 October 2026 effective date is unconfirmed | Wrong version label | Registry version uses the page's own date until Microsoft publishes one; marked UNVERIFIED |
| Learner code escapes the lab | Host harm | ACD-005 limits plus Security read; OS sandbox decision |
| Questions too close to real exam items | Exam policy breach | Original questions only, each citing Microsoft Learn docs, reviewed by Second quality |
| Readiness read as a pass prediction | Misleading | Labelled estimate, help text, no percentages next to "pass" |
| Real-Time Intelligence costs | Capacity spend | Teardown, reminders, measurement first, Chris's go |
| Scope: 64 exercises plus 196 questions | Slow delivery | Separate content and exam threads, batches of 6–10, two PRs at review per workstream |
