# Mutation testing, batch 482

One module not scored before, run with `--kinds all`:
- Spark's result types for CASE, if, coalesce, nvl, nvl2, greatest, least and UNION, INTERSECT and EXCEPT in Spark SQL on this PC (WR-259): each value is cast to Spark's wider type of them all (text with a number is a string, an integer with a decimal the decimal that holds both), and a mix Spark stops on, such as a boolean with a number, is refused by name (`spark_branch_types.py`).

The run was at `main` c9716c45 (2026-11-26), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 288 mutants, so a sample of 150 was run.

The test set was test_spark_branch_types.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. The new tests are pure sqlglot: they read and write statements without running them.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_branch_types.py` | test_spark_branch_types (+ test_spark_branch_types_mutation_b) | 150 of 288 (149 + 1) | 132 / 18 | **88.0%** | 132/149 → 146/149 | 0/1 → 0/1 | 146 / 4 | **97.3%** | 4 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **spark_branch_types.py**:
  - Spark's names for each whole number type and a decimal, and a refusal that names TINYINT, SMALLINT or DECIMAL(2,1) beside BOOLEAN.
  - A float with a whole number is a float either way round, and a date with a timestamp is a timestamp.
  - A literal with an exponent is a double, also with a minus sign.
  - A side of a set operation with a star can't be told, and a column of unknown type beside a known one is left alone.
  - Only marked set operations are typed. A column the schema doesn't know doesn't stop the typing.
  - A statement that can't be typed comes back as it was.
  - A cast column keeps its name, quoted.
  - → `test_spark_branch_types_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `spark_branch_types.py` | 78 | two ints: `a[1] >= b[1]` to `>` | Equivalent: two ints of the same digits are the same kind, which `a == b` returns just above. |
| `spark_branch_types.py` | 170 | `_needs` returns None instead of False | Equivalent: its caller only tests whether it is true. |
| `spark_branch_types.py` | 205 | `qualify(..., identify=True)` | Equivalent: the qualified copy is read only for its types, and quoting its names doesn't change them. |
| `spark_branch_types.py` | 286 | `zip(..., strict=True)` to `strict=False` | Equivalent: the two lengths were compared equal just before. |
