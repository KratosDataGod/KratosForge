# Mutation testing, batch 368

One module not scored before, run with `--kinds all`:
- The platform repository's files: records from the platform, TOML files for them, reading the repository back, comparing Git with what is deployed, exporting, and the deploy plan (`git/platform_files.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. The module has 425 mutants, and 150 were sampled. `main` hasn't changed this module since this run.

The run used test_platform_repo, test_platform_files_edges, test_platform_files_cov3, test_git_service_platform_mutation and test_regex_anchors, and scored 137 / 150 (91.3%).

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `git/platform_files.py` | test_platform_repo, test_platform_files_edges, test_platform_files_cov3, test_git_service_platform_mutation, test_regex_anchors (+ test_git_platform_files_mutation) | 150 of 425 (148 + 2) | 137 / 13 | **91.3%** | 136/148 → 148/148 | 1/2 → 2/2 | 150 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **git/platform_files.py**:
  - Only the shared landing zone lets dev and test read by default.
  - A blank line in a file's header comment is a bare `#`.
  - A long file name in a problem is cut to 200 characters.
  - A table compared with a list is one change, not field by field, and shortcut rules not deployed yet are compared field by field.
  - Export refuses to replace uncommitted files unless confirmed. It names up to 5 of them and adds "and more" from 6, and it counts the files it left unchanged.
  - A plan without a problems list, or with no admins anywhere and none ticked to remove, has no stop.
  - A new assignment step names its scope and target.
  - A deployed policy that Git also holds is never offered for removal.
  - → `test_git_platform_files_mutation.py`.

### Left

None.
