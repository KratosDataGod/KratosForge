# Mutation testing, batch 119

Two modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants together with the earlier kinds:
- A notebook cell's change to a Prod workspace waiting for its typed name: the workspaces marked Prod and remembered on this PC, the refusal, the typed name and the allowance it gives (`prod_writes_service.py`).
- Secrets in files about to be committed: how much of a finding is shown, how much of a file and a line is read, binary files and placeholders (`git/secrets.py`).

The run was at `main` 5865db954 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, each was checked to exist, and the tests first passed once on the unmutated copy. Each module has fewer than 150 mutants, so all of them ran. No `-k` filter was used. `test_redactor_characterization.py::test_the_audit_page_shows_this_table` reads `docs/`, so it was left out with `--deselect`. A mutant whose run times out counts as killed; none timed out.

Before the "after" runs, the new tests were run in a copy of `sidecar/` on its own and against each module as `ast.unparse` writes it, as in batch 11. They passed both ways.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `prod_writes_service.py` | test_prod_writes (+ test_prod_writes_mutation) | 76 of 76 (72 + 4) | 61 / 15 | **80.3%** | 61/72 → 72/72 | 0/4 → 4/4 | 76 / 0 | **100%** | none |
| `git/secrets.py` | test_git, test_git_secrets_edges, test_git_secrets_shapes, test_redactor_characterization (+ test_git_secrets_mutation) | 63 of 63 (60 + 3) | 53 / 10 | **84.1%** | 53/60 → 60/60 | 0/3 → 2/3 | 62 / 1 | **98.4%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **prod_writes_service.py**:
  - The typed name allows the next run for 120 s, a refusal can be confirmed for 600 s, and 500 Prod workspaces are remembered. The earlier tests used the names of these limits, so a changed number passed them.
  - An allowance still holds at exactly 120 s and a refusal can still be confirmed at exactly 600 s; half a second later neither can.
  - A project's keys workspace is marked Prod too.
  - New ids are added after the ones remembered, and only the newest 500 are kept.
  - Ids already remembered aren't written again.
  - Only non-empty text ids in the file count.
  - "Not marked" is `False`, with no id, no project, a project that can't be read, or environments that can't be read; the last remembers nothing.
  - → `test_prod_writes_mutation.py`.
- **git/secrets.py**:
  - A finding shows four characters of a secret longer than 12, and one of a secret of 12 or fewer.
  - A file is read up to its 2,000,000th byte, and a line up to its 20,000th character.
  - A NUL in the first 8,000 bytes makes a file binary; one just after doesn't.
  - A value made of every one of `x`, `*` and `.` is a placeholder.
  - → `test_git_secrets_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `git/secrets.py` | 78 | `len(line) > 20000` to `>=` | Equivalent: a line of exactly 20,000 characters cut to `line[:20000]` is the same line. |

