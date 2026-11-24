# Mutation testing, batch 331

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- What a change to one column would break: the tables, Python and semantic model that read it (`meta/change_impact.py`).

The run was at `main` ea988e99e (2026-11-20), with `sidecar/tools/mutate.py` as it is since #2902. The module has 165 mutants, and the run ran all 165 of 165 (uncapped). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. The set is every test file that calls `meta/change_impact.py`: test_change_impact and test_change_impact_cov3. Together they take about 4 seconds, so none was left out. No `-k` filter or `--deselect` was needed: no test in the set reads `docs/`, `examples/` or `app/`.

Each pytest session got a fresh temporary folder of its own, made by a small plugin that was not committed and removed again when the session ended, so a folder left by one mutant can't fail the next one (see batch 182). After the run no folder in it was nested deeper than 40 levels, and there was no late run of kills. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (2, 2 and 2).

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `meta/change_impact.py` | test_change_impact, test_change_impact_cov3 (+ test_change_impact_mutation) | 165 of 165 (164 + 1), uncapped | 138 / 27 | **83.6%** | 138/164 → 159/164 | 0/1 → 1/1 | 160 / 5 | **97.0%** | 5 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **meta/change_impact.py**:
  - A module reference with a part that isn't a Python name (`transforms.bad-name`) is never read, even when the file is there.
  - An unknown entity is refused as "No entity ...", and a column the entity doesn't write is refused by name.
  - The changed table's own rows say "this table" and the others say "table".
  - A `[[clean]]` or `[[check]]` step on another column isn't one of the entity's own references to the column.
  - Tables come before Python, and Python before the model, whatever their names. A second reason of the same severity keeps the first chain.
  - A new type flags reports only on columns that copy it, not on a derived column. A drop whose direct reader is derived flags no report.
  - A new type on a column that another table's `exists_in` check looks up is a check, "both sides must keep the same type".
  - A column dropped two copies upstream still flags reports on the last copy, and a relationship on such a copy loses a side.
  - A key column dropped in the model layer loses its relationship but isn't a report column. A renamed relationship column breaks the relationship.
  - Python of a table that doesn't read the changed one isn't listed.
  - → `test_change_impact_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `meta/change_impact.py` | 31 | `_RANK` `"breaks": 2` to 3, and `"check": 1` to 0 | Equivalent: `_RANK` is only compared and sorted on, and "breaks" stays above "check". |
| `meta/change_impact.py` | 106 | the model's sort order 2 to 3 | Equivalent: the model still sorts after tables (0) and Python (1). |
| `meta/change_impact.py` | 122 | `if new_type` negated in the retype wording | Equivalent: the wording (`verb`) is only used when the column goes (a drop or a rename), never for a new type. |
| `meta/change_impact.py` | 223 | `layer and any(...)` to `layer or any(...)` | Equivalent: an entity's layer is always a name, so with no layers the `any` is false too. With a layer, the loop that follows skips every column whose entity isn't in the last layer, so nothing is added. |
