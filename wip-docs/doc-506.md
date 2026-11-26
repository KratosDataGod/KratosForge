# Mutation testing, batch 506

A re-score of one module, run with `--kinds all`:
- Local Delta tables: reading a CREATE TABLE ... USING DELTA or an INSERT as a Delta write, reads as of a version or a time, and which column types an append can take (`localdata/delta.py`). It was last scored in batch 1 at 76.7% before and 96.7% after (point mutants only), again in batch 3 at 96.7% with no change needed, and its boundary mutants in batch 159 at 71.4% before and 85.7% after (`--kinds boundary`).

The run was at `main` 7b43ff88 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 329 mutants, so a sample of 150 was run (147 point + 3 boundary).

The test set was the files of batches 1, 3 and 159: test_append_conversions, test_conversions_everywhere, test_local_delta_types, test_parse_errors_plain, test_localdata_delta_mutation, test_safe_parse_readers and test_localdata_delta_boundary.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. The "after" run reran the survivors with `--only`.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `localdata/delta.py` | test_append_conversions, test_conversions_everywhere, test_local_delta_types, test_parse_errors_plain, test_localdata_delta_mutation, test_safe_parse_readers, test_localdata_delta_boundary (+ test_localdata_delta_mutation_b) | 150 of 329 (147 + 3) | 142 / 8 | **94.7%** | 140/147 → 142/147 | 2/3 → 2/3 | 144 / 6 | **96.0%** | 6 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **localdata/delta.py**:
  - A CREATE TABLE ... USING PARQUET is not a Delta write; only a file format named DELTA is.
  - A table read as of a version keeps its schema name, and a table without one has None.
  - → `test_localdata_delta_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `localdata/delta.py` | 452 | the outer `or` of the string/binary clause of `_assignable` to `and` | Equivalent, as batch 1 said: a pair that passes is already equal and returned by `src.equals(dst)` first. |
| `localdata/delta.py` | 273, 275, 286 | `lambda _: False` to `True` | Equivalent, as batch 1 said (lines 273 and 287 then): fallbacks for an older pyarrow without `is_string_view`, `is_binary_view` or `is_list_view`; the installed pyarrow has them. |
| `localdata/delta.py` | 411 | `src.bit_width <= 32` to `<= 33` | Equivalent: integer widths are 8, 16, 32 and 64, so no width is 33. |
| `localdata/delta.py` | 414 | `dst.bit_width >= src.bit_width` to `>` for floats | Equivalent, as batch 159 said: two floats of the same width are the same type. |
