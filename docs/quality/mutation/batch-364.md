# Mutation testing, batch 364

One module not scored before, run with `--kinds all`:
- App Studio projects on disk: the project files and their checks, secrets kept out of specs, the size and count limits (2 MB spec, 32 MB history, 64 MB zip, 500 versions), history with undo, redo, publish and restore, versions, save-as and zip import and export (`appstudio/project.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because several runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. The module has 249 mutants, so a sample of 150 was run. The container restarted during the batch; runs cut short were resumed with `--only` on the indexes not yet run, which keeps the same numbering.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0, and against the module as it is on `main` today, which has not changed it since ea988e99.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `appstudio/project.py` | test_appstudio, test_appstudio_project_edges, test_secret_detectors_userinfo (+ test_appstudio_project_mutation) | 150 of 249 (148 + 2) | 109 / 41 | **72.7%** | 107/148 → 146/148 | 2/2 → 2/2 | 148 / 2 | **98.7%** | 2 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **appstudio/project.py**:
  - The project files: format 1, UTC times, `app.json` indented by two; a `kfapp.json` with a wrong format or app id is refused; the folder name is cut to 80 characters and a device name is refused.
  - Secret keys are cut to 40 characters, at the top level and nested; `"password": false` is not a secret and `true` is; a JSON line holding a secret is reported once, and a secret on a torn line is found in the raw text.
  - Each limit, just over and just under: the 2 MB spec, the 32 MB history, the 64 MB zip and 500 versions.
  - History: the sequence numbers and kinds through change, undo, redo, publish and restore, a note cut to 199 characters and "…", the default limit of 100, and publish history holding only publishes; an invalid spec raises and saves no version.
  - Save-as cuts the name to 120 and copies only version files; export takes only version files; an import with stray files names five of them, each cut to 60.
  - → `test_appstudio_project_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `appstudio/project.py` | 387 | `and`/`or` swapped in `_member_ok` | Equivalent: the final check after it refuses the same names. |
| `appstudio/project.py` | 388 | early return changed in `_member_ok` | Equivalent: same reason. |
