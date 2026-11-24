# Mutation testing, batch 84

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Engines that run in a Python runtime of their own: locked installs, the worker's environment, the CLI lookup and the worker session (`engines/runtimes.py`).
- Optimize results waiting on this PC for a central lakehouse that couldn't be reached (`optimize/outbox.py`).
- Estate X-ray: the scan over every lakehouse, its findings, their ranking and what a scan shares (`xray.py`).

The run was at `main` e9097a765 (2026-11-17), with `sidecar/tools/mutate.py` as fixed in #2839, so each mutant ran without a bytecode cache, and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. `optimize/outbox.py` has fewer than 150 mutants, so all were run; the other two were sampled to 150. `engines/runtimes.py` and `optimize/outbox.py` have no `<`, `<=`, `>` or `>=` comparison, so they have no boundary mutants. Nothing merged to `main` between that run and this section touches these modules or their tests.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used, and no test in these sets reads `docs/`, `examples/` or `app/`. For `optimize/outbox.py`, the store-wide tests were given by node id, so only their outbox case ran: the crash-kill test, the durability test and one awkward-path test.

A mutant whose run times out counts as killed, as in earlier batches.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `engines/runtimes.py` | test_runtimes, test_runtimes_edges, test_programs_from_path_only, test_dbconnect, test_engine_conformance (+ test_runtimes_mutation) | 150 of 162 (150 + 0) | 124 / 26 | **82.7%** | 124/150 → 150/150 | none | 150 / 0 | **100%** | none |
| `optimize/outbox.py` | test_optimize_outbox, test_optimize_service, test_optimize_service_edges, test_optimize_service_mutation, test_optimize_share_analysis, test_store_upgrades, the outbox cases of test_store_crash_kills, test_sqlite_durability and test_awkward_paths (+ test_optimize_outbox_mutation) | 70 of 70 (70 + 0) | 58 / 12 | **82.9%** | 58/70 → 69/70 | none | 69 / 1 | **98.6%** | 1 equivalent |
| `xray.py` | test_xray, test_xray_edges, test_xray_staging, test_optimize_service (+ test_xray_mutation) | 150 of 169 (142 + 8) | 95 / 55 | **63.3%** | 95/142 → 138/142 | 0/8 → 8/8 | 146 / 4 | **97.3%** | 4 equivalent |

No bug was found.

### What the survivors were, and the tests that now kill them

- **engines/runtimes.py**:
  - The time limits are 20 minutes for an install, 2 hours for a cell and 10 seconds to stop after a cancel.
  - pip runs hidden, with its output captured as text, never raising on a failed exit, and its log is whatever it printed on either stream, cut to the last 20,000 characters.
  - A CLI file that can't be run isn't used.
  - The worker starts hidden, with text pipes, in a session of its own outside Windows, and its reader thread never keeps the app open.
  - Cells are numbered from 1, and only that cell's result or error answers it.
  - A closed session refuses a cell, and after a cell there is nothing to cancel.
  - A cell's time is from its start to its answer, and a wait or a cell that runs out names its limit in minutes.
  - Closing asks a process that still runs to quit, does nothing when it has ended and the session is closed, and always marks the session closed.
  - → `test_runtimes_mutation.py`.
- **optimize/outbox.py**:
  - The default limits are 200 items and 30 days, an age in days is counted in seconds, and a cap below 1 still keeps the newest item.
  - One trim that drops an item for its age and one past the cap counts both.
  - A failure inside a write undoes it and is raised.
  - Each failure doubles the wait, to the second, and the wait stops growing at an hour.
  - A lakehouse never seen has dropped nothing.
  - An error is kept to its first 500 characters when an item is added, fails or is dropped.
  - → `test_optimize_outbox_mutation.py`.
- **xray.py**:
  - The thresholds are the documented ones, each tested at its edge: 200 small files is high, 30 days without a write is stale, 30 versions suggests a vacuum, a cost from a cent is listed and from $10 is medium, and only the five costliest notebooks are listed.
  - Only small-files advice makes a small-files finding, and a kilobyte is shown as one.
  - A staging name dated up to a day ahead can be the newest, and one a second later can't.
  - Findings of one severity rank by kind before title, more files rank first, and `counts` counts each kind.
  - A scanner reads 8 at a time, and reports its progress from 0 and every tenth table.
  - A scan reads at most `MAX_TABLES` and says when it stopped, and each lakehouse row adds up its tables.
  - A table whose log can't be read adds nothing and isn't called unidentified, and a health without sizes counts as none.
  - Only a lakehouse with neither tables nor files is empty, and one that can't be listed is skipped, not called empty.
  - What a scan shares leaves out people, costs and findings without a lakehouse, and names a table without a schema by its name alone.
  - → `test_xray_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `optimize/outbox.py` | 193 | the attempts cap in the backoff, 16 to 17 | Equivalent: from 6 attempts the wait is already past the one-hour cap, so a higher exponent changes nothing. |
| `xray.py` | 49 | `return str(n)` dropped in `_size` | Equivalent: the loop always returns at TB, so the line is never reached. |
| `xray.py` | 75 | default versions since vacuum 0 to 1 | Equivalent: both are below the 30 that suggests a vacuum. |
| `xray.py` | 179 | the rank of an unknown kind, 9 to 10 | Equivalent: both rank after every known kind, the last of which is 8. |
| `xray.py` | 274 | `zip(..., strict=True)` to `strict=False` | Equivalent: the lakehouses walked and their listings are always the same length. |

