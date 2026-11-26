# Mutation testing, batch 66

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Comparing one table in two places, or now against an earlier Delta version: counts, keys on one side only, changed columns, totals and, for a table larger than memory, a checksum (`meta/compare.py`).
- Best practices for a layer's semantic model, checked on this PC from the metadata project (`model_practices.py`).
- The Learn lab checks: a learner's query against the answer, a table's shape, orphans, best-practice findings, speed and seeded defects (`learn/checks.py`).

The run was at `main` 3ca056487 (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all were run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. `test_learn_guides.py` and `test_practice_docs.py` reach `model_practices.py` but read `docs/`, so they were left out; that can only lower the "before" score.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `meta/compare.py` | test_meta_compare_edges, test_meta_compare_totals, test_dataops (+ test_meta_compare_mutation) | 124 of 124 (122 + 2) | 101 / 23 | **81.5%** | 100/122 → 118/122 | 1/2 → 2/2 | 120 / 4 | **96.8%** | 4 equivalent |
| `model_practices.py` | test_model_practices, test_learn_labs (+ test_model_practices_mutation) | 79 of 79 (78 + 1) | 58 / 21 | **73.4%** | 58/78 → 78/78 | 0/1 → 1/1 | 79 / 0 | **100%** | none |
| `learn/checks.py` | test_learn_labs, test_academy_dbsql_lab (+ test_learn_checks_mutation) | 114 of 114 (109 + 5) | 98 / 16 | **86.0%** | 97/109 → 109/109 | 1/5 → 5/5 | 114 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **meta/compare.py**:
  - A missing table names the side it is missing on (either, the left or the right), with empty counts, rows and changes.
  - Columns only on one side are listed on that side, in a full compare and a hashed one.
  - A column that is a number on one side only gets no total.
  - Samples stop at 20 rows; the counts don't.
  - A keys-only table with the same keys on both sides has nothing changed.
  - A hashed compare starts with empty one-side and changed lists, and its integer totals stay exact integers.
  - Two integer columns hash as integers, so values that differ only past nine significant digits still differ; rounding applies only to an integer matched against a decimal.
  - A checksum is 16 hex digits.
  - Float totals equal within the tolerance (relative to the larger total on either side, or the absolute floor near zero) show no change, by size whatever the sign, and exactly at the bound still counts as the same.
  - → `test_meta_compare_mutation.py`.
- **model_practices.py**:
  - Each rule links to its docs section, and a rule can't be changed once made.
  - A column's model type comes from its contract first; without one, surrogate, key and looked-up columns are whole numbers, and Type 2 columns have their types only in a Type 2 entity.
  - A list of names names five, then counts the rest.
  - A described table with undescribed visible columns is found.
  - An odd table name is found on the table, and an odd column name on the column.
  - The model item takes each table's own columns and types.
  - → `test_model_practices_mutation.py`.
- **learn/checks.py**:
  - An answer is compared row by row up to a million rows; an answer longer than the limit is refused, one at the limit is compared.
  - Wrong columns are named, and the count of differing rows is how many answer rows are missing.
  - The detail says "in the same order" only when order was checked.
  - A defects query that doesn't run fails with why.
  - Speed is measured over three runs each by default, at least one even when none is asked for; exactly the goal passes; times are rounded to four places and the speedup to two; a run too quick to time counts as infinitely faster, with no speedup number.
  - → `test_learn_checks_mutation.py`.

### Left

| Module | Line | Mutant | Why it stays |
| --- | --- | --- | --- |
| `meta/compare.py` | 27 | `if TYPE_CHECKING` negated | Equivalent: it only imports `pyarrow.dataset` at module load instead of for type checking; the annotations are strings, and the functions import it themselves. |
| `meta/compare.py` | 186 | `left or 0` to `left or 1` | Equivalent while `FLOAT_TOLERANCE` equals `FLOAT_FLOOR` (both 1e-9): with a zero or missing left total, the scale `max(1, abs(right))` times the tolerance is never below the floor the original takes anyway. |
| `meta/compare.py` | 186 | `right or 0` to `right or 1` | Equivalent for the same reason, with a zero or missing right total. |
| `meta/compare.py` | 219 | `strict=True` to `strict=False` | Equivalent: both sides' totals come from the same list of columns, so the lists always have the same length. |

