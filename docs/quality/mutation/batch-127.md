# Mutation testing, batch 127

One module not covered before, not touched by an open pull request, run with `--kinds all`, so its score counts the boundary mutants together with the earlier kinds:
- App Studio generation: the plan schema's limits, what a checked plan keeps and cuts, and the layout of the specification made from it (`appstudio/generate.py`).

`proof.py` was planned for this batch but left out because the lane reached its time limit before it could run; it still has no score.

The run was at `main` 5865db954 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, each was checked to exist, and the tests first passed once on the unmutated copy. The module has fewer than 150 mutants, so all of them ran. No `-k` filter or `--deselect` was needed: none of these tests reads `docs/`, `examples/` or `app/`.

Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so the run below can't score a false 100%. The new tests pass with DuckDB 1.5.6 and 2.0.0. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `appstudio/generate.py` | test_appstudio_generate, test_appstudio_generate_edges, test_appstudio_edit, test_appstudio_edit_edges, test_appstudio_publish, test_appstudio_publish_edges, test_appstudio_service_edges (+ test_appstudio_generate_mutation) | 142 of 142 (141 + 1) | 102 / 40 | **71.8%** | 101/141 → 140/141 | 1/1 → 1/1 | 141 / 1 | **99.3%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **appstudio/generate.py**:
  - The plan schema's limits: 10 pages, 12 components a page, titles of 120 and 200 characters, a purpose of 500, at most 5 audiences of 120 and 5 questions of 300, and no other properties.
  - The instruction names the limits and the target's component types.
  - The grid sizes, and a fifth card wraps to a new row.
  - A measure without a formula is referenced by name.
  - A plan keeps at most 10 pages of 12 components, and an untitled page is "Page 1", "Page 2" and so on.
  - The plan's name, purpose, audiences, questions and titles are cut to their limits.
  - What is left out is named within its limits (a type of 40 characters, a measure of 80, a field of 120).
  - Nothing usable lists at most twenty reasons.
  - A table without columns, and metadata without relationships, have none in the context.
  - Only a text component carries its title as text.
  - The sample metadata lists each column once and isn't truncated.
  - → `test_appstudio_generate_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `appstudio/generate.py` | 196 | a wrapped row's height starts at 1 instead of 0 | Equivalent: the row's height is then `max(row_h, h)`, and every component is at least 1 high (`_SIZE` and `_DEFAULT_SIZE`), so it is `h` either way. |

