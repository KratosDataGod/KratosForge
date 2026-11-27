# Mutation testing, batch 418

One module not scored before, run with `--kinds all`:
- The learner's Academy workspace: start, save, Run, Check, hints, the explanation and Reset for every exercise kind with a workspace on this PC, with work that survives a restart and a pass recorded with the help it had (`academy/lab.py`).

The run was at `main` ea988e99 (2026-11-24), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. test_academy_exercises was left out of the test set because each of its runs takes about 875 seconds; that can only lower the "before" score. The module has 223 mutants, so a sample of 150 was run. `main` hasn't changed this module since.

The test set was test_academy_lab, test_academy_lab_python, test_academy_limits, test_academy_quizlab, test_academy_configlab, test_academy_dbsql_lab, test_academy_sqlprojlab and test_academy_gitlab.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. A Python exercise's script is never run by the new tests: the learner process is stood in for. One survivor was left by the first "after" run. It got a test and was run again; the table counts that second run.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/lab.py` | test_academy_lab, test_academy_lab_python, test_academy_limits, test_academy_quizlab, test_academy_configlab, test_academy_dbsql_lab, test_academy_sqlprojlab, test_academy_gitlab (+ test_academy_lab_mutation_b) | 150 of 223 (146 + 4) | 105 / 45 | **70.0%** | 105/146 → 144/146 | 0/4 → 4/4 | 148 / 2 | **98.7%** | 2 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy/lab.py**:
  - The exercise and grading folders are the pack's own. Nothing exists in a folder that isn't there, and a folder without a state has an empty one.
  - Only SQL exercises open a SQL lab, and only lab exercises get a workspace. The database path must exist unless the caller says otherwise. The state is written with two-space indents. A failed write leaves no temporary file and raises the error.
  - An answer of exactly 1 MiB is saved and checked, and one byte more isn't. A Git session over 1 MiB is refused by the workspace. At exactly 1 MiB, the Git lab's own smaller limit refuses it.
  - SQL Run rows stop at the limit and at 1,000, and "more" is set only when there are more. Translated SQL runs in UTC, and DuckDB SQL runs in the PC's zone.
  - Every Run reports its time in milliseconds. The config, database project and Git runs cap their limit from 1 to 1,000.
  - A Python Run returns the last 20,000 characters of its output and its time. It recreates `run/` or its script when either is gone. A Python check folder that's already gone isn't an error.
  - Results pair with checks by position only when the kinds line up. A DuckDB check carries its pass or fail sentence, and nothing when there is none.
  - A check with no hints used says 0. An exercise without hints says so.
  - The explanation after a pass doesn't mark it seen, and it carries the exercise's remediation.
  - → `test_academy_lab_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `academy/lab.py` | 324 | `zip(..., strict=False)` to `strict=True` when pairing results with checks | Equivalent: the zip runs only after the lengths compared equal (`and` short-circuits), so strict never raises. |
| `academy/lab.py` | 441 | `int(st.get("hints_used") or 0)` to `or 1` in hint | Equivalent: the result goes into `max(..., level)`, and level is at least 1. |
