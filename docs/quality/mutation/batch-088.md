# Mutation testing, batch 88

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Table clones and warehouse snapshots in T-SQL: finding `CREATE TABLE ... AS CLONE OF` and `ALTER DATABASE ... SET TIMESTAMP` in a batch, the clone's `AT` time, and the local engine's refusal (`tsql_clone.py`).
- Python UDFs on this PC's Spark: `udf`, `pandas_udf`, `spark.udf.register` and `call_udf`, each return type's fit and the Arrow type of a `pandas_udf`'s result (`spark_udf.py`).
- Validate notebook's service: starting, cancelling and listing validations, the clean run and its summary, and the saved report (`validate_service.py`).

The run was at `main` e80b1a87c (2026-11-17), with `sidecar/tools/mutate.py` as fixed in #2839 and #2902, so each mutant ran without a bytecode cache and each run first checked that its tests pass on the unmutated copy. The method is the same as in earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has more than 150 mutants, so 150 were sampled.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways. No `-k` filter was used. From `test_tsql_loud_gaps.py`, only `test_each_item_in_the_docs_refused_list_is_refused` ran (it runs the clone and snapshot statements); the file's other tests read `docs/` or don't reach `tsql_clone.py`.

The first `validate_service.py` run was stopped partway by a time limit and was run again whole; the "before" figures below are from the full run. A mutant whose run times out counts as killed, as in earlier batches.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `tsql_clone.py` | test_tsql_clone, test_tsql_loud_gaps (its clone and snapshot test) (+ test_tsql_clone_mutation) | 150 of 155 (139 + 11) | 125 / 25 | **83.3%** | 120/139 → 139/139 | 5/11 → 11/11 | 150 / 0 | **100%** | none |
| `spark_udf.py` | test_spark_udf, test_spark_api_mutation (+ test_spark_udf_mutation) | 150 of 240 (148 + 2) | 93 / 57 | **62.0%** | 93/148 → 146/148 | 0/2 → 2/2 | 148 / 2 | **98.7%** | 2 equivalent |
| `validate_service.py` | test_validate, test_validate_service_edges, test_regex_anchors, test_integrations_services_edges (+ test_validate_service_mutation) | 150 of 156 (149 + 1) | 119 / 31 | **79.3%** | 118/149 → 147/149 | 1/1 → 1/1 | 148 / 2 | **98.7%** | 2 equivalent |

The two `tsql_clone.py` timeouts (a statement's index never moving on) and one `validate_service.py` timeout (a report's name number never moving on) count as killed, before and after.

One bug was found, outside what the new tests pin, and is fixed; its test in `test_spark_udf_mutation.py` now passes:
- A `pandas_udf` declared to return an `ArrayType` and returning numpy arrays (`s.apply(lambda v: np.array(...))`) gave null for every row, where Spark gives the arrays as lists. A numpy array wasn't turned into a list before its fit was checked, so it didn't fit the array type. A numpy array with dimensions now becomes its list, one value or many.

### What the survivors were, and the tests that now kill them

- **tsql_clone.py**:
  - A clone is a frozen value.
  - A batch the tokenizer can't read (an unclosed string, bracket or comment) has no clone, no snapshot move and an empty refusal.
  - A `CREATE` cut short at the end of a batch, before `CLONE OF`, isn't a clone, and `CLONE OF` with no source is one with no names, refused by name.
  - A source name cut short after its dot keeps the part before it, and the statement ends there.
  - A clone statement ends at its semicolon, or at the end of the batch, and the text after the semicolon is left alone.
  - `SET TIMESTAMP` counts only after `ALTER DATABASE`, from the word right after it up to ten words on.
  - An `AT` time is read to the millisecond: half a second after now is later than now, and a time equal to now is not.
  - → `test_tsql_clone_mutation.py`.
- **spark_udf.py**:
  - A return type that is neither a Spark type nor a string is refused.
  - Each type's fit: a boolean only for a bool; each integer type exactly its range, never a bool or a float; a decimal only for a `Decimal`; any value as its text for a string; a date but not a datetime for a date; a datetime for a timestamp; bytes and bytearray as bytes; a map with its keys and values fitted; a struct from a dict or a sequence of its length.
  - Structs inside a list arrive as rows.
  - Each Spark type's Arrow type, with string for any other.
  - numpy and pandas scalars become Python values, a pandas array its list, and a numpy array its list (once left as it was: the bug above); null is `None`, NaN or NaT, never an array.
  - `udf` of a UDF is that UDF; a UDF's `evalType` is 100 and a `pandas_udf`'s 200.
  - `pandas_udf` called with the function returns the UDF, keeps a return type given by name, refuses a number as the return type after a function, and `functionType` 200 wins over iterator hints.
  - A UDF registers once per connection.
  - `spark.udf.register` returns the UDF, refuses a return type for a UDF made with `udf()`, and Java UDFs are refused.
  - `install` gives Spark 2's `PandasUDFType` numbers.
  - → `test_spark_udf_mutation.py`.
- **validate_service.py**:
  - The newest hundred validations are kept; keeping one again replaces it; the file is indented JSON.
  - Status finds a saved validation by its own id.
  - The list is newest first, twenty by default, with a validation that has no start time last.
  - A failed cell's traceback keeps its last 4,000 characters.
  - The notebook's Runs on setting is read as `pc`, `fabric` or not known.
  - The clean kernel is told its lakehouse, an empty workspace name when there is none, and the lakehouse is sent again only when it or the write guard changes, with the guard as a bool.
  - A start with no lakehouse doesn't sign in, one with a lakehouse does, and the run is a daemon thread.
  - Cancel before the kernel starts is still a cancel, and cancel stops a running kernel.
  - A run with no lakehouse has no sandbox and deletes nothing.
  - A cancelled cell's own time is kept, and a finished cell with no time keeps none.
  - The run's time is the seconds between its start and end, to the millisecond, and a run that took no time says 0.0 s.
  - The summary counts the cells that succeeded before the failure.
  - The environment lists what the code cells import, not the Markdown.
  - The saved report's name has the validation's date and time, and its JSON is indented.
  - Clean up's plan and tidy-ups are returned as they come.
  - → `test_validate_service_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `spark_udf.py` | 157 | `ignore_index=True` to `False` when joining an iterator `pandas_udf`'s parts | Equivalent: only the values are read (`list(out)`), never the index. |
| `spark_udf.py` | 331 | `return False` to `return None` for a name already registered | Equivalent: `_taken`'s answer is only used as a condition. |
| `validate_service.py` | 64 | `sort_keys=True` to `False` in the clean kernel's lakehouse key | Equivalent: the key is compared only with the one made the same way from the same context, so both sort or neither does. |
| `validate_service.py` | 187 | `id[9:24]` to `id[9:25]` in the report's file name | Equivalent: the extra character is the `-` before the id's hex part, and the name's trailing `-` is stripped. |

