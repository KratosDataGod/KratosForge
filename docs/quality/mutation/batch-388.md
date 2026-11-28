# Mutation testing, batch 388

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- Spark's to_json on this PC: JSON written by each value's type as Spark writes it, its options, and typeof's names for structs and maps (`spark_json.py`).

The run and the "after" run were both at `main` 9c7201ae4 (2026-11-24), with `sidecar/tools/mutate.py` as it is since #2902. The module has 160 mutants, and the run ran all 160 of 160 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is test_spark_json_cov2, test_spark_complex_cov3, test_sql_boundary, test_sql_cov2, test_sql_mutation, test_spark_json, test_spark_complex_rescore and test_spark_json_values, about 20 seconds together. The first try stopped before any mutant, because one test failed in the copy of `sidecar/`: test_spark_json_values' check against Spark 3.5's captured answers reads them from outside `sidecar/`. It was deselected and the run started again; the score counts only the tests given. That check covers most of the types to_json writes, which is why so many mutants survived without it.

Each pytest session got a fresh temporary folder of its own, as in batch 331. As in batch 387, a check used only for these runs refused any connection to anything but this machine; it wrote down none. After the run no folder in it was nested deeper than 40 levels. The run ends on survivors, not on a streak of kills. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (12, 1 and 1).

The new tests run to_json through this PC's `spark.sql`, with the session time zone set to UTC. Before the "after" run, they were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_json.py` | test_spark_json_cov2, test_spark_complex_cov3, test_sql_boundary, test_sql_cov2, test_sql_mutation, test_spark_json, test_spark_complex_rescore, test_spark_json_values (one test deselected) (+ test_spark_json_mutation) | 160 of 160 (157 + 3), uncapped | 104 / 56 | **65.0%** | 104/157 → 150/157 | 0/3 → 2/3 | 152 / 8 | **95.0%** | 8 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **spark_json.py**:
  - to_json writes each type as Spark does: true and false, a double as 1.0E10 and NaN quoted, a date, a timestamp with Z in UTC and one without a zone, binary as base64, and nested arrays.
  - It writes maps by their key type (text, int, boolean, and a date as its days since 1970-01-01), structs with arrays and maps three deep, and an empty array of no type.
  - A decimal is written in Java's form: 1E-7 for scale 7, 1.50 for scale 2, and 1.000001 for scale 6. A decimal with no scale given has scale 0.
  - NULL struct fields are kept only when ignoreNullFields is false.
  - The dateFormat, timestampFormat and timestampNTZFormat options are read.
  - to_json of a plain value is refused by name.
  - Options are read by name in any case. One this PC doesn't read is refused by name, and options that aren't text pairs are refused. More names than values is an error.
  - typeof names decimals with and without a scale (decimal(10,0) by default), structs of two fields, arrays of structs, and maps of arrays, and names none for a type it isn't sure of.
  - → `test_spark_json_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `spark_json.py` | 114 | a decimal with no scale given has scale 1, not 0 | Equivalent: scale 0 and 1 are both 6 or under, so either way the decimal is written plain. |
| `spark_json.py` | 116 | `scale <= 6` to `< 6` | Equivalent: at scale 6 the E-form branch checks the adjusted exponent, which is never below -6 at that scale, so it writes the plain text too. |
| `spark_json.py` | 76 | an array's element written at the same depth, not one deeper | Equivalent: the depth only names the lambda's variable. An inner lambda that reuses the outer name only reads its own, because the list it works on is read outside it. Nested arrays, maps and structs give the same JSON. |
| `spark_json.py` | 88 | a map's value written at the same depth | Equivalent, as the line above. |
| `spark_json.py` | 98 | a struct field written at the same depth | Equivalent, as the line above. |
| `spark_json.py` | 155 | the outermost value written at depth 1, not 0 | Equivalent: only the lambdas' names change. |
| `spark_json.py` | 83 | a map key written at depth - 1, and at the same depth | Equivalent (two mutants): the keys written here (int, boolean, decimal) never use the depth. |
