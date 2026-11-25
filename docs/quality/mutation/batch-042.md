# Mutation testing, batch 42

Three modules not covered before, none touched by an open pull request, run with the tool's default `--kinds all`, as in batch 40. The table shows the point and boundary parts separately.

- The page of Spark read options this PC takes: each option of each format put to the reader's own check, honoured or refused, the note for a refusal other than the usual one, the known limits, the count table, the per-format tables and the names taken under another name (`spark_read_options.py`).
- The RPC parameter check: each parameter's JSON type checked against the outer type of the method's annotation, `Optional` and unions, `Any`, generics, required and unknown names, a method that takes any other name, and params that aren't an object (`rpc_params.py`).
- Batched loads: the batch size from free memory (the default when it can't be told, the floor), the plan that fills batches up to the target with empty values last, a batch's filter and text, and the steps folded by name with their details (`meta/batches.py`).

The run was at `main` 0df53df69 (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No test in these runs reads `docs/` or a module's source.

`test_spark_read_options.py::test_the_docs_page_is_up_to_date` compares the module's output with `docs/spark-read-options.md`, so it was left out of the runs. That makes `spark_read_options.py`'s "before" lower than what the whole suite pins. `test_rpc_fuzz.py` builds its wrong values from `rpc_params` itself, so a change to how a type is read also changed what it tried; the new tests use stand-in methods with fixed signatures and exact refusal messages.

The new tests write no files and use no network or timing.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_read_options.py` | test_spark_read_options (3 tests) (+ test_spark_read_options_mutation) | 17 of 17 (17 + 0) | 3 / 14 | **17.6%** | 3/17 → 14/17 | none | 14 / 3 | **82.4%** | 3 equivalent |
| `rpc_params.py` | test_shortcut_request_only, test_rpc_fuzz (+ test_rpc_params_mutation) | 47 of 47 (47 + 0) | 39 / 8 | **83.0%** | 39/47 → 47/47 | none | 47 / 0 | **100%** | none |
| `meta/batches.py` | test_batches (+ test_meta_batches_mutation) | 50 of 50 (48 + 2) | 37 / 13 | **74.0%** | 36/48 → 46/48 | 1/2 → 2/2 | 48 / 2 | **96.0%** | 2 tuning constants |

`spark_read_options.py` and `rpc_params.py` have no `<` or `>` comparisons, so they have no boundary points. No real bugs were found.

### What the survivors were, and the tests that now kill them

- **spark_read_options.py**:
  - Rows in the file's order, each honoured or refused; no note for the usual refusal or for an option with no limit; a known limit found by the option's lower-case name.
  - Another refusal reason made into the note ("This PC's Spark ...").
  - The count table: honoured and total per format, a format with none.
  - Per-format sections in order, a `|` in a note written as `/`, and the line of names taken under another name, sorted, only where a format has them.
  - The page's first and last lines, and where the page is written.
  - → `test_spark_read_options_mutation.py`, with a stand-in options file.
- **rpc_params.py**:
  - Each plain type refusing every other JSON type, with true or false kept apart from numbers and a whole number taken as a number.
  - An optional type taking null and its own type only; `Any`, a union with `Any` and no annotation taking everything; only a generic's outer type checked.
  - A parameter without a default required, missing and unknown names listed in order, and a method that takes any other name.
  - The rules read from a signature, and a method whose type hints can't be read left to the call.
  - → `test_rpc_params_mutation.py`.
- **meta/batches.py**:
  - A batch's filter (inclusive of both ends, empty values only), its text, and "batch" only for one.
  - The batch size: the default without an estimate or free memory, the memory share over headroom per row, the floor and just above it.
  - The plan: values filling a batch up to exactly the target, one row over starting a new one, an oversized value on its own, value order, and empty values last as one batch (none when they count no rows).
  - Steps folded by name in first-run order, seconds rounded to milliseconds, a detail said once, three details all said, and a fourth or more counted.
  - → `test_meta_batches_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `spark_read_options.py` | 98 | the `__name__ == "__main__"` guard negated or swapped | Equivalent for the tests: it only writes the docs page when run as a script. |
| `spark_read_options.py` | 46 | `has_schema=False` → `True` | Equivalent: each option is checked with no value, and the schema only matters for options that take one. |
| `meta/batches.py` | 24, 25 | `DEFAULT_BATCH_ROWS` 2,000,000 and `MIN_BATCH_ROWS` 50,000 → +1 | Tuning constants: the tests use them by name, and pinning their values would only restate them. |

