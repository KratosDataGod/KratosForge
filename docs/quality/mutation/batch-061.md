# Mutation testing, batch 61

Four modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Job files and running a job on this PC: loading, the name and engine checks, and reading the result as Arrow (`jobs.py`).
- Measuring a run: wall time, CPU time and memory, on the engine or a notebook's own process (`measure.py`).
- This installation's id for the optimization tables: the id file, its label and a new id on reset (`optimize/installation.py`).
- Home's Ready to work checklist: the clock, free space, network drive and workspace rows, and the write test's time (`readiness.py`).

The run was at `main` 5d5028f98 (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. Three test files were left out:
- `test_local.py` reads `examples/`.
- `test_clock_skew.py` reads `app/`, outside `sidecar/`. A first `readiness.py` run that included it scored a false 94 of 94, because the file can't be collected in the copy. That run was thrown away and the module was run again without it.
- `test_kernel_process.py` and `test_sql_exec.py` reach `measure.py` but take 35 to 50 seconds each. They were left out for time, which can only lower the "before" score.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `jobs.py` | test_jobs_edges, test_append_callers, test_duckdb_try_cast, test_environment_isolation, test_publish_edges, test_publish_mutation (+ test_jobs_mutation) | 32 of 32 (32 + 0) | 22 / 10 | **68.8%** | 22/32 → 32/32 | none | 32 / 0 | **100%** | none |
| `measure.py` | test_measure_meter, test_optimize_local_runs, test_engine_usage_mutation, test_scorecard_mutation (+ test_measure_mutation) | 68 of 68 (67 + 1) | 54 / 14 | **79.4%** | 54/67 → 67/67 | 0/1 → 0/1 | 67 / 1 | **98.5%** | 1 equivalent |
| `optimize/installation.py` | test_optimize_service, test_optimize_service_edges, test_corrupt_state_reads, test_opportunities_share, test_optimize_shared (+ test_optimize_installation_mutation) | 19 of 19 (19 + 0) | 11 / 8 | **57.9%** | 11/19 → 19/19 | none | 19 / 0 | **100%** | none |
| `readiness.py` | test_readiness, test_offline_start, test_service_edges (+ test_readiness_mutation) | 94 of 94 (89 + 5) | 52 / 42 | **55.3%** | 52/89 → 88/89 | 0/5 → 4/5 | 92 / 2 | **97.9%** | 2 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **jobs.py**:
  - Loading a job file gives the job, with its folder and target.
  - A job name that is empty, only spaces, `.`, `..` or `_writes`, or that holds `/`, `\` or `:` (`../x`, `a/b`, `a\b`, `c:x`), is refused. `sales`, `a.b`, `...` and `_write` are accepted.
  - An unknown engine is refused.
  - A job and its target can't be changed once made.
  - The result is read with whichever Arrow method DuckDB has, `to_arrow_table` or `fetch_arrow_table`, after setting the time zone to UTC.
  - → `test_jobs_mutation.py`.
- **measure.py**:
  - On Windows, CPU time joins the high and low halves of each time, and the process handle isn't inherited.
  - The meter is its own context and samples on a daemon thread named `run-meter`, with no samples counted before the interval passes.
  - On leaving, the sampler is stopped and waited for up to 2 seconds before the last read.
  - Engine wall and CPU times are differences, rounded to milliseconds; a kernel's CPU time is too.
  - Memory is reported to a tenth of a MB.
  - → `test_measure_mutation.py`.
- **optimize/installation.py**:
  - The time stamp is UTC to the second.
  - The OS family is macOS, Windows or Linux, or "unknown" when Python can't tell.
  - The file is written with one-space indents.
  - A label keeps its first 80 characters, trimmed.
  - A file that can't be read counts as no file.
  - Reset gives the new id and keeps the label.
  - Only a lowercase UUID counts as an id.
  - → `test_optimize_installation_mutation.py`.
- **readiness.py**:
  - The limits are 5 minutes and 2 GiB, and a network drive is drive type 4.
  - A clock exactly 5 minutes off either way is ok, and a row without a host names "the server".
  - Further off, the row gives minutes up to 119, then hours, rounded, and says ahead or behind.
  - One workspace is singular, and none offers the workspaces page.
  - Exactly 2 GiB free is enough, one byte less warns, and the amount is given in GiB.
  - On Windows, a drive letter path, or a drive alone such as `Z:`, is asked about by its root. A path without a drive letter isn't asked about, a drive that can't be asked about counts as on this PC, and off Windows no drive is asked about.
  - The write test reports how long it took.
  - → `test_readiness_mutation.py`.

### Left

| Module | Line | Mutant | Why it stays |
| --- | --- | --- | --- |
| `measure.py` | 119 | `now > self.peak` to `>=` | Equivalent: when the new reading equals the peak, setting the peak to it changes nothing. |
| `readiness.py` | 121 | `seconds > 0` to `>= 0` | Equivalent: the row is only worded for a clock more than 5 minutes off, so `seconds` is never 0 there. |
| `readiness.py` | 121 | `seconds > 0` to `seconds > 1` | Equivalent for the same reason: no `seconds` between 0 and 1 reaches that line. |

