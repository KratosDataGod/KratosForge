# Mutation testing, batch 59

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- How much memory is free for a local query, a container's limit included, and the budget a query gets from it (`free_memory.py`).
- Reading a row of a Capacity Metrics app export: the reasons a row is skipped, the period it covers and how it is keyed (`usage/metrics_app.py`).
- The workloads in a workspace for the optimization engine: the summary of an item's job history and reading a notebook's cells from its definition (`opportunities/inventory.py`).

The run was at `main` 732f1b5ad (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No test in these sets reads `docs/` or `examples/` (`test_opportunities_assess_mutation.py` does, so it was not used), and no `-k` filter was used.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `free_memory.py` | test_free_memory, test_free_memory_edges, test_meta_preflight_mutation (+ test_free_memory_mutation) | 53 of 53 (51 + 2) | 47 / 6 | **88.7%** | 47/51 → 51/51 | 0/2 → 2/2 | 53 / 0 | **100%** | none |
| `usage/metrics_app.py` | test_usage_import (+ test_usage_metrics_app_mutation) | 53 of 53 (51 + 2) | 34 / 19 | **64.2%** | 34/51 → 51/51 | 0/2 → 2/2 | 53 / 0 | **100%** | none |
| `opportunities/inventory.py` | test_opportunities, test_opportunities_analyzers_mutation (+ test_opportunities_inventory_mutation) | 84 of 84 (81 + 3) | 68 / 16 | **81.0%** | 67/81 → 80/81 | 1/3 → 3/3 | 83 / 1 | **98.8%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **free_memory.py**:
  - A control-group path may hold a colon.
  - On cgroup v1, a limit of exactly 2^60 means no limit, and one less is a limit.
  - When three quarters of the free memory equals the share asked for, the share sets the cap; a share between three quarters of free and all of it is cut to three quarters.
  - → `test_free_memory_mutation.py`.
- **usage/metrics_app.py**:
  - Each reason a row is skipped, read one row at a time: no workspace or item, no start time, a start or end time that can't be read, an end before the start, a negative CU value.
  - A workspace or an item alone is enough, zero and fractions of a CU second are kept, and an operation may end as it starts.
  - An operation covers its start to its end, or just its start; an item total needs both ends of its period and covers it.
  - An operation is keyed by its id, or else by what it ran; an item total by its period; the hash part of a key is 24 characters.
  - Optional fields are kept when given and empty when not.
  - → `test_usage_metrics_app_mutation.py`.
- **opportunities/inventory.py**:
  - Runs exactly at either edge of the window count, and one outside does not; a start that isn't text is skipped.
  - A time's offset is kept, and a time without one is read as UTC.
  - Only finished runs with an end not before their start are timed, a run of 0 seconds included; only failed runs count as failed.
  - Seconds are rounded to three places; no runs is 0 seconds, and runs none of which finished is unknown.
  - The last run is the latest one.
  - A payload that isn't base64 is read as it is, a cell's source given as lines is joined, and a definition without notebook content is refused.
  - → `test_opportunities_inventory_mutation.py`.

### Left

| Module | Line | Mutant | Why it stays |
| --- | --- | --- | --- |
| `opportunities/inventory.py` | 110 | `zip(..., strict=False)` to `strict=True` | Equivalent: splitting on n cell markers gives n + 1 chunks, so the markers and `chunks[1:]` always have the same length. |

