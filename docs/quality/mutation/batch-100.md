# Mutation testing, batch 100

Two modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Running the person's own Git: the time limits, the environment each call gets (SSH batch mode, optional locks for read-only calls), what is sent on standard input, and the plain message for an error none of the known causes match (`git/cli.py`).
- One Git repository on this PC: branch name limits, the status count and summary, which remote the provider links use, commit rows and bodies, renames, stashes, the discard and secrets questions, the file size limit, notebook and untracked diffs, conflict views and the diff line limit (`git/repo.py`).

The run was at `main` 4ddd36620 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902: each mutant ran without a bytecode cache, and the tests were first run once on the unmutated copy of `sidecar/`, where they passed. The method is the same as earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. `git/cli.py` has 50 mutants, so all of them ran; `git/repo.py` has 639, so 150 were sampled.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used and nothing was deselected. The `git/repo.py` tests run real Git on temporary repositories with a Git configuration of their own; nothing reaches a remote. `test_release.py` and `test_pipeline_runs.py` also call `git/repo.py`; they were left out of its set to keep each mutant's run short.

One `git/repo.py` mutant timed out, before and after: `i += 1` to `i += 0` on line 179, which never leaves the status loop. It counts as killed.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `git/cli.py` | test_git_cli_and_scopes_edges, test_git, test_programs_from_path_only, test_git_ops_edges, test_git_service_edges (+ test_git_cli_mutation) | 50 of 50 (50 + 0) | 36 / 14 | **72.0%** | 36/50 → 49/50 | none | 49 / 1 | **98.0%** | 1 equivalent |
| `git/repo.py` | test_git, test_git_repo_edges, test_git_hunks, test_git_service_edges, test_git_service_more_edges, test_git_service_mutation, test_corrupt_state_reads, test_notebook_comments (+ test_git_repo_mutation) | 150 of 639 (148 + 2) | 110 / 40 | **73.3%** | 110/148 → 143/148 | 0/2 → 2/2 | 145 / 5 | **96.7%** | 5 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **git/cli.py**:
  - A call waits 60 s and a network call 1800 s; asking the version waits 10 s with no input.
  - A run gets the app's environment and the default time limit, and only a read-only run skips Git's optional locks.
  - Input is sent in place of an empty standard input.
  - The person's own `GIT_SSH_COMMAND` or `GIT_SSH` is kept; with neither set, SSH runs in batch mode.
  - An error none of the known causes match is given in Git's own words with a capital letter, and one with nothing after `fatal:` reads "Git couldn't do that."
  - → `test_git_cli_mutation.py`.
- **git/repo.py**:
  - A branch name of 244 characters is allowed and 245 is not; one starting with `-` is refused before Git sees it.
  - The change count adds staged, unstaged, untracked and conflicted files.
  - The summary for a detached head, a branch in step, ahead or behind, and before the first commit, in the singular and plural.
  - Provider links use the branch's own remote and that remote's default branch, else `origin`.
  - A commit needs an email as well as a name, and that is asked before the staged files are scanned.
  - A commit's body is kept apart from its subject, and each field of a commit row stays in its place.
  - The branch you are on can't be deleted, even when confirmed.
  - A file at a commit is cut only beyond the size limit (5,000,000 bytes).
  - A missing or binary side has no conflict view; a conflict view starts at line 1 with no old side, and two blocks are counted in the plural.
  - A staged rename to a name with spaces keeps the whole name, and a commit that renames one file and adds another lists both.
  - A stash is counted and listed with its date and message.
  - The discard question and the secrets message count their files in the singular and plural.
  - History says there is more only beyond the limit.
  - A staged notebook is compared with the last commit, an untracked text file shows every line as added, and a notebook cell ends with one newline.
  - A diff is cut only past the line limit.
  - → `test_git_repo_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `git/cli.py` | 53 | `m.group(1)` to `m.group(0)` | Equivalent: the pattern is one group and nothing else, so both are the same text. |
| `git/repo.py` | 147, 355, 378 | `read_only=True` to `False` | Equivalent here: it only sets `GIT_OPTIONAL_LOCKS=0`, which changes no output. |
| `git/repo.py` | 428 | `parts[:8]` to `parts[:9]` | Equivalent: `_commit_row` reads only the first eight fields. |
| `git/repo.py` | 681 | `if f[5]` to `if f[6]` | Equivalent: an empty parents field splits to an empty list either way. |
