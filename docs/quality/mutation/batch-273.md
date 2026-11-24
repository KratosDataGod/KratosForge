# Mutation testing, batch 273

One module not covered before, run with `--kinds all`:
- Linking a repository to Fabric workspaces: what Fabric says a workspace follows, the link file, the three commits side by side, and Get Status in the app's words (`fabric_git.py`).

The run was at `main` ea988e99 (2026-11-21), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6, with each test set run with `-x`. The module has 188 mutants, so 150 were sampled. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None reads `docs/`, `examples/` or `app/`, so nothing was left out. The per-mutant timeout was raised from 120 to 600 seconds with a wrapper that sets `mutate.TIMEOUT` (the tool is unchanged). No mutant's run timed out.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `fabric_git.py` | test_fabric_git, test_fabric_git_service_mutation (+ test_fabric_git_mutation) | 150 of 188 (149 + 1) | 128 / 22 | **85.3%** | 127/149 → 147/149 | 1/1 → 1/1 | 148 / 2 | **98.7%** | 2 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **fabric_git.py**:
  - A delete character (127) makes a field empty.
  - Each connection field is cut at its own length: owner, project and host at 100, repository at 128, branch at 250, folder at 256 and the last sync time at 40. An item type is cut at 80.
  - A link file that reads, or one that is a link, exists.
  - A count Git couldn't give is unknown.
  - Uncommitted changes count every kind: staged, unstaged, untracked and conflicts.
  - With no synced commit, nothing is known here and the workspace has no commit or last sync.
  - With no local commit or branch, nothing is compared and there is no "You're on" note.
  - Items come in the order a person acts on them, whatever their names, and keep their logical id.
  - → `test_fabric_git_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `fabric_git.py` | 287 | `unchanged`'s place 5 to 6 | Equivalent: it is still the last state, and the counts are keyed by name. |
| `fabric_git.py` | 292 | the fingerprint's `sort_keys=True` to `False` | Equivalent: the only dict in it is built with its keys in a fixed order. |
