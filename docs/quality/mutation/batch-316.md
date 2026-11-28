# Mutation testing, batch 316: workloads/inventory.py (every mutant)

One module, run uncapped: every mutant, not a sample (`--cap 100000 --seed 7 --kinds all`). It has no `<` or `>` comparison, so it has no boundary mutants.
- Turning each native source into workload records: runs from the run log, Fabric and schedules on this PC, owners, and the metadata entity, notebook, local pipeline and Fabric item workloads with their deployments, schedules and links (`workloads/inventory.py`).

Batch 140 sampled 150 of its then 260 mutants (56.7% before, 100% after). It still has 260, and this run ran all 260 of 260.

The run was at `main` ea988e99e (2026-11-20), with the method of batch 315. After the runs, no folder in the `--basetemp` tree was nested 40 levels deep, and the kills don't end in a streak of near-identical times. Three kills picked at random were checked by hand, and each failed real tests (10, 24 and 2). The "after" run re-checked only the 15 survivors, with `--only`. Before it, the new tests passed in a copy of `sidecar/` on their own and against the module as `ast.unparse` writes it. No mutant timed out.

The set is the files that call the module's functions: test_workloads and test_workload_service_mutation reach it through the workload service. No `-k` filter or `--deselect` was needed.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/inventory.py` | test_workload_inventory_mutation, test_workload_service_mutation, test_workloads (+ test_workload_inventory_uncapped) | 260 of 260 (260 + 0), uncapped | 245 / 15 | **94.2%** | 245/260 → 259/260 | none | 259 / 1 | **99.6%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **workloads/inventory.py**:
  - A metadata run keeps its job id, and a Fabric job keeps its id and names its workspace.
  - An entity keeps its description. Dev is never behind the project's version.
  - Only a notebook run of this notebook, with an item id, links it. A notebook is linked by name only when its publish workspace's items were read, and the link keeps the workspace's name.
  - A notebook names its lakehouse, offers to run its schedule here, and a notebook or local pipeline names the signed-in user it runs as. A local pipeline is Preview.
  - A Fabric schedule's last run is not added again when a run this app started has the same start.
  - → `test_workload_inventory_uncapped.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `workloads/inventory.py` | 359 | `"scheduled_only": True` to `False` for a pipeline only a schedule runs | Equivalent: nothing reads the key; the workload record is built from the entry's other fields. |

