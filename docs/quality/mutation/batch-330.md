# Mutation testing, batch 330

One module, run uncapped: every mutant, not a sample. This is the first mutation run of this module.
- OpenLineage run events for a metadata project's runs (`meta/openlineage.py`).

The run was at `main` 3424d8f33 (2026-11-23), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 137 of 137 mutants ran (134 point + 3 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Each pytest session got a new temporary folder of its own, removed when the session ended (a small plugin, not committed).

The set is test_openlineage, test_openlineage_fuzz (fixed seeds), test_lineage_stale and test_lineage_expr_fuzz, about 12 s a session. test_network_hosts, which also names the module, was left out: it reads `docs/enterprise-install.md`, outside `sidecar/`, so it fails in mutate.py's copy of `sidecar/`. The score counts only the files given. Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed every way. No `-k` filter was used, and no mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `meta/openlineage.py` | test_openlineage, test_openlineage_fuzz, test_lineage_stale, test_lineage_expr_fuzz (+ test_openlineage_mutation) | 137 of 137 (134 + 3) | 125 / 12 | **91.2%** | 124/134 → 131/134 | 1/3 → 2/3 | 133 / 4 | **97.1%** | 4 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **meta/openlineage.py**:
  - A plain source name (not a URL or a jdbc: or odbc: one) keeps a `?` or `#` in it.
  - The project is named by its info's name before its folder's.
  - A run of under a second ends that much after its start.
  - Of two runs of an entity with the same start, the one listed last counts.
  - `_own_sources` stops at a column the entity doesn't make.
  - A column copied from one the entity derives is a TRANSFORMATION, not IDENTITY.
  - An input whose entity isn't in the project is left out of the inputs and the column lineage.
  - → `test_openlineage_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `meta/openlineage.py` | 116, 119, 156 (3) | `re.split('[?#]', …, maxsplit=1)[0]` → `maxsplit=0` | Equivalent: only the part before the first `?` or `#` is read, and that is the same however many splits are made. |
| `meta/openlineage.py` | 204 | `seconds >= 0` → `> 0` | Equivalent: adding 0 seconds leaves the start as it was. |
