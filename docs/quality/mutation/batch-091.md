# Mutation testing, batch 91

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Pairing with VS Code and approving its writes: the folder check, the write question and its five-minute wait, turning the editor on and off, unpairing, interrupting and the audit entries (`editor_service.py`).
- An environment's drift: the reasons a read failed, comparing library versions, each kind of drift and the summary lines (`fabric_environment.py`).
- Syncing a folder with a Databricks workspace: which local files are listed, the size limit both ways, the paths read, the errors skipped, and the state kept after a push or a pull (`engines/databricks_workspace_sync.py`).

The run was at `main` 705d4bce8 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902: each mutant ran without a bytecode cache, and the tests were first run once on the unmutated copy of `sidecar/`, where they passed. The method is the same as earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. `editor_service.py` has 130 mutants and `fabric_environment.py` 112, so all of them ran; `engines/databricks_workspace_sync.py` has 188, so 150 were sampled. Each test set was run with `-x`, so a mutant's run stops at its first failing test.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. `test_rpc_surface.py` also calls `fabric_environment.py` but reads `app/`, so it was left out of that set; no test in these sets reads `docs/`, `examples/` or `app/`.

No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `editor_service.py` | test_editor_service_edges, test_editor_channel, test_editor_channel_edges (+ test_editor_service_mutation) | 130 of 130 (130 + 0) | 108 / 22 | **83.1%** | 108/130 → 129/130 | none | 129 / 1 | **99.2%** | 1 equivalent |
| `fabric_environment.py` | test_fabric_environment (+ test_fabric_environment_mutation) | 112 of 112 (112 + 0) | 104 / 8 | **92.9%** | 104/112 → 112/112 | none | 112 / 0 | **100%** | none |
| `engines/databricks_workspace_sync.py` | test_databricks_workspace_sync, test_databricks_workspace_sync_edges, test_databricks_sync_service (+ test_databricks_workspace_sync_mutation) | 150 of 188 (147 + 3) | 121 / 29 | **80.7%** | 121/147 → 142/147 | 0/3 → 1/3 | 143 / 7 | **95.3%** | 7 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **editor_service.py**:
  - A path is inside the folder only when it is text, the folder isn't empty and has no NUL, and a sibling folder whose name starts the same way is outside.
  - A write question's id is `wq_` and 16 hex digits, and its view gives its fields.
  - With no policy, no editor is named as allowed.
  - Syncing while off starts no channel, and syncing while on keeps the same channel.
  - Turning the editor on or off and deciding a write return what they should.
  - Turning the editor off runs on a daemon thread named `editor-off`, and it is no longer stopping afterwards.
  - Unpairing and interrupting return what they should and are audited with the window and the result.
  - A run's audit gives its seconds.
  - A lakehouse is used only when both its ids are given.
  - A write waits 300 seconds for an answer, and the message says "within 5 minutes"; an approved write is audited as such.
  - → `test_editor_service_mutation.py`.
- **fabric_environment.py**:
  - A 401 or 403 reads as an access problem, and a 404 and a 402 each have their own reason.
  - A version or a specifier that pip can't read is compared as text, and a pre-release meets a specifier.
  - Each kind of drift on its own makes the environment drifted.
  - "No drift" comes before the staged lines and before "Runtime not read" in the summary, and one library is "1 published library".
  - → `test_fabric_environment_mutation.py`.
- **engines/databricks_workspace_sync.py**:
  - Only the first 1000 entries of a folder are looked at.
  - A file of exactly 10 MB is pushed, and one byte more is skipped.
  - A file written locally gets its parent folders.
  - The workspace path read for each form of remote path.
  - A missing path (`RESOURCE_DOES_NOT_EXIST` or `404`) is skipped, and any other error is raised.
  - A missing modified time counts as 0.
  - A `.py` push loses nothing.
  - A pushed file the workspace doesn't list is remembered at time 0, and isn't overwritten.
  - A pull keeps the other files' state, and a file the second listing leaves out gets time 0.
  - Content at the pull size limit is pulled, and content one byte over is skipped with "It's over 10 MB in Databricks, so it isn't pulled."
  - → `test_databricks_workspace_sync_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `editor_service.py` | 77 | `and` to `or` in the root check | Equivalent: both conditions give the same answer for every path form, on POSIX and on Windows. |
| `engines/databricks_workspace_sync.py` | 61 | the `raise` removed for a `/Repos` path | Equivalent: the remote path pattern only accepts `/Users` and `/Shared` paths, so this line is never reached. |
| `engines/databricks_workspace_sync.py` | 131 | `follow_symlinks=False` to `True` in reading a file's size | Equivalent: links are already skipped by `is_file(follow_symlinks=False)` before the size is read. |
| `engines/databricks_workspace_sync.py` | 151 | `len(parts) > 1` to `> 0` | Equivalent: a valid remote path always has more than one part. |
| `engines/databricks_workspace_sync.py` | 151 | the default of 1 to 0 in the parts lookup | Equivalent: a valid remote path always starts with `Users` or `Shared`, so the default is never used. |
| `engines/databricks_workspace_sync.py` | 151 | `len(parts) > 1` to `>=` | Equivalent, for the same reason as the line above. |
| `engines/databricks_workspace_sync.py` | 342 | `>` to `>=` on the pull size limit | Equivalent: the limit's length isn't a multiple of 4, so no valid base64 has it, and content of that length fails to decode either way. |
| `engines/databricks_workspace_sync.py` | 347 | `is_symlink() or parent != root` to `and` | Equivalent: the local listing never includes a link, and a plain name in the folder always has the folder as its parent. |

