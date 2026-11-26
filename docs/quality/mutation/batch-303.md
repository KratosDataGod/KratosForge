# Mutation testing, batch 303

This batch scores one SQL translator module that had not been scored before: `spark_json.py`. It ran with `--kinds all`, so the score counts the boundary mutants as well as the earlier kinds, and the table shows each part.
- Spark's `to_json` on this PC. This covers how each type is written (including nested arrays, maps and structs, and decimals in Java's plain or E form), the `to_json` options it reads, and the names `typeof` gives a struct or map.

The run was at `main` 29e00e70d (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The module has 160 mutants, and 150 were sampled. Test files were given to the tool as paths relative to `sidecar/`. They passed once on the unmutated copy first. Each pytest session got a new temporary folder, removed when the session ended. No `-k` filter was used. One test in `test_spark_json_values.py`, `test_each_refusal_of_json_is_still_needed_and_listed`, was deselected because it reads `docs/sql-compatibility.md`, which the tool's copy of `sidecar/` doesn't hold. No mutant's run timed out.

Before the "before" run, the tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. The new tests pass on DuckDB 2.0.0 and on sqlglot 30.23.0 and 30.27.0 too.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_json.py` | test_spark_json_values (one test deselected), test_spark_json_cov2, test_spark_complex_cov3, test_spark_complex_rescore (+ test_spark_json_mut303) | 150 of 160 (147 + 3) | 129 / 21 | **86.0%** | 129/147 → 140/147 | 0/3 → 2/3 | 142 / 8 | **94.7%** | 8 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **spark_json.py**:
  - Nested values are written inside each other: an array of arrays (also three deep), a struct holding an array, a map whose value is an array, and an array of such maps.
  - A decimal given only its precision has scale 0.
  - At scale 7, a value below one millionth switches to Java's E form (`1E-7`).
  - The options are refused unless the names and the values are both written as lists. Values must be text, not numbers.
  - More option names than values never pass.
  - `typeof` names a decimal without its size `decimal(10,0)`. With only a precision it names it `decimal(5,0)`.
  - These tests are in `test_spark_json_mut303.py`. Each expected value is what Spark 3.5 writes.

### Mutants left, and why

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `spark_json.py` | 76, 88, 98 | `depth + 1` to `depth + 0` for an array's element, a map's value or a struct's field | Equivalent: the depth only names the lambda variable. With the same name, the inner lambda shadows the outer one. Each lambda refers only to its own variable, and the list it walks is read outside it, so the SQL gives the same answer. The tests above, which nest values three deep, pass with this mutant. |
| `spark_json.py` | 155 | starting depth `0` to `1` | Equivalent: it only renames every lambda variable, `kf_j0` becoming `kf_j1` and so on. |
| `spark_json.py` | 83 | `depth + 1` to `depth - 1`, and to `depth + 0`, for a map's key | Equivalent: a key can only be a whole number, a boolean or a decimal, and none of those uses a lambda variable. A decimal's own lambda variable has a fixed name. |
| `spark_json.py` | 114 | `else 0` to `else 1` for the scale of a decimal with no size | Equivalent: a scale of 0 and a scale of 1 are both 6 or less, so both are written plain. |
| `spark_json.py` | 116 | `scale <= 6` to `scale < 6` | Equivalent: at scale 6 the E-form branch always gives the plain text too. The adjusted exponent there is the digit count minus 7, so at least −6, and the plain form is used from −6 up. |
