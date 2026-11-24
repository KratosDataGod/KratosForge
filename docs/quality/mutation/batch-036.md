# Mutation testing, batch 36: boundary mutants

A Quality review of batch 35 found a gap in the tool itself. `sidecar/tools/mutate.py` flipped a comparison to its opposite (`<` to `>=`, `<=` to `>`), but it never moved a comparison's boundary (`<` to `<=`, `>` to `>=`). So a value sitting exactly on an edge, which is where off-by-one bugs live, was never tested. Chained comparisons such as `since <= start < until` were already mutated one operator at a time; only the boundary move was missing.

**What changed in the method.** `mutate.py` now also makes boundary mutants: each `<`, `<=`, `>` and `>=` in a comparison is moved to its neighbour. `--kinds` chooses which mutants to make:
- `all` (the default from this batch on): the earlier mutants, then the boundary ones.
- `point`: only the earlier mutants. Their indexes are the same as before, so `--seed` and `--only` still match the runs in batches 1 to 35.
- `boundary`: only the boundary mutants.

**Earlier scores leave boundary mutants out.** The scores in batches 1 to 35 count only the mutants the tool made then. They are left as they were. Every run from this batch on includes boundary mutants.

The run was at `main` 805786e9 (2026-11-15) with `--kinds boundary` (every boundary point, no sampling), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Eight recent modules with fast tests were re-run, each with the test set its own batch lists. For cpu_cap, databricks_ip and usage/reconcile, that set includes batch 35's tests, which were still in review (#2688), so they were copied in for the runs only.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways. The tests that read `docs/` are left out as in the batches listed.

## Results (boundary mutants only)

| Module | Test files run (its batch) | Boundary mutants | Before: killed / survived | Before | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `cpu_cap.py` | batch 35's set | 2 | 2 / 0 | **100%** | no change needed | **100%** | none |
| `databricks_ip.py` | batch 35's set | 1 | 0 / 1 | **0%** | 1 / 0 | **100%** | none |
| `usage/reconcile.py` | batch 35's set | 13 | 3 / 10 | **23.1%** | 13 / 0 | **100%** | none |
| `spark_sql_params.py` | batch 34's set | 9 | 8 / 1 | **88.9%** | 8 / 1 | **88.9%** | 1 equivalent |
| `databricks_variant.py` | batch 28's set | 19 | 13 / 6 | **68.4%** | 15 / 4 | **78.9%** | 4 equivalent |
| `nbsymbols.py` | batch 27's set | 22 | 6 / 16 | **27.3%** | 13 / 9 | **59.1%** | 9 equivalent |
| `orchestration/expressions.py` | batch 31's set | 16 | 13 / 3 | **81.3%** | 16 / 0 | **100%** | none |
| `localdata/tsql_format.py` | batch 31's set | 12 | 9 / 3 | **75.0%** | 12 / 0 | **100%** | none |
| **All eight** | | 94 | 54 / 40 | **57.4%** | 80 / 14 | **85.1%** | 14 equivalent |

Every boundary mutant that isn't equivalent is now killed. The new tests are in `sidecar/tests/test_boundary_mutation.py`, added to each module's set for the "after" runs.

One real bug was found, filed as #2691, with a strict xfail that flips when it's fixed: on the local engine, T-SQL `FORMAT(value, 'N<p>')` fails with DuckDB's own conversion error when the value has more integer digits than `38 - p`, for example `FORMAT(1.5, 'N38')`.

### What the survivors were, and the tests that now kill them

- **usage/reconcile.py**:
  - A timed row starting exactly at the period's start counted, and one starting exactly at its end not.
  - An item total from exactly the start to exactly the end counted, and one a second past either edge counted as partly outside.
  - An item total that only touches the period (ends at its start or starts at its end) not counted as partly outside.
  - A run starting exactly at the period's end not in the period.
  - A row starting exactly when a run starts, or exactly when it finishes, matched to it.
  - Of two bills for the same slot on a full tie, the first one seen kept.
- **databricks_ip.py**: a network BLOB with the longest prefix (/32, /128) accepted, and one longer refused.
- **databricks_variant.py**: a VARIANT DOUBLE of exactly 0.001 printed plain, and an index one past an array's end not found.
- **nbsymbols.py**: definitions on a cell's last line with no newline after it (`def`, `import`, `except ... as`, a `case` name), a name on the last line after a wide character, the cursor right after a name, and a cell index one past the last.
- **orchestration/expressions.py**: an interpolation at the start of a value that ends with `@`, and expressions cut short (`output[`, `output['x'`, `concat(`) reported as not closed, keeping the references they could read.
- **localdata/tsql_format.py**: `N38` and seven `f`s accepted (the most each takes), and `yy` giving the two-digit year.
- → `test_boundary_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `spark_sql_params.py` | 43 | `value > 0` → `value >= 0` | Equivalent: the value there is always an infinity (as in batch 34). |
| `databricks_variant.py` | 59 | `copysign(1, value) < 0` → `<= 0` | Equivalent: `copysign(1, x)` is 1 or -1, never 0. |
| `databricks_variant.py` | 64 | `value < 0` → `value <= 0` | Equivalent: zero returns earlier. |
| `databricks_variant.py` | 219 | `0 < at` → `0 <= at` | Equivalent: `at` starts at 1 or -1 and only grows from 1, so it is never 0. |
| `databricks_variant.py` | 219 | `at < len(text)` → `at <= len(text)` | Equivalent: at the end, the step pattern matches nothing and the loop stops the same way. |
| `nbsymbols.py` | 118, 125, 134, 148, 220, 225, 230, 256, 268 | `0 < line` → `0 <= line` (9 mutants) | Equivalent: Python's line numbers start at 1, so a line is never 0. |

### Not in this batch

The other modules from batches 1 to 35. They can be re-run with `--kinds boundary` in later batches.

