# Mutation testing, batch 432: academy/lab.py (every mutant)

One module, run uncapped: every mutant, not a sample (`--cap 100000 --seed 7 --kinds all`). This is lane A's first mutation run of this module. It has six boundary mutants.
- The Academy exercise workspace on this PC (`academy/lab.py`): start, state, save, run, validate, hints, the explanation and reset for SQL, Databricks SQL, Python and Delta, settings, quiz, Git and database-project exercises, every file through the checked workspace paths, and the help a pass had kept across a reset.

It has 223 mutants, and this run ran all 223 of 223, uncapped.

The run was at `main` e49cddbebe (2026-11-24), with the method of batch 379. Each pytest session had its own fresh `--basetemp`, removed at the end of the session; the nesting-check log stayed empty for the run and all three re-runs. The kills don't end in a streak of near-identical times. Three kills chosen at random (mutants 11, 98 and 151) were re-run by hand without `-x`: each failed real tests (14, 1 and 1). No mutant timed out.

Python and Delta exercises run the learner's script in the learner process (academy/sandbox.py), Git exercises replay a session with real Git, and Databricks SQL exercises are checked by academy/dbsql_lab.py. The new tests replace each of those with a stand-in that records what this module hands it (`sandbox.run`, `pylab.prepare` and `pylab.grade`, `gitlab.run` and `gitlab.grade` with `git_ops.remove_clone`, and `dbsql_lab.check_all`), so no learner process or Git starts; SQL, settings, quiz and database-project exercises run for real in the test's own process. Nothing makes a network call, so no module had to be skipped. The set is test_academy_lab, test_academy_configlab and test_academy_sqlprojlab, which run in about 15 seconds. Left out for time, since each would take the set well past 20 seconds and tests its own adapter more than this module: test_academy_gitlab (31 s, real Git), test_academy_quizlab (28 s), test_academy_lab_python (23 s, the learner process), test_academy_limits (18 s) and test_academy_dbsql_lab (18 s). With the new file the set runs in about 25 seconds under load.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/lab.py` | test_academy_lab, test_academy_configlab, test_academy_sqlprojlab; after: + test_academy_lab_mutation | 223 of 223 (217 + 6), uncapped | 133 / 90 | **59.6%** | 132 / 217 (60.8%) → 215 / 217 (99.1%) | 1 / 6 → 6 / 6 | 221 / 2 | **99.1%** | 2 equivalent |

The 90 survivors were re-run with the new file added (`--only`): 74 were killed and 16 were left. Fourteen of the 16 were not equivalent: Run's `ms` worked out wrong for a settings document, a database project, SQL or a Git session (subtracted the other way, divided, or multiplied by 1001), a write whose temporary file was already gone raising the wrong error, and a check folder the grader had already removed failing the check. Tests were added for them and the 16 were re-run with the file: 10 were killed. The 4 left of the 14 multiplied by 1001 instead of 1000, which a run of 250 ms can't show once rounded; the test's clock was made to read 2 seconds, and those 4 were re-run: killed. The 76 new tests in `tests/test_academy_lab_mutation.py` check:
- the file and folder names, the 1 MB limit, which adapters have a workspace and which keep their own starter as the answer, the refusals for an exercise that has no SQL lab or no workspace, and `_exists`, `_state`, `_write` (CRLF kept, a failed write leaving no temporary file, a temporary file already gone), the state's indented JSON and `_fresh_dir` (emptied, a link refused);
- `_with_messages` paired by position, else by kind, and the workspace database: its write-ahead log, `_connect`'s settings in order with and without a time zone, and `_fresh` running the setup before it locks;
- `start` for every kind (what each writes, an existing answer kept, a second start changing nothing, a Python exercise's `run/` prepared with its starter), `state` before and after it, and `save` up to exactly 1 MB, counted in bytes;
- `run` for every kind: a quiz has nothing to run, the limit kept between 1 and 1,000 (200 by default) for settings, database projects, SQL and Git, `more`, cells the screen can show, the time limit saying it stopped, Databricks SQL translated and run in UTC, the milliseconds it took, a Python script run in `run/` with the last 20,000 characters of its output and its own `run/` prepared again when gone, and a Git session in a fresh `run/` that is removed after, with what an earlier run left cleared first and a locked folder only logged;
- `validate` for every kind with its messages and the last result kept, the Python grader in a fresh `check/` removed even when grading fails, the arguments each adapter is handed, the time limit failing a SQL check, and for a quiz the picks of a failed check kept and a change after it remembered;
- hints (clamped, the highest kept, none for a quiz), the explanation (counted only before a pass, with a quiz's questions and an empty remediation list), and reset keeping only the help that was used, for a workspace never started and with a state it can't read.

No bugs were found.

### Left

- Mutant 189: `hint` keeps `max(int(st.get("hints_used") or 1), level)` instead of `or 0`. `level` is never below 1 by then, so the maximum is the same. Equivalent.
- Mutant 215: `_with_messages` zips the results with the checks with `strict=True` instead of `False`. The zip runs only after their lengths were found equal, so it never raises. Equivalent.

Lane B scored the same module at the same time, in batch 418 (#3359). The two runs used different tests and neither saw the other's, so this page and batch 418's page each report their own run. Neither is a re-score of the other.
