# Mutation testing, batch 110

Two modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants together with the earlier kinds:
- Excellence findings: what a finding and an advisory finding carry, their stable id, the ranking and the next actions (`excellence/findings.py`).
- Deleting a folder the way Windows lets it be deleted: busy and read-only files, links, and the retries (`trees.py`).

The run was at `main` 5865db954 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, each was checked to exist, and the tests first passed once on the unmutated copy. Each module has fewer than 150 mutants, so all of them ran. No `-k` filter or `--deselect` was needed: none of these tests reads `docs/`, `examples/` or `app/`. `test_trees.py` reads the engine's own files under `sidecar/`, which the tool copies.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `excellence/findings.py` | test_excellence, test_excellence_catalog (+ test_excellence_findings_mutation) | 26 of 26 (26 + 0) | 13 / 13 | **50.0%** | 13/26 → 24/26 | none | 24 / 2 | **92.3%** | 2 equivalent |
| `trees.py` | test_trees (+ test_trees_mutation) | 30 of 30 (29 + 1) | 21 / 9 | **70.0%** | 20/29 → 28/29 | 1/1 → 1/1 | 29 / 1 | **96.7%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **excellence/findings.py**:
  - A finding id is `fx-` and 16 hex digits.
  - A failed finding takes its text and evidence from the outcome; an advisory one takes them from the advice, and is empty when there is none.
  - The outcome's detail is kept, and a missing one is empty.
  - With severity tied, stronger evidence comes first, then less effort, then less risk, at each step of each scale.
  - There are five next actions by default.
  - → `test_excellence_findings_mutation.py`.
- **trees.py**:
  - WinError 145 alone makes a folder busy, and another code doesn't.
  - A link is retried without `chmod`, so the file it points to stays read-only.
  - A read-only file is made writable with nothing else added, and a folder also gets its execute bit.
  - What isn't an `OSError` is raised at once, and so is a different failure on a retry.
  - → `test_trees_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `excellence/findings.py` | 22 | effort and risk `"high": 2` to `3` | Equivalent: only the order is used, and 3 is still above medium's 1. |
| `excellence/findings.py` | 23 | evidence `"insufficient": 3` to `4` | Equivalent: only the order is used, and 4 is still above inferred's 2. |
| `trees.py` | 53 | `_busy`'s last `return False` to `None` | Equivalent: its only caller reads it as `not _busy(...)`, and `None` and `False` are both false. |

