# Mutation testing, batch 160

A boundary re-score of five modules first scored in batches 1, 3, 4, 7 and 13, before `mutate.py` made boundary mutants (batch 36), run again with `--kinds boundary` only:
- Local data projects: threads, timeouts and a project's name at their limits (`localdata/projects.py`, batch 3).
- Connection profiles: a key's length in a message, a hybrid's platforms and a remote budget of 0 (`connprofiles.py`, batch 13).
- Notebook versions: two versions kept in the same instant (`notebook_versions.py`, batch 4).
- The OneLake write queue: a cell's wait and the memory cap (`write_queue.py`, batch 1).
- notebookutils on this PC (`notebookutils_local.py`, batch 7): scored only, with no new tests in this batch.

The run was at `main` 4ddd3662 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902 (sample cap 150, seed 7, `--kinds boundary`), with the test files of those batches, given as paths relative to `sidecar/`. Each module has fewer than 150 boundary mutants, so all of them ran. No test needed `--deselect`, and the tests first passed once on the unmutated copy.

The new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, on DuckDB 1.5.6 and 2.0.0. The memory cap test runs the write in a thread and waits at most 3 s for it, so a mutant that would wait forever fails instead of hanging. No mutant's run timed out.

## Results

| Module | Test files run | Boundary mutants | Before: killed / survived | Before | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `localdata/projects.py` | test_local_data_projects_edges, test_corrupt_state_reads, test_file_replace, test_localdata_projects_mutation (+ test_localdata_projects_boundary) | 5 of 5 | 3 / 2 | **60.0%** | 5 / 0 | **100%** | none |
| `connprofiles.py` | test_connprofiles, test_connprofiles_edges, test_connprofiles_key_text, test_connprofiles_shape, test_databricks_host, test_databricks_profile_host, test_engine_connections_settings, test_engines_service, test_engines_service_edges, test_secret_detectors_userinfo, test_connprofiles_mutation (+ test_connprofiles_boundary) | 5 of 5 | 4 / 1 | **80.0%** | 5 / 0 | **100%** | none |
| `notebook_versions.py` | test_notebook_versions, test_notebook_versions_service_edges, test_notebook_versions_mutation (+ test_notebook_versions_boundary) | 5 of 5 | 4 / 1 | **80.0%** | 5 / 0 | **100%** | none |
| `write_queue.py` | test_write_queue_edges, test_onelake_writes, test_write_queue_mutation (+ test_write_queue_boundary) | 4 of 4 | 1 / 3 | **25.0%** | 3 / 1 | **75.0%** | 1 equivalent |
| `notebookutils_local.py` | test_notebookutils_fs, test_notebookutils_helpers, test_notebookutils_local_edges, test_notebookutils_parity, test_dbutils, test_keys, test_paging_complete, test_contract_onelake, test_notebookutils_local_mutation | 5 of 5 | 1 / 4 | **20.0%** | not done in this batch | **20.0%** | 4 not done |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **localdata/projects.py**: 256 threads are allowed, and a project name of exactly 100 characters. → `test_localdata_projects_boundary.py`.
- **connprofiles.py**: a remote budget of 0 minutes a day is allowed. → `test_connprofiles_boundary.py`.
- **notebook_versions.py**: a checkpoint kept in the same instant as a save is moved 1 ms after it, so it stays newest. → `test_notebook_versions_boundary.py`.
- **write_queue.py**: a wait of exactly 5 ms is reported; a write that fills the memory cap exactly doesn't wait. → `test_write_queue_boundary.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `write_queue.py` | 197 | `b > end` to `>=` in `upload` | Equivalent: a span that ends where the last one ended adds `b - max(a, end)`, which is 0. |
| `notebookutils_local.py` | 261 | `maxBytes <= 0` to `<` in `fs.head` | Not done in this batch. |
| `notebookutils_local.py` | 470 | `status_code >= 400` to `>` for a Key Vault reply | Not done in this batch. |
| `notebookutils_local.py` | 480 | `exp > time.time()` to `>=` in `isValidToken` | Not done in this batch. |
| `notebookutils_local.py` | 784 | `len(other) > 5` to `>=` for the files named in a load error | Not done in this batch. |

