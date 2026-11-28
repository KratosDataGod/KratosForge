# Mutation testing, batch 393

One module, run uncapped: every mutant, not a sample. This is lane A's first mutation run of this module.
- Academy exercise workspaces: reading what a learner made as untrusted input, with paths kept inside, links refused and files size-capped (`academy/workspace.py`, ACD-004).

The run was at `main` e49cddbeb (2026-11-24), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 136 of 136 mutants ran (129 point + 7 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Each pytest session got a new temporary folder of its own, removed when the session ended (a small plugin, not committed). Nothing here runs the exercise sandbox: the module only reads files.

The set is test_academy_validators and test_academy_configlab, about 8 s a session. The lab and sandbox files that also go through it were left out as too slow for a run per mutant (test_academy_sandbox 90 s, test_academy_databricks_content over 3 minutes, test_academy_pylab and test_academy_lab_python about 20 s each). The score counts only the files given. Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed every way. No `-k` filter was used.

One mutant timed out in both runs and counts as killed: mutant 89 removes the "isn't a file" refusal in `load_table`, so DuckDB opens the named pipe that test_load_table_needs_a_regular_file makes, and waits for a writer that never comes. Run by hand on its own, that one test was still waiting after 300 s, while the unmutated module passes it in about a second.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `academy/workspace.py` | test_academy_validators, test_academy_configlab (+ test_academy_workspace_mutation) | 136 of 136 (129 + 7) | 89 / 47 (1 of the killed timed out) | **65.4%** | 87/129 → 128/129 | 2/7 → 7/7 | 135 / 1 (1 timed out) | **99.3%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **academy/workspace.py**:
  - The limits: 256 MB a data file, 4 MB a settings document, 20,000 files and 5,000,000 rows. Each comparison at its value with a small stand-in: a file of exactly the limit is read and one byte more refused, with the limit said in MB; as many files as allowed are listed and one more refused; a table of exactly max_rows loads.
  - Links: a reparse point is a link only with the name-surrogate bit (a cloud file in place isn't). Off Windows the link count comes from lstat without opening the file. With `_WINDOWS` set, the count is read through a read-only handle, a file swapped between the two looks counts as linked (2), and a file that can't be opened is refused.
  - Names: a workspace reached through a linked pack folder is refused; a name that isn't text, is empty, holds NUL or is over 400 characters is refused, and 400 is fine; "./" and "a/../x" are refused even when the path needn't exist.
  - Each refusal's words: a missing workspace (start again), a path through a file, a folder read as a file, a file listed as a folder, bad JSON with its line, a symbolic or hard link inside a listed folder, and a folder with no .parquet files. A folder of parquet files loads.
  - → `test_academy_workspace_mutation.py`.

### Survivor judged equivalent

- `getattr(st, "st_reparse_tag", 1)` (line 46): the default is used only where stat results carry no reparse tag (off Windows), and `1 & 0x20000000` is 0, as `0 & 0x20000000` is.

Lane B scored the same module at the same time, in batch 413 (#3359). The two runs used different tests and neither saw the other's, so this page and batch 413's page each report their own run. Neither is a re-score of the other.
