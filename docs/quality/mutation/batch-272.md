# Mutation testing, batch 272

One module not covered before, run with `--kinds all`:
- Clone, fetch, pull and push run in the background, with progress, cancel and how a run ends (`git/ops.py`).

The run was at `main` ea988e99 (2026-11-21), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6, with each test set run with `-x`. The module has 153 mutants, so 150 were sampled. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None reads `docs/`, `examples/` or `app/`, so nothing was left out. As in test_git_ops_edges.py, a small Python program stands in for git, so no network is needed. The per-mutant timeout was raised from 120 to 600 seconds with a wrapper that sets `mutate.TIMEOUT` (the tool is unchanged). No mutant's run timed out.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `git/ops.py` | test_git, test_git_ops_edges (+ test_git_ops_mutation) | 150 of 153 (149 + 1) | 120 / 30 | **80.0%** | 120/149 → 139/149 | 0/1 → 1/1 | 140 / 10 | **93.3%** | 8 equivalent, 2 Windows only |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **git/ops.py**:
  - An operation has a 12-character id and is marked as talking to a remote.
  - The page keeps the last 50 finished operations and never drops any while there are fewer. Operations are listed newest first.
  - Output with no known ending gives no message of its own.
  - A failed run tells its after step it failed, and only a conflict reads the result.
  - A clone makes its parent folders, or uses one that exists.
  - A first push goes to `origin`, or else to the first remote.
  - The run is a background (daemon) thread.
  - Seconds taken are rounded to a tenth.
  - Details are the last 40 lines, with at most 20 from standard output.
  - A run a second short of the limit counts as timed out, and the message names the limit in whole minutes. Since #3240 the limit is an awake-time timer, not elapsed time, so the test now stands in for that timer and checks the limit it is given and the message in whole minutes.
  - → `test_git_ops_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `git/ops.py` | 205, 210 | the timer's and the stdout reader's `daemon=True` to `False` | Equivalent: the timer is cancelled and the reader joined before the run ends. |
| `git/ops.py` | 215 (×3) | `read1` not used, or 4096 to 4097 | Equivalent: only how much of stderr is read at a time changes; every line is still read. |
| `git/ops.py` | 229 | the stderr tail kept at 41 lines, not 40 | Equivalent: the details are cut to the last 40 lines afterwards. |
| `git/ops.py` | 233 | the reader's join timeout 5 to 6 seconds | Equivalent: git has ended, so its output is already closed. |
| `git/ops.py` | 248 | `ok = True` to `False` on success | Equivalent: `ok` isn't read again on that path. |
| `git/ops.py` | 291 (×2) | `taskkill`'s `capture_output` and timeout | Windows only: this branch runs only where `CREATE_NEW_PROCESS_GROUP` exists. |
