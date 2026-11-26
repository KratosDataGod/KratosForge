# Mutation testing, batch 409

One module not scored before, run with `--kinds all`:
- The Academy's engine service: certification packs, exercises, the learner's workspaces, hints and explanations, and recording a pass in Profile without ever changing the result (`academy_service.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. test_academy_appcontainer skips itself off Windows, so it ran no tests here. The module has 33 mutants, so all were run. `main` hasn't changed this module since.

The test set was test_academy_lab, test_academy_configlab, test_academy_quizlab, test_academy_sqlprojlab, test_academy_lab_python, test_academy_gitlab and test_academy_appcontainer, which reach this module through the service.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy_service.py` | test_academy_lab, test_academy_configlab, test_academy_quizlab, test_academy_sqlprojlab, test_academy_lab_python, test_academy_gitlab, test_academy_appcontainer (+ test_academy_service_mutation_b) | 33 of 33 (33 + 0) | 24 / 9 | **72.7%** | 24/33 → 31/33 | none | 31 / 2 | **93.9%** | 2 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy_service.py**:
  - An exercise built on a Learn lab shows the lab's lesson, with its prerequisites, what is expected, the remediation and its three hints.
  - Saving an answer says it was saved.
  - A pass with the reference solution is recorded, and the Profile row doesn't say the answers changed.
  - → `test_academy_service_mutation_b.py`, on E37 in a workspace under the test's own folder.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `academy_service.py` | 89 | `answers_changed` default `False` to `True` | Equivalent: `lab.validate` always returns `answers_changed`, so the default is never used. |
| `academy_service.py` | 96 | hint `level` default 1 to 0 | Equivalent: `lab.hint` raises the level to at least 1. |
