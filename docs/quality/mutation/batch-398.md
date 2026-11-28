# Mutation testing, batch 398

One module, scored for the first time in mutation lane A. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- The academy's Python and Delta exercises: the learner's script run in the learner process, then checks of what it left in its workspace (`academy/pylab.py`).

The run and the "after" run were both at `main` 9c7201ae4 (2026-11-24), with `sidecar/tools/mutate.py` as it is since #2902. The module has 75 mutants, and the run ran all 75 of 75 (uncapped). It has no boundary mutants. Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is test_academy_pylab, test_academy_limits and test_academy_sandbox, which start real learner processes. test_academy_databricks_content also reaches the module, but it takes about 2.5 minutes, so it was left out; the score counts only the files given. The new tests stub the learner process (`sandbox.run`) where they grade, so they start no script.

The machine was shared and loaded during the run (load average about 8), and the set took close to `mutate.py`'s 120 second limit. The run reported 61 killed, 1 survived and 13 timed out. A timed-out mutant counts as killed by the score's rule, but here that would be wrong: each of the 13 was run again by hand, without `-x` and without a limit. Twelve passed every test (they took 107 to 137 seconds), so they are counted as survivors below. One (line 40) failed 2 tests and is counted as killed. The one slow kill, at 111 seconds, was also run again by hand and failed a real test. So the honest "before" is 62 killed and 13 survived. Memory ran short on the machine during these runs; no result above came from a run that was stopped for it.

By the "after" run the unmutated set itself took longer than 120 seconds, so the "after" run used the new tests, test_academy_pylab and test_academy_limits. A mutant that fails some of the set's tests fails the whole set, so its kills hold for the full set too. It killed all 13.

Each pytest session got a fresh temporary folder of its own, as in batch 331. As in batch 387, a check used only for these runs refused any connection to anything but this machine; it wrote down none. After the run no folder in it was nested deeper than 40 levels. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (3, 6 and 6).

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/pylab.py` | test_academy_pylab, test_academy_limits, test_academy_sandbox (+ test_academy_pylab_mutation) | 75 of 75 (75 + 0), uncapped | 62 / 13 (the run said 61 killed, 13 timed out, 1 survived; see above) | **82.7%** | 62/75 → 75/75 | none | 75 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy/pylab.py**:
  - A check it doesn't know fails in words.
  - A table file that isn't there, or is outside the workspace, fails with the workspace's words. One DuckDB can't read says why in 200 characters.
  - The table is named from the file: 40 characters at most, other characters as `_`, and `t_` before a leading digit.
  - A config check gives the validator's answer.
  - An error is named by its last line, cut at 300 characters.
  - Grading shows the last 20000 characters of the script's output and errors.
  - Only exercises without Delta may run in an AppContainer.
  - → `test_academy_pylab_mutation.py`.

### Left

None.

Lane B scored the same module at the same time, in batch 415 (#3359). The two runs used different tests and neither saw the other's, so this page and batch 415's page each report their own run. Neither is a re-score of the other.
