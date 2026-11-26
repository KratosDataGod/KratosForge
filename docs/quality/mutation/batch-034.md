# Mutation testing, batch 34

Three modules, none touched by an open pull request, each with tests that run in a few seconds:
- `spark.sql` parameters: the 32-bit edge between INT and BIGINT literals, NaN and the infinities as DOUBLE, a Decimal Spark's DECIMAL can't hold, `?` inside a string, which colons are markers, one DataFrame used twice in `{}` fields, and `getArgument` calls that are cut short or in SQL that can't be read (`spark_sql_params.py`).
- Column lineage: derive and lookup entries without their column, a derive that reads its own name, an expression that can't be read, mode and Type 2 history columns added once, a lookup's dimension key, and upstream walks past a missing entity or round a cycle (`meta/lineage.py`).
- The SQL compatibility profiles: which targets run on this PC, and each documentation link's last-updated date (`sqlcompat/profiles.py`).

Batch 33's modules were left to batch 33, which ran at the same time.

The run was at `main` fd8230b51 (2026-11-15), with `sidecar/tools/mutate.py` and the same method as batches 1 to 33 (sample cap 150, seed 7), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Every test that was kept passed both ways (369 passed), so no run below can score a false 100%. No test in these runs reads `docs/` or a module's source.

None of these modules writes files, and the new tests use no network and no timing. The lineage tests use stand-in entities, so no project is created on disk.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `spark_sql_params.py` | test_spark_sql_params, test_sql_cell_widgets (+ test_spark_sql_params_mutation) | 150 of 169 | 128 / 22 | **85.3%** | 148 / 2 | **98.7%** | 2 equivalent |
| `meta/lineage.py` | test_lineage, test_meta_service_edges (+ test_meta_lineage_mutation) | 96 of 96 | 85 / 11 | **88.5%** | 96 / 0 | **100%** | none |
| `sqlcompat/profiles.py` | test_learn_fixes_edges, test_sql_compat (+ test_sqlcompat_profiles_mutation) | 15 of 15 | 11 / 4 | **73.3%** | 15 / 0 | **100%** | none |

No real bugs were found.

### What the survivors were, and the tests that now kill them

- **spark_sql_params.py**:
  - 2,147,483,647 and -2,147,483,648 written as INT, and one past either end as BIGINT (`L`).
  - NaN, Infinity and -Infinity written as DOUBLE casts, each with its own text.
  - Decimal NaN and the infinities refused, naming DECIMAL.
  - `'?'` in a string not counted as a positional marker.
  - `(x)`, a colon at the end of the query and `a:b` not read as markers, and `:a` at the very start bound.
  - One DataFrame in two `{}` fields registered once under one name, and a second DataFrame under another.
  - `getArgument` in SQL that can't be tokenized left as it is, and calls cut short (no parenthesis, no closing parenthesis after the name or after the default) left alone.
  - Another function called on a string not filled as if it were `getArgument`.
  - → `test_spark_sql_params_mutation.py`.
- **meta/lineage.py**:
  - Derive and lookup entries with no `column` or `as`, and entries that aren't tables, skipped.
  - A derive of `a` from `a * 2` reading the source's `a`, with no note.
  - An expression that can't be read giving no inputs and its note.
  - Mode columns added once, after the contract's columns.
  - Type 2 history columns only on a Type 2 entity, each once.
  - A lookup naming the dimension's key only when the dimension exists and has one.
  - Upstream of an entity the project doesn't have being empty, and upstream stopping at a cycle.
  - → `test_meta_lineage_mutation.py`.
- **sqlcompat/profiles.py**:
  - Only Spark SQL and DuckDB run on this PC.
  - Each documentation link carrying its last-updated date, not its URL.
  - → `test_sqlcompat_profiles_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `spark_sql_params.py` | 43 | `value > 0` → `value > 1` | Equivalent: the value there is always an infinity, so both comparisons agree. |
| `spark_sql_params.py` | 103 | `zip(..., strict=True)` → `strict=False` | Equivalent: the lengths are checked just before, so the zip never sees a difference. |

### Not in this batch

Batch 33's modules.

