# Mutation testing, batch 90

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Comparing two outputs of the same table: the rules, whole-row and keyed matching, the largest difference per column and the examples listed (`verify.py`).
- The control-flow activities a pipeline runs on this PC: Set and Append Variable, If, Switch, ForEach, Until, Wait, Fail, Filter, Notebook, the data activities and Execute Pipeline (`orchestration/adapters.py`).
- Side by side: the checksum of what each run wrote, the type names shown, the table rows, the verdict, which side was faster and the cores to match (`side_by_side.py`).

The run was at `main` 8838a247c (2026-11-17), with `sidecar/tools/mutate.py` as it is since #2902: each mutant ran without a bytecode cache, and the tests were first run once on the unmutated copy of `sidecar/`, where they passed. The method is the same as earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. All three modules have more than 150 mutants (246, 194 and 202), so 150 of each were sampled. Each test set was run with `-x`, so a mutant's run stops at its first failing test.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. `test_side_by_side.py::test_examples_in_the_repository_are_the_generated_notebooks` reads `examples/`, so it was left out with `--deselect`; no other test in these sets reads `docs/`, `examples/` or `app/`.

The first runs of `verify.py` and `orchestration/adapters.py` used fewer test files: the comparison tests of test_prove (by node id), test_engine_compare_mutation and test_prove_service_edges for `verify.py`, and test_pipeline_adapters_edges, test_pipeline_runner_edges, test_orchestration_runner_mutation and test_pipeline_runs for `orchestration/adapters.py`. Each survivor of those runs was run again with every calling test file, listed in the table, and the "before" figures below are from those runs.

A mutant whose run times out counts as killed, as in earlier batches. One mutant of `orchestration/adapters.py` timed out, in both runs: with the `raise` removed for an Until iteration that failed (line 190), the loop goes on running.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `verify.py` | test_prove, test_engine_compare_mutation, test_prove_service_edges, test_wizard, test_wizard_mutation, test_learn_service (+ test_verify_mutation) | 150 of 246 (148 + 2) | 120 / 30 | **80.0%** | 119/148 → 148/148 | 1/2 → 1/2 | 149 / 1 | **99.3%** | 1 equivalent |
| `orchestration/adapters.py` | test_pipeline_adapters_edges, test_pipeline_runner_edges, test_orchestration_runner_mutation, test_pipeline_runs, test_pipeline_notebooks, test_pipeline_notebooks_edges, test_pipeline_notebooks_mutation, test_pipeline_data_edges, test_pipeline_data_mutation, test_pipeline_script_edges, test_pipeline_script_mutation (+ test_pipeline_adapters_mutation) | 150 of 194 (147 + 3) | 124 / 26 | **82.7%** | 123/147 → 143/147 | 1/3 → 3/3 | 146 / 4 | **97.3%** | 4 equivalent |
| `side_by_side.py` | test_side_by_side, test_side_by_side_edges, test_side_by_side_more_edges, test_side_by_side_cores, test_side_by_side_service_mutation (+ test_side_by_side_mutation) | 150 of 202 (145 + 5) | 136 / 14 | **90.7%** | 133/145 → 142/145 | 3/5 → 5/5 | 147 / 3 | **98.0%** | 3 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **verify.py**:
  - A timestamp unit other than `us`, `ms` or `s` is refused, and so is a key column that isn't in both outputs, by name.
  - Zero examples is accepted, and the row limit defaults to 5,000,000.
  - Whole rows are rounded to the significant digits the relative tolerance leaves: 17 with no tolerance (and the note says so), at least 1 and at most 15 otherwise. A number column is compared as text only when both sides are integers or decimals.
  - A whole-row example says which side has the row and how many more times, whatever the row holds, under this PC's column name. Rows on one side only make the tables differ.
  - With no column in common, only the row counts are compared, in both directions.
  - A keyed example names the side a missing row is on, and a changed row lists only its changed columns, with both values, under every key column.
  - The largest absolute and relative differences are reported only for number columns that changed, an exact (decimal) column included.
  - Timestamps are compared to the unit asked for.
  - Values come back as JSON can hold them (infinity and NaN as text, a decimal or date as text), and a largest difference that isn't a finite number is left out.
  - → `test_verify_mutation.py`.
- **orchestration/adapters.py**:
  - ForEach runs in batches of 20 by default, of `batchCount` when it is a whole number, of 1 when sequential, and of at least 1 and at most 50. It takes up to 100,000 items and no more.
  - If, Switch, ForEach, Until and Execute Pipeline are containers; the other activities aren't.
  - Append Variable, Wait and a finished Until return an empty output.
  - A Switch on a number matches the case written as text.
  - Until runs 10,000 times at most by default. One that is past its deadline, or exactly at it, after its first iteration, or whose first iteration fails, counts one iteration.
  - A timespan reads minutes and days.
  - Only entries with a text key set the pipeline's return value.
  - A Notebook parameter is given as its type (objects and lists as JSON text, `TRUE` as true, `3.0` and `"7"` as whole numbers), a value that isn't its type or a type that isn't known fails, and a parameter name must be a Python name.
  - → `test_pipeline_adapters_mutation.py`.
- **side_by_side.py**:
  - An integer column is summed as an integer unless it is named as a number, and then it matches the same values as decimals.
  - A date and the same date as text give the same checksum, and so does a large string.
  - The checksum is the sum of the row hashes kept to 64 bits.
  - A large string is named `string`, and a timestamp `timestamp`.
  - A table that one side appended to is noted as appended.
  - A run that failed on one side means the results are not the same.
  - A tie is called for this PC, and processing time is compared only when it is shorter than the whole run.
  - A `%%configure` cell asking for no cores leaves a Python notebook's 2.
  - → `test_side_by_side_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `verify.py` | 322 | `row[-2] > row[-1]` to `>=` for an example's side | Equivalent: whole-row examples are only the rows whose counts differ (`na <> nb`), so the two counts are never equal there. |
| `orchestration/adapters.py` | 48 | `append: bool = False` to `True` in the `Ctx` protocol | Equivalent: a `Protocol` stub; its defaults are never used, because the runner's own `set_variable` is the one called. |
| `orchestration/adapters.py` | 52 | `wait: bool = True` to `False` in the `Ctx` protocol | Equivalent, for the same reason. |
| `orchestration/adapters.py` | 157 | the batch of 1 for a sequential ForEach to 0 | Equivalent: the batch is then raised to at least 1, so a sequential ForEach still runs one item at a time. |
| `orchestration/adapters.py` | 329 | `text.count(":") == 2 and "." in ...` to `or` in reading a timespan | Equivalent: every text gives the same number of seconds, or fails the same way, either way. |
| `side_by_side.py` | 55 | `is_string(typ) or is_large_string(typ)` to `and` in the checksum | Equivalent: a text column then falls through to `CAST(... AS VARCHAR)`, which gives the same text and so the same hash. |
| `side_by_side.py` | 137 | `c.get("version", -1)` to `0` for a commit without a version | Equivalent: such a commit is kept only if the version before the run is below its default, and a table's versions start at 0, so neither -1 nor 0 is above it. |
| `side_by_side.py` | 319 | `pc_cores or 1` to `or 0` when this PC's cores aren't known | Equivalent: a positive number of cores wanted is at least 0 and at least 1, so either way the run uses all cores, and a number below 1 is raised to 1 either way. |

