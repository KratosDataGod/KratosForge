# Mutation testing, batch 163

A boundary re-score of five modules first scored before batch 36, when `mutate.py` made no boundary mutants (`<` to `<=`, `>` to `>=`; see batch 36). Only the boundary mutants were run (`--kinds boundary`), so the scores below count those alone; each module's point score from its first batch stands as it was. The modules, with the batch that first scored each:
- Whether a notebook can run on this PC and in Fabric, and the schedules it publishes (`fabric_run.py`, batch 17).
- Warehouse projects in a Git repository: listing, reading, saving, importing and comparing their files (`warehouse_project_service.py`, batch 21).
- Spark's decimal result types on this PC (`spark_decimals.py`, batch 28).
- The notebook kernel's own process: its memory and time limits and how it is stopped (`kernel_proc.py`, batch 21).
- The pipeline agent: schedules, wakes from sleep and how many jobs it claims (`agent.py`, batch 17).

The run was at `main` 4ddd3662 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902 (sample cap 150, seed 7, `--kinds boundary`). Each module has fewer than 150 boundary mutants, so all of them ran. The tests given were every test file that uses the module, as paths relative to `sidecar/`, except for `kernel_proc.py`, whose full set takes about three minutes a run, so only its own three test files were given. They first passed once on the unmutated copy of `sidecar/`. Tests that read `docs/`, `examples/`, `app/` or `.github/` fail in that copy, so they were left out: `test_notebook.py::test_notebook_reads_local_files_and_writes_tables` for `fabric_run.py`, `test_spark_decimal_rules.py::test_what_still_differs_is_listed_in_the_compatibility_docs` for `spark_decimals.py`, and for `agent.py` the files `test_mypy_ratchet.py` and `test_redact.py` and eight tests in `test_mailbox.py` and `test_agent_queue_bound.py`.

Before each "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. No mutant's run timed out.

## Results

| Module | Test files run | Boundary mutants | Before: killed / survived | Before | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `fabric_run.py` | the 20 test files that use it (+ test_fabric_run_boundary) | 14 of 14 | 4 / 10 | **28.6%** | 14 / 0 | **100%** | none |
| `warehouse_project_service.py` | test_warehouse_project, test_warehouse_project_edges, test_warehouse_project_more_edges, test_warehouse_project_mutation (+ test_warehouse_project_boundary) | 14 of 14 | 7 / 7 | **50.0%** | 14 / 0 | **100%** | none |
| `spark_decimals.py` | the 5 test files that use it (+ test_spark_decimals_boundary) | 14 of 14 | 3 / 11 | **21.4%** | 10 / 4 | **71.4%** | 4 equivalent |
| `kernel_proc.py` | test_kernel_proc_mutation, test_kernel_process, test_kernel_process_edges (+ test_kernel_proc_boundary) | 11 of 11 | 5 / 6 | **45.5%** | 11 / 0 | **100%** | none |
| `agent.py` | the 25 test files that use it (+ test_agent_boundary) | 10 of 10 | 6 / 4 | **60.0%** | 9 / 1 | **90.0%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **fabric_run.py**:
  - A SQL text built from five additions is still read whole, and one built from six is known only at run time.
  - A `#` comment that starts exactly at a match counts as a comment.
  - A quoted line of exactly 60 characters is kept whole.
  - The first and last lines of a cell are quoted, and a line number of 0 quotes the cell, not its last line.
  - Notebooks run exactly `MAX_RUN_DEPTH` deep stop the check, and one fewer is followed.
  - A Built-in file of exactly the size limit is read.
  - Every 1 minute and the longest interval are schedules; 0 and one more are refused.
  - `.option()` given only its name doesn't stop the check.
  - → `test_fabric_run_boundary.py`.
- **warehouse_project_service.py**:
  - A file, a save, an imported script and an existing file of exactly the size limit are all taken.
  - The compare takes exactly the object limit.
  - Files are listed down to exactly the folder limit and no deeper.
  - → `test_warehouse_project_boundary.py`.
- **spark_decimals.py**:
  - A product, a remainder and a `div` whose operands need exactly 38 digits keep Spark's type, and the `div` stays exact.
  - Division shifts from 0 up to exactly 38 digits are written, and a scale of 38 is refused.
  - Ceil and floor with as many places as the scale are unchanged, and exactly 38 digits still fit either way.
  - → `test_spark_decimals_boundary.py`.
- **kernel_proc.py**:
  - A failed read of the Job Object list whose count would have fitted isn't read again.
  - Memory of exactly the limit is allowed.
  - A cell at exactly its time limit isn't interrupted yet, and one at exactly the grace after its interrupt isn't ended yet.
  - Stopping, and a cell that ends its process at once, wait only until their deadline.
  - → `test_kernel_proc_boundary.py`.
- **agent.py**:
  - A schedule exactly its longest wait away is kept, and one a second further is worked out again.
  - The clock moving exactly a status beat and a poll isn't a wake.
  - The agent claims exactly as many jobs as it has free workers.
  - → `test_agent_boundary.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `spark_decimals.py` | 95 | `precision <= MAX` to `<` in `_adjust` | Equivalent: at exactly 38 digits the other branch gives the same precision and scale, since 38 minus the whole digits is the scale. |
| `spark_decimals.py` | 382 | `i < stars[0]` to `<=` | Equivalent: a column that changes is never the star itself, so its position is never the star's. |
| `spark_decimals.py` | 384 | `i > stars[-1]` to `>=` | Equivalent, for the same reason. |
| `spark_decimals.py` | 532 | `places >= 0` to `>` in `_ceil_floor` | Equivalent: at 0 places both branches multiply and divide by 1. |
| `agent.py` | 187 | `at <= now` to `<` for a saved incident time | Equivalent: when the two are equal, either one is the same value. |

