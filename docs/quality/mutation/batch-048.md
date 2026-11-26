# Mutation testing, batch 48

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. None of these modules has a `<` or `>` compare, so every mutant is a point mutant.
- VL_Sources, the source systems' settings as a Fabric variable library: its rows, definition files and reading any library's values (`meta/varlib.py`).
- The optimization lakehouses this PC is connected to, and which one is active (`optimize/registry.py`).
- An assessment as rows for the shared Optimize lakehouse (`opportunities/share.py`).

The run was at `main` f1c8892ef (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. None of the tests run reads `docs/` or `examples/`, so none was left out. `test_estate.py` takes over a minute in full, so the `meta/varlib.py` runs used `-k "varlib or variable or library or keys_workspace or definition_files"`, which keeps its 11 variable library tests and the new ones.

The new tests build their own values and write only under pytest's `tmp_path`: no network, registry or timing.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `meta/varlib.py` | test_estate, filtered (+ test_meta_varlib_mutation) | 46 of 46 (46 + 0) | 41 / 5 | **89.1%** | 41/46 → 45/46 | none | 45 / 1 | **97.8%** | 1 equivalent |
| `optimize/registry.py` | test_optimize_setup, test_corrupt_state_reads, test_optimize_service (+ test_optimize_registry_mutation) | 31 of 31 (31 + 0) | 26 / 5 | **83.9%** | 26/31 → 31/31 | none | 31 / 0 | **100%** | none |
| `opportunities/share.py` | test_opportunities_share (+ test_opportunities_share_mutation) | 49 of 49 (49 + 0) | 31 / 18 | **63.3%** | 31/49 → 49/49 | none | 49 / 0 | **100%** | none |

No real bugs were found.

### What the survivors were, and the tests that now kill them

- **meta/varlib.py**:
  - The Keys workspace rows (`KEYS_WORKSPACE_ID`, `KEYS_WORKSPACE_NAME`) appear only when an environment names a Keys workspace.
  - The definition files are written with two-space indents, as Fabric's own export writes them.
  - → `test_meta_varlib_mutation.py`.
- **optimize/registry.py**:
  - A connection is refused if it lacks either the workspace or the lakehouse, and nothing is saved.
  - Connecting again keeps that lakehouse's own last check, and never another lakehouse's.
  - Forgetting the active lakehouse makes the newest one left active.
  - `connections.json` is written with one-space indents.
  - → `test_optimize_registry_mutation.py`.
- **opportunities/share.py**:
  - The existing tests run a whole assessment, where every opportunity has every field. The new ones build opportunities by hand.
  - A finding's detail names each item by name, or by id when it has none, with its role. It names the overlaps only for an opportunity that isn't counted, and lists the check and undo steps only when there are any.
  - An item needs both a workspace and an item id, and an opportunity with none of those is skipped.
  - A run is partial when the budget was reached or any coverage wasn't read.
  - The run id is `oe-` and 16 hex digits of the compact, key-sorted JSON, whatever the order of the workspaces.
  - The evidence and impact JSON is compact and key-sorted. One more, recorded with the security notes.
  - → `test_opportunities_share_mutation.py`.

### Left

| Module | Line | Mutant | Why it stays |
| --- | --- | --- | --- |
| `meta/varlib.py` | 21 | `if TYPE_CHECKING:` negated | Equivalent: `Project` is only used in annotations, which `from __future__ import annotations` never evaluates, and `meta/project.py` doesn't import `varlib`, so importing it at run time changes nothing. |

