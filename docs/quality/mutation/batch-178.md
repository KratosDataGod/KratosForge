# Mutation testing, batch 178

One module, run uncapped: every mutant, not a sample of 150. Batch 7 sampled 150 of its then 449 mutants.
- The local `notebookutils`: `fs` on this PC and in the attached lakehouse, credentials and Key Vault, `notebook.run`, the runtime context, Fabric item lists, `loadTable`, user data functions and the variable library (`notebookutils_local.py`).

The run was at `main` bdf2c1775 (2026-11-19), with `sidecar/tools/mutate.py` as it is since #2902. It used `--cap 100000 --seed 7 --kinds all`, so all 516 mutants ran (511 point, 5 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. `test_notebook_parity.py::test_spark_reads_files_and_groups_like_pyspark` reads `examples/`, so it was left out with `--deselect`. Two files from batch 7's set were left out to keep each mutant's run short: `test_keys.py` (16 s on its own, and it doesn't call this module) and `test_estate.py` (42 s). Four quick files that call the module were added: test_notebookutils_load_table, test_dbutils_parity, test_notebook_resources and test_notebook_parity.

No mutant's run timed out.

### Why this run was done again

The first uncapped run of this module gave false kills, so it was thrown away and its numbers are not used here. Each mutant's tests ran with one fixed `--basetemp` folder for the lane. pytest deletes that folder at the start of every session. Mutant 58 drops `_same_or_inside`'s answer for local paths (line 130). With it, `test_local_mv_into_itself` copies a folder into itself until the path is too deep. From then on, deleting the folder raised `RecursionError` at the start of every session, so every later mutant failed before any test ran and counted as killed. That run's timings show it: from mutant 59 on, every mutant was "killed" in about 10.5 s. The run here gave each pytest session a new, empty base folder of its own. A small plugin outside the committed files did this (`pytest_configure` sets `basetemp` to a fresh `tempfile.mkdtemp` folder). The broken run had 4 survivors. This run has 49.

Before the "after" run, the new tests were run in a copy of `sidecar/` on their own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `notebookutils_local.py` | test_notebookutils_local_mutation, test_notebookutils_local_edges, test_notebookutils_fs, test_notebookutils_helpers, test_notebookutils_parity, test_notebookutils_load_table, test_paging_complete, test_contract_onelake, test_dbutils, test_dbutils_parity, test_notebook_resources, test_notebook_parity (+ test_notebookutils_local_uncapped) | 516 of 516 (511 + 5), uncapped | 467 / 49 | **90.5%** | 466/511 → 508/511 | 1/5 → 5/5 | 513 / 3 | **99.4%** | 3 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **notebookutils_local.py**:
  - `loadTable`: it returns True. An append keeps the table's schema and an overwrite replaces it. A folder's files are joined by name, with missing columns allowed. It needs an attached lakehouse, a table name that isn't blank and a known `pathType`. An empty folder says it has no files. Files of another format are named, five at most, then "and more".
  - `FileInfo` has a change time of 0 unless one is given. A local path and a OneLake path are never the same path (`False`, not just falsy).
  - `Files/` itself can't be changed, and nothing is sent.
  - `head` of a local file reads only its first bytes. `head` of 1 byte gives 1 byte. `head` of 0 bytes reads nothing, also from OneLake.
  - A folder named `Files` deeper down is looked up in its parent. Only the lakehouse's own `Files` and `Tables` are answered without a listing.
  - `put` without overwrite keeps what is there. `cp` of a folder needs `recurse`. `mv` onto something that is there needs `overwrite`. `mv` into itself is refused in `mv`'s own words.
  - A OneLake listing refused with 403 is raised as it is, without a second lookup. `rm` asks OneLake for one entry to see that a folder isn't empty.
  - An unknown `notebookutils` name is an `AttributeError`.
  - A Key Vault link whose port isn't a number isn't on the vault. A Key Vault 400 is an error with its code. `getSecret` asks once, with a 30-second limit and API version 7.4.
  - A token is valid until the second it expires, and payloads of every length get their padding back.
  - `notebook.run` gives 90 seconds by default, and passes a cell timeout only when one is set.
  - The notebook, lakehouse and table lists give 1,000 items by default. The default lakehouse is looked up in the workspace given.
  - The runtime context equals its copy. It isn't a pipeline run or a reference run, and its user name comes from `USERNAME` when `USER` isn't set.
  - A variable reference reads the library it names.
  - → `test_notebookutils_local_uncapped.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `notebookutils_local.py` | 322 | `mv`'s `create_path` default `False` → `True` | Equivalent: `mv` never reads `create_path`. It is there only so Fabric's signature works. |
| `notebookutils_local.py` | 232 | a local folder's size in `_entry` 0 → 1 | Equivalent: nothing reads a folder's size there. `mv` compares a folder's files, not its size. |
| `notebookutils_local.py` | 236 | the size of the `Files` or `Tables` root entry 0 → 1 | Equivalent, as batch 7 found: nothing reads that size, and moving `Files` itself is refused. |

