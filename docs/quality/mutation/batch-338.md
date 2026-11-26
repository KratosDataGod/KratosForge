# Mutation testing, batch 338

One module, run uncapped: every mutant, not a sample. This is the first mutation run of this module.
- Where a metadata project's files stand in Git, and the release record a promote or rollback writes (`meta/release.py`).

The run was at `main` 3424d8f33 (2026-11-23), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 103 of 103 mutants ran (103 point + 0 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Each pytest session got a new temporary folder of its own, removed when the session ended (a small plugin, not committed).

The set is test_release, test_release_cov2, test_meta_service_edges and test_snapshot_cov2, about 20 s a session. test_rollback (16 s) and test_prod_typed_actions (32 s) promote through the service but don't read this module's results, so they were left out to keep each mutant's run short. The score counts only the files given. Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed every way. No `-k` filter was used, and no mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `meta/release.py` | test_release, test_release_cov2, test_meta_service_edges, test_snapshot_cov2 (+ test_release_mutation) | 103 of 103 (103 + 0) | 79 / 24 | **76.7%** | 79/103 → 101/103 | none | 101 / 2 | **98.1%** | 2 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **meta/release.py**:
  - The status line for each state, word for word: nothing committed; a branch or no branch with the commit's 7 characters; 1 or 2 uncommitted files; 1 or 3 commits not pushed and 2 behind; no upstream.
  - A dirty tree without a count is "0 files changed".
  - A folder outside Git is `available` but no repo, and has no last commit to read; a file in a folder that isn't there reads as None.
  - More than 200 changes: 200 are listed and all are counted.
  - A file too big to read at the last commit is None (with `MAX_FILE_BYTES` lowered), and read whole under it.
  - The login name, and a receipt that is missing or isn't JSON is `False`.
  - A rollback's record: the project, both releases' receipts, the earlier release's code, and who rolled back (the given name, else the login name, with the object id); the deployment columns' `by`.
  - → `test_release_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `meta/release.py` | 209 | `ls-tree` with `read_only=False` | Equivalent: it only stops setting `GIT_OPTIONAL_LOCKS=0`, and `ls-tree` takes no lock. |
| `meta/release.py` | 230 | `not cli.git_path() or not folder.is_dir()` → `and` | Equivalent: a folder that isn't there makes `Repository` raise GitError, which is None too. |
