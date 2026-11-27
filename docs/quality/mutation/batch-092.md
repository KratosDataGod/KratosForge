# Mutation testing, batch 92

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Protecting CSV cells a spreadsheet would run as a formula: dictionary columns, `string_view` columns and the count of protected cells (`csv_cells.py`).
- Reading Delta tables that use deletion vectors or column mapping: when DataFusion's reader is needed, reading some columns, the memory cap and its message, and deleted positions (`delta_read.py`).
- Excellence assessment scores: the rating floors, the coverage thresholds, dimensions with little or no weight and the confidence levels (`excellence/scoring.py`).

The run was at `main` 9ab1d2721 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902: each mutant ran without a bytecode cache, and the tests were first run once on the unmutated copy of `sidecar/`, where they passed. The method is the same as earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all of them ran. Each test set was run with `-x`, so a mutant's run stops at its first failing test.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. Two tests in `test_delta_profile_matrix.py` read `docs/`, so they were left out with `--deselect`. `test_csv_cells.py::test_every_csv_writer_protects_its_cells` scans the whole engine for CSV writers, takes about 40 s and doesn't call `csv_cells.py`'s functions, so it was left out with `--deselect` to keep each mutant's run short.

No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `csv_cells.py` | test_csv_cells, test_export_exact, test_model_dictionary, test_shortcut_center (+ test_csv_cells_mutation) | 97 of 97 (97 + 0) | 88 / 9 | **90.7%** | 88/97 → 94/97 | none | 94 / 3 | **96.9%** | 3 equivalent |
| `delta_read.py` | test_delta_read_spark_tables, test_delta_profile_matrix, test_delta_profile_readers (+ test_delta_read_mutation) | 78 of 78 (77 + 1) | 66 / 12 | **84.6%** | 65/77 → 75/77 | 1/1 → 1/1 | 76 / 2 | **97.4%** | 2 equivalent |
| `excellence/scoring.py` | test_excellence (+ test_excellence_scoring_mutation) | 136 of 136 (123 + 13) | 123 / 13 | **90.4%** | 116/123 → 123/123 | 7/13 → 13/13 | 136 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **csv_cells.py**:
  - A dictionary column with nothing to protect is left as it is, and counts no cell.
  - A risky dictionary value that no row uses counts no cell; one that rows use is counted once per row.
  - A `string_view` batch or table with nothing to protect still goes to the writer as plain strings.
  - → `test_csv_cells_mutation.py`.
- **delta_read.py**:
  - Column mapping is found from the table setting alone and from the protocol alone, and a mode of `none` is no column mapping.
  - The whole-read cap is 2 GiB.
  - A read over the cap names the features and both sizes ("2000.0 GB, more than 1000 GB").
  - Some columns of a table with deletion vectors are read in the order asked, through `arrow` and `head`.
  - A table without deletion vectors has no deleted positions (an empty dict).
  - → `test_delta_read_mutation.py`.
- **excellence/scoring.py**:
  - Each rating starts at its floor (90, 80, 70, 50 and 0).
  - A rounded zero is never negative.
  - Coverage exactly at the provisional and rated thresholds.
  - A dimension with a weight of 1 still counts, and one with no weight is left out, also when it holds the only outcomes.
  - Confidence exactly at its thresholds.
  - → `test_excellence_scoring_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `csv_cells.py` | 92, 96, 113 | `zip(strict=True)` to `False` | Equivalent: both sides come from the same schema or batch, so they always have the same length. |
| `delta_read.py` | 82 | `get_add_actions(flatten=True)` to `False` | Equivalent: in deltalake 1.6.6 `size_bytes` is a top-level column either way. |
| `delta_read.py` | 149 | `zip(strict=True)` to `False` | Equivalent: both columns come from the same deletion-vector table, so they always have the same length. |

