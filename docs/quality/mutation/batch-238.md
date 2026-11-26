# Mutation testing, batch 238

A re-score of `spark_complex.py`, which batch 86 left at **92.5%**, on today's `main`. Since then the module has gained the int-shift typing (#3150: a shift of a tinyint, smallint or int is an int, and `typeof` of one says `int`), so the sample is new.

The run was at `main` ddc89182 (2026-11-20), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The module now has 156 mutants, so 150 were sampled. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. `test_spark_complex.py::test_docs_list_what_still_differs` reads `docs/`, so it was deselected. The machine was shared with other mutation runs, so each mutant's timeout was raised from 120 s to 600 s (`mutate.TIMEOUT`); no run timed out.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_complex.py` | test_spark_complex, test_spark_higher_order, test_spark_complex_mutation, test_spark_complex_cov3 (+ test_spark_complex_rescore, and the existing test_spark_builtins_sql_boundary) | 150 of 156 (149 + 1) | 116 / 34 | **77.3%** | 115/149 → 139/149 | 1/1 → 1/1 | 140 / 10 | **93.3%** | 10 equivalent |

The "before" score is lower than batch 86's because #3150's new code was tested only from `test_spark_builtins_sql_boundary.py`, which wasn't in this module's set. The "after" run adds that file to the set.

One gap was found and pinned with a strict xfail, not fixed: a shift of a shift of an int (`shiftleft(shiftleft(i, 1), 1)`) is typed `bigint` here, and `typeof` says `bigint`. Spark types it `int`, because the inner shift is an int. Only the outer shift's argument type is read, and the inner shift's type is still DuckDB's BIGINT at that point. Test: `test_a_shift_of_a_shift_of_an_int_is_an_int`.

### What the survivors were, and the tests that now kill them

- **spark_complex.py**:
  - `_int_shift_type` finds a marked shift inside brackets, says INT for a tinyint, smallint or int argument, and None for a long, a double, an argument with no type, or a node that isn't a shift.
  - `typeof` of a shift of a long says `bigint`, and `typeof(NULL)` says `void`, not `int`.
  - A shift of a shift keeps its value (12 for `shiftleft(shiftleft(3, 1), 1)`), not the text `int`.
  - `to_json` beside a shift of an int leaves the shift an int: a `to_json` mark doesn't make the typing skip the other marks.
  - `to_json(NULL)` is refused with the "couldn't be worked out" reason and its step, not the reason for a type `to_json` doesn't write.
  - A cast to string, which is rewritten as a lambda sqlglot can't type, keeps its type for `to_json` (`spark_complex.TYPED`).
  - → `test_spark_complex_rescore.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `spark_complex.py` | 82 | `and` to `or` in `_once_arg`'s wrapper check | Equivalent, as in batch 86 (its line 80). |
| `spark_complex.py` | 150 | `and` to `or` when picking the `LATERAL VIEW` calls that get the map forms | Equivalent, as in batch 86 (its line 144). |
| `spark_complex.py` | 189 (×2) | `quote_identifiers=False` and `identify=False` to `True` | Equivalent, as in batch 86 (its line 178): only the typed copy, which is then dropped, is quoted. |
| `spark_complex.py` | 201, 213, 262 | `-1` to `0` as the default id | Equivalent, as in batch 86 (its line 191): every marked argument is given an id before the copy is typed, so the default is never read. |
| `spark_complex.py` | 217 | `or` to `and` in "no type" | Equivalent, as in batch 86 (its line 192): the type is never `None`. An UNKNOWN or NULL type that is let through matches none of the fixes, and `spark_json.type_name` gives None for both. |
| `spark_complex.py` | 241 | the cast to INT's `TYPED` mark `True` to `False` | Equivalent: the mark keeps a node's own type for a later typing pass, and sqlglot works out INT from `CAST(... AS INT)` by itself. |
| `spark_complex.py` | 261 | `and` to `or` when finding a shift's argument | Equivalent: the node `_int_shift_type` reaches is a shift that is not itself a marked argument (the bracket around it is), except a shift inside a shift. There, both forms read a BIGINT and say None. |

