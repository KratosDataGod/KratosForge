# Mutation testing, batch 196

Workloads as the desktop UI lists them (`workload_service.py`): reading the metadata project, notebooks, pipelines and Fabric items into one inventory, a workload's page and timeline, the preflight and the actions, settling them from run records, and the audit lines sent to the platform.

The run was at `main` bdf2c177 (2026-11-19), with the method of batch 142. The module has 449 mutants, so 150 were sampled. No `-k` filter or `--deselect` was needed. One mutant's run timed out (line 725, the audit send's lock taken with `blocking=True`, which waits for ever on the lock a test holds), and it counts as killed.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workload_service.py` | test_workload_service_paths, test_workloads, test_workload_pipelines, test_workload_audit, test_workload_query, test_workload_capacity_use (+ test_workload_service_mutation) | 150 of 449 (147 + 3) | 105 / 45 | **70.0%** | 105/147 → 147/147 | 0/3 → 3/3 | 150 / 0 | **100%** | none |

The 100% is for the 150 mutants sampled with seed 7, not for all 449 of the module's.

A second sample, with seed 11 and the same tests plus the first round's new ones: cap 60 killed 52 of 60 (86.7%) before and 60 of 60 after; cap 300 (which includes those 60) killed 268 of 300 (89.3%: 267/299 point, 1/1 boundary) before and 300 of 300 after. One of its runs timed out and counts as killed: line 314, where `_wl_record` refreshes by default. None of its survivors was equivalent.

No bugs were found.

### What the survivors were, and the tests that now kill them

- **workload_service.py**:
  - A cached run log is cached for exactly an hour, then stale; Fabric items for exactly a day. The existing tests used the names, so a changed number still passed.
  - A read's age is the seconds since it, or none when its time can't be read.
  - Dev without a target is read on this PC and isn't cached; another environment without one is read on OneLake. Up to 2000 runs are read.
  - OneLake needs both a storage and a Fabric token. A read under two minutes old is used again without either, and a refresh keeps it, marked as cached and why.
  - Freshness reads the newest run first, in every environment that was read.
  - Only the runner notebook's schedules in an environment's workspace are that environment's Fabric schedules.
  - With no items read, a scan starts and says it is reading them, or asks for a sign-in; a refresh scans again even with items read before.
  - Items are live only when this scan read them, under exactly two minutes ago.
  - A scan runs on a background thread, eight workspaces at a time, and returns when it ended. A workspace that can't be read keeps the reason, not the state.
  - A listing isn't a refresh unless asked. A page holds 100 workloads by default, and actions list 50 at a time.
  - A pipeline listing that fails says why in 300 characters. A Fabric item's history keeps its newest 50 runs.
  - The timeline says who promoted what (version cut to 8 characters) and who changed an owner, with none and you for empty values. A definition's time is the file's own, or the recorded one with no file. The newest 100 events are kept.
  - A product is matched by its id or its name and shown by its name, or its id with no name.
  - Only a running Fabric job with an id can be cancelled.
  - A run on this PC needs no sign-in and runs as its identity; a run in Fabric runs as the person who starts it.
  - A run in Test warns of the version there, cut to 8 characters, and runs in Test. Only an entity's run goes to the metadata run.
  - A manual run that started in the same second settles a schedule action.
  - Audit lines sent are counted, none go without a platform, and waiting lines are tried again after exactly ten minutes, on a background thread.
  - Seed 11: without pipelines there is no pipeline source, and a cut listing is partial and says how many are listed. A scan reads only the workspaces this app works in and is kept as live or partial. A listing reads 500 Fabric runs and 1000 on this PC.
  - Seed 11: a workload's page shows its newest 50 runs, actions and ownership changes; capacity use covers 30 days by default; a linked notebook returns its page. A Fabric item with no job history keeps its deployment.
  - Seed 11: the timeline names a run's state, environment and where it ran, for the first 30 runs.
  - Seed 11: the preflight refuses a definition with problems, naming the first two; only a failed last run warns; a cancel, and a run on this PC that writes to a workspace, need a sign-in. A schedule run now names the pipeline or the notebook, and the recorded line names the platform only when there is one.
  - Seed 11: freshness isn't checked where a configured environment couldn't be read. Up to 200 open actions settle at a time, with Fabric's message or its status. A metadata run's outcome is kept in 2000 characters.
  - Seed 11: an action's audit line is dated when it ended, else when it started; nothing waiting sends nothing; a scan asked for again is pending while it runs. Items that couldn't all be read name the first three workspaces. A consumer from the catalog is declared.
  - → `test_workload_service_mutation.py`.

### Left

None.

