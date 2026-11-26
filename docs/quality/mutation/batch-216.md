# Mutation testing, batch 216

One module, run uncapped: every mutant, not a sample. Batch 29 sampled 150 of its then 214 point mutants (81.3% before, 95.3% after), and batch 165 ran its 6 boundary mutants (66.7% before, 83.3% after).
- Schema drift review and what a write changes in a table's schema (`meta/drift.py`).

The run was at `main` ddc89182d (2026-11-20), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 220 of 220 mutants ran (214 point + 6 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Each pytest session got a new temporary folder of its own, removed when the session ended (a small plugin, not committed).

The set is batches 29 and 165's files with two more that check `widens` and `schema_changes`: test_breaking_changes and test_meta_write_types. The score counts only the files given. Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed every way. No `-k` filter was used.

A mutant whose run times out counts as killed, as in earlier batches. One timed out, in the first run: `end -= 1` to `end -= 0` when a section ends in blank lines (line 93), a loop that never ends.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `meta/drift.py` | test_meta_drift_mutation, test_meta_drift_boundary, test_meta_drift_edges, test_schema_evolution, test_breaking_changes, test_meta_write_types, test_dataops, test_dataops_edges (+ test_meta_drift_uncapped) | 220 of 220 (214 + 6) | 203 / 17 | **92.3%** | 198/214 → 201/214 | 5/6 → 5/6 | 206 / 14 | **93.6%** | 14 equivalent |

No bugs were found. The score stays under 95% because every mutant left is equivalent.

### What the survivors were, and the tests that now kill them

- **meta/drift.py**:
  - A new `[contract.columns]` section goes right after an existing `[contract]` section, before the next section, with the new column under it.
  - A uint16 widens to a decimal only with room for its 10 digits left of the point.
  - → `test_meta_drift_uncapped.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `meta/drift.py` | 30 | `base_type()` used the other way round | Equivalent, as in batch 29: a Polars type and its instance find the same entry, and a type with parameters is in the table neither way. |
| `meta/drift.py` | 92 (3) | the trailing-blank loop's bound `start + 1` to `start - 1`, `start + 0` or `>=` | Equivalent: the line at `start` is the section's header, which isn't blank, so the loop stops there either way. |
| `meta/drift.py` | 107 (3) | a new columns section's place `(at + 1, at + 2)` to `(at - 1, …)`, `(at + 0, …)` or `(…, at - 2)` | Equivalent: the lines kept from the slice are the blank line and the header just inserted (or none), so the same lines are written. |
| `meta/drift.py` | 128 (2) | a new `[contract]` section's place `len - 1` to `len + 1` or `len - 0` | Equivalent: both slices are past the end, so the drift line is appended in the same place. |
| `meta/drift.py` | 140 | a new `[source]` section's end `1` to `0` | Equivalent: the rename line is inserted at line 1 either way. |
| `meta/drift.py` | 182 | `_BITS[old] <= 32` to `<= 33` | Equivalent: no integer type has 33 bits. |
| `meta/drift.py` | 186, 188 (3) | `or 38` to `or 39` | Equivalent, as in batch 29: Polars gives a decimal with no precision 38 (1.5.6 and 2.0 environments), so the fallback is never read. |
