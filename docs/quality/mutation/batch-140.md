# Mutation testing, batch 140

One workload module not covered before, run with `--kinds all`:
- Turning each native source into workload records: runs from the run log, Fabric and schedules on this PC, owners, and the metadata entity, notebook, local pipeline and Fabric item workloads with their deployments, schedules and links (`workloads/inventory.py`).

The run was at `main` 4ddd3662 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. The module has 260 mutants, so 150 were sampled; the sample held no boundary mutant. No `-k` filter was used. One test was left out with `--deselect` to keep each mutant's run short: `test_workloads.py::test_ten_thousand_workloads_filter_and_page_quickly`, which builds 10,000 Fabric item records with this module and then times filtering and paging them (`workloads/query.py`); it checks the speed of the paging, not the records, which the other tests check, and takes about 1 s a run. Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it; they passed both ways. No mutant's run timed out.

The first run used test_workloads only, and scored 61 / 89 (40.7%). Each survivor was run again with test_workload_pipelines, test_workload_service_paths, test_workload_audit and test_workload_query added, which reach this module through the workload service, and the "before" figures are from that run.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/inventory.py` | test_workloads, test_workload_pipelines, test_workload_service_paths, test_workload_audit, test_workload_query (+ test_workload_inventory_mutation) | 150 of 260 (150 + 0) | 85 / 65 | **56.7%** | 85/150 → 150/150 | none sampled | 150 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **workloads/inventory.py**:
  - A run by an agent names the agent, and its host when it has one. A run ends its seconds after it started (0 seconds too), and one without seconds or a start has no end.
  - A Fabric run started from this app, a Fabric job and a schedule run on this PC each get empty or "workspace" defaults for what they leave out, keep what they have, and a job takes either spelling of its times. Runs are newest first, one with no start last.
  - A project with no name is "project", and one with no team or contacts has no team or business contact; one that has them names its team and first contact.
  - Where tables go in each environment, which workspace and whose identity an environment that runs in Fabric shows, and that an environment not set up offers no run.
  - The newest deployment says which version an environment runs.
  - An environment's schedules on this PC are the ones for this entity or for all entities, on unless they say otherwise; Fabric schedules are off unless they say so.
  - The products an entity feeds use the product's name when it has no title. An entity's errors are its own, without its name. Its upstream tables are the ones it reads.
  - A kept link or a link from a run gets defaults for a missing workspace name and way of linking.
  - A linked notebook's Fabric runs are cached until signed in, it has only its own schedules, names its lakehouse's workspace, can be cancelled while a run is going, and doesn't list a schedule's last run twice. One linked by name shows how the Fabric items were read. Its last run here is its newest schedule run.
  - A pipeline run the app stopped says so, a message from the schedule wins, and its mocks are counted. A pipeline only a schedule runs is listed by its folder name; the first pipeline at a path is kept, and its place and problems are shown.
  - Runs count for a pipeline through its schedule, or by its name only when the name is unique; a run whose record is gone shows the schedule's own row.
  - A Fabric item gets defaults for a missing name, workspace name and description, its schedules are off unless they say so, it shows how it was read once it has runs, can be cancelled while one is going, and a Preview type can't be scheduled.
  - → `test_workload_inventory_mutation.py`.

### Left

None.

