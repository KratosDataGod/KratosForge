# Mutation testing, batch 115

One module not covered before, not touched by an open pull request, run with `--kinds all`, so its score counts the boundary mutants together with the earlier kinds:
- App Studio publishing: the report's name, the readiness check, the fingerprint the confirmation is bound to, and the item definition (`appstudio/publish.py`).

`http_session.py` was planned for this batch but left out because there wasn't time for it before this lane's stop: 17 test files call it, and a fair run needs them all.

The run was at `main` 5865db954 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, each was checked to exist, and the tests first passed once on the unmutated copy. The module has fewer than 150 mutants, so all of them ran. No `-k` filter or `--deselect` was needed: none of these tests reads `docs/`, `examples/` or `app/`.

Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `appstudio/publish.py` | test_appstudio_publish, test_appstudio_publish_edges (+ test_appstudio_publish_mutation) | 55 of 55 (55 + 0) | 51 / 4 | **92.7%** | 51/55 → 54/55 | none | 54 / 1 | **98.2%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **appstudio/publish.py**:
  - A report name is kept up to 256 characters and cut after that.
  - A field the semantic model doesn't have is named by its component's title.
  - The fingerprint doesn't depend on the order of the specification's keys.
  - → `test_appstudio_publish_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `appstudio/publish.py` | 110 | `ensure_ascii=False` to `True` for the fingerprint | Equivalent: it only changes how other characters are spelled in the JSON that is hashed, so equal content still gives equal fingerprints and different content different ones, and the check and the publish both make it the same way. |

