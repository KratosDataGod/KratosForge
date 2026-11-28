# Mutation testing, batch 211

Column lineage and what is built on it, run uncapped (every mutant, not a sample of 150), boundary mutants included:
- Column lineage itself (`meta/lineage.py`, last scored in batch 34 at 96 points; the lineage hardening since has added 8).
- What a column change breaks (`meta/change_impact.py`).
- OpenLineage run events (`meta/openlineage.py`).
- Reading an expression for the save gate and for lineage: `sql_expr` and `check_expr` in `meta/project.py` (lines 529 to 548 only, with `--only`; the rest of the module was scored in batches 14 and later).

The run was at `lineage-mutation` 5beec75 (`main` with the lineage hardening, 2026-11-21), with `sidecar/tools/mutate.py` as it is since #2902 (`--cap 1000`, seed 7, `--kinds all`). The tests given first passed once on the unmutated copy of `sidecar/`, and no test needed `--deselect`. Each "after" run re-checked only the survivors with `--only`, except `openlineage.py`, which was run again in full because the fix below changed it. One `openlineage.py` mutant (`item in seen or item not in cols` → `and` in `_own_sources`) loops for ever and timed out both times; it counts as killed.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `meta/lineage.py` | test_meta_lineage_mutation, test_lineage_edges, test_lineage_stale, test_lineage_expr_fuzz, test_lineage_scale, test_lineage | 104 of 104, uncapped | 103 / 1 | **99.0%** | no change needed | **99.0%** | 1 equivalent |
| `meta/change_impact.py` | test_change_impact, test_change_impact_cov3, test_lineage_edges, test_lineage_stale, test_lineage_expr_fuzz, test_lineage_scale (+ test_meta_change_impact_mutation) | 165 of 165, uncapped | 146 / 19 | **88.5%** | 160 / 5 | **97.0%** | 5 equivalent |
| `meta/openlineage.py` | test_openlineage, test_lineage_edges, test_lineage_stale, test_lineage_expr_fuzz, test_lineage_scale (+ test_meta_openlineage_mutation) | 139 of 139, uncapped | 129 / 10 | **92.8%** | 133 / 6 | **95.7%** | 6 equivalent |
| `meta/project.py` `sql_expr`, `check_expr` | test_meta_project_edges, test_meta_project_uncapped, test_meta_project_mutation, test_lineage_expr_fuzz | 11 (of 480), `--only` | 9 / 2 | **81.8%** | 11 / 0 | **100%** | none |

### Real bug found and fixed

- **openlineage.py, `_latest`**: two runs of one entity that started at the same time were told apart by `str()` of their run id, status, seconds and start, so a run with `seconds = 1` and one with `seconds = "1"` tied, and whichever came later in the rows was exported, with different event times (a text duration isn't added). The docstring promises the rows' order never changes which run is exported. It now uses `repr()`, so the two no longer tie; a tie now means every value the event is made of is the same. Found by the boundary mutant `>=` → `>` at line 307, which is equivalent after the fix.

### What the survivors were, and the tests that now kill them

- **change_impact.py**:
  - Tables listed before Python before the model when their severities tie, whatever their names; a second reason of the same severity keeps the first chain.
  - A `transforms.` module whose name isn't an identifier (`transforms.bad-name`) is never read, even when the file is there.
  - An unknown entity is named in the refusal (a `KeyError` is a `LookupError` too, so the type alone didn't pin it).
  - A consumer's detail: `this table` for the entity itself, `table` for another.
  - An `exists_in` check elsewhere is a `check` on a new type and a `breaks` on a drop, each with its own reason.
  - Dropping a hidden key column (`customer_sk`) breaks its relationship without a "reports that use" check; renaming it breaks the relationship.
  - A drop carried two steps on breaks a relationship made by the key's name (`same name as ...'s surrogate key`).
  - Python of a table that doesn't read the changed one isn't listed.
  - → `test_meta_change_impact_mutation.py`.
- **openlineage.py**:
  - The project is named by its `info` name, and by its folder only without one.
  - A run shorter than a second still ends after it starts.
  - A name with `#` or `?` that isn't a driver URL is kept whole.
  - Inputs from an entity the project doesn't have are left out.
  - Two runs alike but for the type of their seconds export the same event in either order (the bug above).
  - → `test_meta_openlineage_mutation.py`.
- **project.py**: a non-text expression is refused with its own message (without the check, the regex raises a `TypeError` of its own); 200 operators parse and 201 are refused, and operators inside quotes aren't counted. → `test_meta_project_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `meta/lineage.py` | 110 | `first_derived.get(c, -1) >= derive_at[col]` → `>` | Equivalent: the two are equal only when `c` is `col` itself, and then `ref(c, col)` gives `src_inputs(c)` too. |
| `meta/change_impact.py` | 31 (2) | `_RANK` breaks 2 → 3, check 1 → 0 | Equivalent: only the order of the two ranks is used. |
| `meta/change_impact.py` | 106 | the model's place 2 → 3 | Equivalent: it stays last. |
| `meta/change_impact.py` | 122 | `if new_type` negated in the retype verb | Equivalent: the verb is only used when the column goes (a drop or rename), so the retype one is never read. Dead code, noted rather than changed. |
| `meta/change_impact.py` | 223 | `layer and any(...)` → `or` | Equivalent: a shortcut; with it gone the model is planned and every column outside the last layer is skipped in the loop. |
| `meta/openlineage.py` | 116, 119, 156 | `maxsplit=1` → `0` | Equivalent: only the part before the first `?` or `#` is kept, however many splits are made. |
| `meta/openlineage.py` | 211 | `seconds >= 0` → `> 0` | Equivalent: adding 0 seconds changes nothing. |
| `meta/openlineage.py` | 268 | `sub == "IDENTITY"` → `!=` | Equivalent: `up is not inp` holds only for a derive, lookup or key (already a transformation); a plain copy's inputs never come from its own entity. The line is dead code, noted rather than changed. |
| `meta/openlineage.py` | 307 | `_latest(r) >= _latest(...)` → `>` | Equivalent since the fix above: runs that tie are alike in everything the event is made of. |
