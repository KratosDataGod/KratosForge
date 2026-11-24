# Mutation testing, batch 124

Two modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants together with the earlier kinds:
- Ranks earned from Learn evidence: the ladder's requirements, what the evidence counts, the top rank and when a rank can be earned today (`ranks.py`).
- Workspace monitoring views for a warehouse: the row cap, Kusto's reply and errors, and the KQL queryset the app keeps in Fabric (`warehouse_monitoring.py`).

`integrations/permissions.py` was planned for this batch but left out, because it is on the approval and policy skip list; `warehouse_monitoring.py` took its place.

The run was at `main` 5865db954 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, each was checked to exist, and the tests first passed once on the unmutated copy. Each module has fewer than 150 mutants, so all of them ran. No `-k` filter or `--deselect` was needed: none of these tests reads `docs/`, `examples/` or `app/`.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. The new tests pass with DuckDB 1.5.6 and 2.0.0. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `ranks.py` | test_ranks, test_profile_goals_impact (+ test_ranks_mutation) | 107 of 107 (102 + 5) | 80 / 27 | **74.8%** | 78/102 → 102/102 | 2/5 → 5/5 | 107 / 0 | **100%** | none |
| `warehouse_monitoring.py` | test_warehouse_monitoring, test_warehouse_monitoring_build_again, test_warehouse_monitoring_service_mutation (+ test_warehouse_monitoring_mutation) | 113 of 113 (111 + 2) | 97 / 16 | **85.8%** | 96/111 → 111/111 | 1/2 → 2/2 | 113 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **ranks.py**:
  - The ladder's numbers: 3 paths for Master Forger, 25 exercises and 4 labs for Pack Runner, 3 fixes, 5 paths, 8 labs and 10 fixes for Pack Leader, 15 notebooks and 90 days. The ladder version is 1.
  - The top rank has no next one, and the rank below it names the top rank as next.
  - Exercises, fixes and notebooks are each counted once, and only from their own kind of evidence.
  - The days count spans the first and last dated evidence; a time it can't read is left out, and no dated evidence is 0 days.
  - "Finish 1 learning path" and "Finish 2 learning paths". A single lesson is named on its own. An unknown requirement is an error.
  - Labs and exercises can be earned once exactly enough have shipped, and only labs count as shipped labs.
  - → `test_ranks_mutation.py`.
- **warehouse_monitoring.py**:
  - At most 500 rows, Kusto is asked for 501, and exactly 500 rows are not truncated. Each try waits up to 90 s.
  - An empty reply is no columns, no rows and not truncated.
  - A column's type is its `DataType` or else its `ColumnType`, and a table without columns is read.
  - A 403 adds Kusto's message only when there is one.
  - A long warehouse name is cut to 200 characters in the queryset's name.
  - A queryset needs a database. Its definition is JSON indented by two.
  - Reading a queryset keeps its version and other keys, uses 1.0.0 when there is no version, and reads a definition without the queryset part as an empty one.
  - → `test_warehouse_monitoring_mutation.py`.

### Left

None.

