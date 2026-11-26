# Mutation testing, batch 35

Three modules, none touched by an open pull request, each with tests that run in under 20 seconds:
- The CPU cap for side by side's "Match Fabric": which processors the affinity is set to off Windows and on Windows, and how kernel32 is loaded (`cpu_cap.py`).
- Databricks SQL's IP functions on this PC: which BINARY lengths are read, how an IPv4 block is compared with IPv6, the error each kind of input names, and how the functions are registered with DuckDB (`databricks_ip.py`).
- Matching imported Capacity Metrics and cost rows to this app's Fabric runs: the coverage window and its tolerance, which runs and rows count, how a match reads, and the bill per capacity (`usage/reconcile.py`).

Batch 33's modules (costs, git scopes and notebook magics) were left to batch 33, which is still running.

One follow-up from batch 34's review is also here. `test_spark_sql_params_mutation.py` now checks that `SELECT : lim` has no marker, since Spark needs `:name` written together. That kills the `nxt.start == tok.end + 1` → `>=` mutant at `spark_sql_params.py` line 77, which batch 34's sample of 150 hadn't picked.

The run was at `main` 585b41c87 (2026-11-15), with `sidecar/tools/mutate.py` and the same method as batches 1 to 34 (sample cap 150, seed 7), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Every test that was kept passed both ways (474 passed), so no run below can score a false 100%. Three tests in `test_databricks_coverage.py` read `docs/`, so they were left out of the `databricks_ip.py` run: `test_what_the_docs_say_runs_still_runs`, `test_the_docs_page_is_up_to_date` and `test_the_docs_link_the_list`.

The new tests write no files and use no network or timing. The CPU cap tests replace every affinity call and kernel32 with stand-ins, so they never change the test process's own cores.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `cpu_cap.py` | test_cpu_cap, test_side_by_side_cores (+ test_cpu_cap_mutation) | 52 of 52 | 48 / 4 | **92.3%** | 52 / 0 | **100%** | none |
| `databricks_ip.py` | test_databricks_ip_bytes_edges, test_databricks_builtins_sql_mutation, test_databricks_coverage (+ test_databricks_ip_mutation) | 104 of 104 | 97 / 7 | **93.3%** | 104 / 0 | **100%** | none |
| `usage/reconcile.py` | test_usage_import (+ test_usage_reconcile_mutation) | 138 of 138 | 104 / 34 | **75.4%** | 135 / 3 | **97.8%** | 3 equivalent |

One `cpu_cap.py` mutant (`bit += 1` → `bit += 0`) loops forever and is counted as killed by its timeout, as in earlier batches.

No real bugs were found.

### What the survivors were, and the tests that now kill them

- **cpu_cap.py**:
  - Off Windows, the processors this process may use now read for pid 0, and the first of them picked in order.
  - On Windows, processor 0 picked first when it is allowed.
  - kernel32 loaded with `use_last_error=True`, both when the affinity is read and when it is set, so the error a refusal raises is the real one.
  - → `test_cpu_cap_mutation.py`.
- **databricks_ip.py**:
  - A BINARY value of a length other than 4, 5, 16 or 17 refused, even when its bytes spell an address as text.
  - A 17-byte BINARY value read as an IPv6 block.
  - An IPv4 block compared with IPv6 as its mapped block, exactly 96 bits longer.
  - The error for an input that may be an address or a block naming both, and the one for a block naming only a block.
  - Both functions registered with `null_handling="special"` and `side_effects=False`.
  - → `test_databricks_ip_mutation.py`.
- **usage/reconcile.py**:
  - A run up to 300 seconds before an import's first row or after its last row's end counted as covered, and one second more not.
  - An import's span ending at its last row's end, not its start.
  - A capacity bill, which has no workspace, covering no run.
  - A run counted only with a start inside the period and a job id, matched only by rows that start before it finished, and measured up to now while it is still going.
  - `by_run` listing the newest first and at most 200.
  - A timed row outside the period not counted, and an item total wholly outside the period not counted as partly inside it.
  - One matched row described in the singular, and the run's workspace id carried, or none.
  - Every count starting at zero and counting one per run.
  - A run without an estimate saying why by its kind.
  - `by_run` naming the workspace the run recorded or its id maps to.
  - The measured-to-estimate ratio kept to three places.
  - An item name the run recorded winning, and table maintenance without its table at the end of its name having none.
  - Capacities listed by name even with one row, the busiest eight meters named, and of two exports reaching as far, the one imported last winning.
  - → `test_usage_reconcile_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `usage/reconcile.py` | 49 | `name[: -len(table) - 1]` → `name[: -len(table) - 0]` | Equivalent: the space that's left is stripped. |
| `usage/reconcile.py` | 85 | `end or start` → `end and start` | Equivalent: `_end` is set on each row but never read. |
| `usage/reconcile.py` | 87 | `imported_at or ""` → `imported_at and ""` | Equivalent: `_imported_at` is read only to rank capacity rows, and those always have a start, so they take the branch above. |

### Not in this batch

Batch 33's modules.

