# Risk check before a run

Before a workload starts, Fabric Desktop by KratosBI checks what could stop the run or make it costly, and shows the evidence. The check only reads. Nothing is started, published, paused or changed to work it out.

It runs for actions that start work on a notebook, a metadata entity or a Fabric item: **Run**, **Run in Fabric**, **Run now** and running a schedule now. Cancel isn't checked.

This first version is the engine, and it **reports only: it doesn't block runs**. It is returned with each run dialog's details (`workload_preflight`) and through the `risk_check` call, with `"enforced": false`. Starting a run (`workload_act`) doesn't check it, so a run started another way isn't stopped by a blocked result. The run dialogs will show it in the next release (FINOPS-004, part 2).

## Results

| Result | Means |
| --- | --- |
| **Pass** | Nothing found that should stop or slow the run. |
| **Warning** | Something to look at first, such as a recent failure, a run already going, or a CU threshold you set. You can go ahead. |
| **Blocked** | An actual restriction: the run would be refused or fail. Examples are your organization's policy, not being signed in, a paused capacity, a problem in the definition, or the memory guard on this PC. A cost estimate never blocks a run. |
| **Unknown** | The capacity's state couldn't be read and nothing else was found. The run may still work. |

Each finding has a title, what it means, the evidence, where the evidence came from and what to do. Findings are listed worst first.

## What is checked

| Check | Source | Outcome |
| --- | --- | --- |
| Organization policy | `allow_fabric_runs` ([policies](../permissions.md)) | Blocked when IT turned Fabric runs off. Runs on this PC aren't affected. |
| Sign-in | This PC's sign-in, without a prompt | Blocked when the action uses Fabric and you're signed out. |
| Capacity | The [capacity reading](capacity.md): GET `/workspaces/{id}` and GET `/capacities` with your sign-in | Paused on a reading from the last hour: **blocked**. Paused on an older reading: **warning**, with its age. Running: **pass**, but how busy it is isn't known, because Fabric has no utilization API for developers. Not visible to you, or Capacity.Read.All not granted: **unknown**, with what to ask for. No workspace, no capacity, or no access to the workspace: **blocked**. |
| Definition | The project's definitions and deployment records | Problems in the definition: **blocked**. An environment running an older version: **warning**. |
| Notebook compatibility | The same check as **Runs on** | Cells that won't work where it runs: **warning**. For a Fabric run, whether this PC could run it instead. A notebook that needs Fabric is never suggested for this PC. |
| Input size | The source's Delta log (metadata entities) | For reference in Fabric. On this PC, input over the memory guard: **blocked**. |
| History | Runs of this workload recorded by this app | The last run failed, or too many of the last five failed: **warning**. A run still going: **warning**. Usual run time, for reference. |
| CU use | [Measured usage](usage-import.md) matched to this workload's runs, otherwise the app's estimate | For reference, labeled measured or estimated. Above the threshold you set: **warning**. With neither, it stays unknown, never 0. |
| Schedules | Schedules on this PC and in Fabric | A schedule on this PC due soon: **warning**. Fabric schedules' next run times aren't read. |

The check makes at most two reads in Fabric: the workspace and the capacity list. It uses an environment's capacity reading from the last hour when there is one. Each result lists the reads it made.

## Settings

`set_risk_settings` changes when the check warns. The settings are kept with this PC's settings.

| Setting | Default | Effect |
| --- | --- | --- |
| `warn_run_cu_hours` | off | Warn when the last run of this workload used more CU hours than this. Measured when an import matched the run, otherwise estimated, and labeled so. |
| `warn_failed_runs` | 2 | Warn when this many of the last five runs failed. 0 turns it off. |
| `schedule_window_minutes` | 60 | Warn when a schedule on this PC runs the workload within this many minutes. |

## Going ahead with warnings

`risk_acknowledge` records that you saw an assessment's warnings and chose to run anyway. It saves who, when, the warnings and an optional note in `risk_acknowledgements.jsonl` in the app's data folder, and the call is in the [audit trail](../user-guide.md#audit-trail).

The assessment is checked again first:
- A blocked result can't be acknowledged.
- If the findings changed since you looked, the acknowledgement is refused and you need to look again.

Checking is never audited, because it only reads.

## For AI tools

The `fabric_desktop_check_run_risk` capability (Preview) returns the same result to tools connected through [MCP or the local API](../ai/README.md). It reads Fabric with your own account.

## Limits

- The capacity's state says whether work can start, not whether it will be slow: utilization isn't available to developers.
- Matching CU to this workload uses the runs this app started. Runs started elsewhere count only through imported measured usage.
- No CU number is predicted for the next run. The last run's figure is shown for reference, labeled measured or estimated.
- Not yet validated against a live tenant. Tests use fixtures.
