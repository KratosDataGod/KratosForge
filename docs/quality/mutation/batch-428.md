# Mutation testing, batch 428: academy/notebookutils_lab.py (every mutant)

One module, run uncapped: every mutant, not a sample (`--cap 100000 --seed 7 --kinds all`). This is lane A's first mutation run of this module. It has one boundary mutant.
- The `notebookutils` (and `mssparkutils`) an Academy PySpark exercise gets (`academy/notebookutils_lab.py`): splitting a Fabric `.py` notebook into cells, `notebook.run`, `exit`, `runMultiple` and `validateDAG` on the exercise's own notebooks, `fs` under the exercise's `Files/`, `runtime.context`, and what needs Fabric saying so.

It has 143 mutants, and this run ran all 143 of 143, uncapped.

The run was at `main` 9c7201ae45 (2026-11-24), with the method of batch 379. Each pytest session had its own fresh `--basetemp`, removed at the end of the session; the nesting-check log stayed empty for the run and both re-runs. The kills don't end in a streak of near-identical times. Three kills chosen at random (mutants 5, 21 and 118) were re-run by hand without `-x`: each failed real tests (7, 1 and 13). No mutant timed out.

The module runs in the learner process, copied into the generated `kf_learner` package. The set's tests start that process (on Linux, its own session with resource limits, as on CI), so they test the mutated copy as a learner gets it; the new tests build the same `Utils` in the test's own process on an exercise folder in `tmp_path`, so no learner process starts for them. Nothing in either makes a network call. The set is test_academy_pyspark_streaming without its 11 stream, checkpoint, asset and bundled-exercise tests, which test pyspark_stream and the assets, not this module. It runs in about 16 seconds. test_academy_pyspark_lab (138 s) was left out for time: it reaches this module only through `pyspark_shim.namespace` (batch 430).

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/notebookutils_lab.py` | test_academy_pyspark_streaming (11 deselected); after: + test_academy_notebookutils_lab_mutation | 143 of 143 (142 + 1), uncapped | 62 / 81 | **43.4%** | 62 / 142 (43.7%) → 140 / 142 (98.6%) | 0 / 1 → 1 / 1 | 141 / 2 | **98.6%** | 2 equivalent |

The 81 survivors were re-run with the new file added (`--only`): 78 were killed and 3 were left. One of the 3, mutant 128 (`copytree(..., symlinks=False)`), was not equivalent: it made copying a folder read through a link inside it to a file outside the exercise. A test was added for it and mutant 128 was re-run alone with the file: killed. The new tests in `tests/test_academy_notebookutils_lab_mutation.py` check:
- `cells`: each cell's first line, the parameters cell, empty cells, and the marker's exact form;
- `run`: arguments set after the parameters cell (or first, when there is none), the exit value as a string and None as None, a failing cell naming the notebook and its line, a notebook name like a variable of up to 63 characters that must be there, another workspace and bad argument names refused, ten notebooks deep as the limit, and the context naming the innermost running notebook;
- `runMultiple` and `validateDAG`, what needs Fabric saying so for each part, the help text, and `install` giving `notebookutils` and `mssparkutils`;
- `fs`: put, append, head (64 KiB by default), mkdirs, ls with Fabric's fields, rm, cp and mv, with their refusals and the folders they make, the lakehouse prefixes and backslashes it accepts, everything outside `Files/` it refuses, links on the way refused, and a link inside a copied folder copied as a link, never read through.

No bugs were found.

### Left

- Mutants 60 and 124: `run`'s default `timeout_seconds` 91 instead of 90, and `runMultiple` passing 1 instead of 0. `run` doesn't read its timeout: the exercise's own time limit is the limit. Equivalent.

Lane B scored the same module at the same time, in batch 420 (#3359). The two runs used different tests and neither saw the other's, so this page and batch 420's page each report their own run. Neither is a re-score of the other.
