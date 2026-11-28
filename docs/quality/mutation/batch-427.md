# Mutation testing, batch 427: academy/exercises.py (every mutant)

One module, run uncapped: every mutant, not a sample (`--cap 100000 --seed 7 --kinds all`). This is the first mutation run of this module. It has 10 boundary mutants.
- The Academy's exercises (`academy/exercises.py`): reading a pack's exercises, how a result is labelled and whether it completes an objective, the reference answer, grading a duckdb_lab answer, handing each adapter's self-test to its lab, and `problems()`, every rule a bundled exercise must keep.

It has 317 mutants, and this run ran all 317 of 317, uncapped.

The run was at `main` 9c7201ae45 (2026-11-24), with the method of batch 379. Each pytest session had its own fresh `--basetemp`, removed at the end of the session; the nesting-check log stayed empty for the run and the re-run. The kills don't end in a streak of near-identical times (the run ends with the boundary mutants, eight of which survived). Three kills chosen at random (mutants 41, 184 and 253) were re-run by hand without `-x`: each failed real tests (22, 1 and 1). No mutant timed out. No test starts a learner process for this module's new tests, and none makes a network call.

The set is test_academy_exercises, test_academy_readiness and test_academy_lab. test_academy_exercises alone takes over nine minutes, almost all of it in self-tests that run an exercise's reference and wrong answers in a learner process or a local Spark, so 18 of its tests were deselected for time: `test_the_starter_does_not_pass_yet` (68 s) and the self-test of each pyspark_lab exercise (C1, C2, E20, E26, E32, E42, E51, E67 to E70, 10 to 122 s each), of the git_lab ones (E06, E07), the delta_lab ones (E22, E56, E63) and E59. Those test the labs more than this module, which only hands them on; the new tests check the handing on with the labs replaced. The set runs in about 21 seconds. test_academy_databricks_content (200 s) and test_academy_limits were left out for the same reason.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/exercises.py` | test_academy_exercises (18 deselected), test_academy_readiness, test_academy_lab; after: + test_academy_exercises_mutation | 317 of 317 (307 + 10), uncapped | 255 / 62 | **80.4%** | 253 / 307 (82.4%) → 304 / 307 (99.0%) | 2 / 10 → 10 / 10 | 314 / 3 | **99.1%** | 3 equivalent |

The 62 survivors were re-run with the new file added (`--only`): 59 are now killed, 3 are left. The new tests in `tests/test_academy_exercises_mutation.py` check:
- an exercise id with no folder isn't an exercise, drafts are listed unless asked not to, and only folders named like an exercise are exercises;
- each adapter's exercise is self-tested by its own lab with its folders (each lab replaced by a stand-in, so nothing runs), and an adapter with no self-test says which can;
- `run_check`: an answer that ends the transaction or doesn't run fails its check and changes nothing; an exercise's own starter is its reference starter;
- `problems()` on a copy of the packs, one broken field at a time: version and minutes as whole numbers in range and their edges; each lab's own check rules (pipeline, config, quiz, git); a pipeline's child files, mocks and children; notebooks and files_folder only for pyspark_lab; plain file names; the Fabric and Databricks classes and adapters; a troubleshoot fault with all three parts; prerequisites, difficulty and hints; a pyspark result_matches needing a table, a duckdb_lab check taking no table or hidden data, and hidden data needing hidden.sql; a stray grading folder; and, for every lab, that exactly two planted wrong answers are enough and one isn't.

No bugs were found.

### Left

- Mutants 49 and 131 (line 59): `STRENGTH` gives LOCAL VERIFIED 3 instead of 2, or a platform's verified class 4 instead of 3. `completes` compares a class only with an objective's `best_local`, which the registry allows only as a local class (registry.py refuses any other), so every comparison comes out the same. Equivalent.
- Mutant 194 (line 126): the fallback strength for a `best_local` not in `STRENGTH` is 3 instead of 2. A missing `best_local` reads as "local_verified", which is in `STRENGTH`, and any other value is refused by the registry, so the fallback is never used. Equivalent.
