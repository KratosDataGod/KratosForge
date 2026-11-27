# Mutation testing, batch 429: academy/sqlprojlab.py (every mutant)

One module, run uncapped: every mutant, not a sample (`--cap 100000 --seed 7 --kinds all`). This is lane A's first mutation run of this module. It has six boundary mutants.
- The `sqlproj_lab` adapter of the Academy (`academy/sqlprojlab.py`): a learner's change set over a warehouse database project, read as text, each file checked for one object in Fabric's Git layout, built in an in-memory DuckDB with no file or network access, and graded by `project_builds`, `objects_present`, `deploy_safe` and `result_matches`.

It has 226 mutants, and this run ran all 226 of 226, uncapped.

The run was at `main` c3f3393d5a (2026-11-24), with the method of batch 379. Each pytest session had its own fresh `--basetemp`, removed at the end of the session; the nesting-check log stayed empty for the run and both re-runs. The kills don't end in a streak of near-identical times. Three kills chosen at random (mutants 1, 124 and 211) were re-run by hand without `-x`: each failed a real test (1, 1 and 1). Two mutants timed out (120 s), and each counts as killed: mutants 52 and 97, which turn the loop that builds views in dependency order into one that never ends when a view can't build. **Rechecked on 2026-11-24:** on a machine this loaded a mutant can time out without hanging, so each was applied again, alone, to `sidecar/` as it was at the batch's commit and the set run with a 1,500-second limit instead of 120. Both really never end: the test suite's own 300-second limit for one test stopped each, with the stack in `build`'s loop. The numbers don't change.

Everything in the module runs in the test's own process on an in-memory DuckDB, so no learner process starts and nothing makes a network call. The set is test_academy_sqlprojlab, which runs in about 5 seconds (with the new file, about 12 seconds under load).

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/sqlprojlab.py` | test_academy_sqlprojlab; after: + test_academy_sqlprojlab_mutation | 226 of 226 (220 + 6), uncapped | 174 / 52 | **77.0%** | 174 / 220 (79.1%) → 219 / 220 (99.5%) | 0 / 6 → 6 / 6 | 225 / 1 | **99.6%** | 1 equivalent |

The 52 survivors were re-run with the new file added (`--only`): 46 were killed and 6 were left. Five of the 6 were not equivalent: an extra schema made from each object's name (206), a schema name with a double quote quoted wrongly (202, 203 and 214), and the reason a table would be rebuilt replaced by the general one (211). Tests were added for them and the 6 were re-run with the file: those 5 were killed. The 75 new tests in `tests/test_academy_sqlprojlab_mutation.py` check:
- `project_path` and `parse`: backslashes, spaces, the depth limit, a byte order mark and CRLF, blank lines trimmed, the order kept, a path twice in any mix of `file` and `delete`, the line each refusal names, and exactly 1 MB read while one byte more is refused, counted in bytes;
- `apply` up to 500 files, and `objects`: what each file defines, a file that defines nothing or more than its object, the layout compared without case, an object defined twice, a name that can't be a file, and a schema labelled by its name;
- `build`: locked settings with no file access, exactly the schemas the project names (one with a double quote too), a report row for every file, procedures and functions read but not run, a table or view that fails saying why on one line, three views built in reverse order, and the seed loaded only when the project builds;
- `_rows` up to 10,000 rows with cells the UI can show, one SELECT only, and the time limit saying it stopped;
- each check's pass and every failure in its own words: the count of other problems, a missing column or columns, names compared with case, the drops `allow_drop` allows, the exact reason a table would be rebuilt, columns compared without case, row order only when `ordered`, an unknown check, and no checks never passing;
- `check_problems`, `spec_problems` and `load_project` for each problem, and `run`'s rows: deletes, problems that name no file, and 200 rows by default with `more` when there are more.

No bugs were found.

### Left

- Mutant 168: `_rows` fetches `MAX_ROWS + 0` rows instead of `MAX_ROWS + 1`. The rows are cut to `MAX_ROWS` straight after, and nothing reads the extra one, so the result is the same. Equivalent.

Lane B scored the same module at the same time, in batch 424 (#3359). The two runs used different tests and neither saw the other's, so this page and batch 424's page each report their own run. Neither is a re-score of the other.
