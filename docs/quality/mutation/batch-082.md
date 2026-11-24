# Mutation testing, batch 82

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds.
- Notebook files on this PC: where a notebook's file is, which notebook a file path names (for VS Code and kratosforge:// links), delete and restore, and a Fabric notebook saved here (`notebook_files_service.py`).
- What an Azure Databricks workspace offers: warehouses, clusters, the Unity Catalog metastore and catalogs, what to try first, and the summary for the command line (`engines/databricks_discovery.py`).
- The central lakehouse's tables and their migrations (`optimize/schema.py`).

The run was at `main` e88bc60e9 (2026-11-17), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), so every run was made with `PYTHONDONTWRITEBYTECODE=1`. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all were run. None of the three modules changed between e88bc60e9 and the `main` this branch was last brought up to.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against each module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used, and no test in these sets reads `docs/`, `examples/` or `app/` (`test_vscode_setup.py`, which reads `docs/`, was left out).

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `notebook_files_service.py` | test_audit, test_editor_channel, test_notebook_files, test_notebook_files_service_edges, test_notebook_folders, test_notebook_resources (+ test_notebook_files_service_mutation) | 89 of 89 (87 + 2) | 66 / 23 | **74.2%** | 66/87 → 86/87 | 0/2 → 2/2 | 88 / 1 | **98.9%** | 1 equivalent |
| `engines/databricks_discovery.py` | test_databricks_discovery, test_databricks_discovery_replies, test_engine_connections_settings, test_cli_tenant_commands (+ test_databricks_discovery_mutation) | 91 of 91 (89 + 2) | 70 / 21 | **76.9%** | 70/89 → 87/89 | 0/2 → 2/2 | 89 / 2 | **97.8%** | 2 equivalent |
| `optimize/schema.py` | test_access_cache, test_capacity_history, test_optimize_lakehouse_stores, test_optimize_roles, test_optimize_service, test_optimize_service_mutation, test_optimize_setup, test_optimize_shared, test_optimize_shared_edges, test_tenant_history (+ test_optimize_schema_mutation) | 57 of 57 (55 + 2) | 56 / 1 | **98.2%** | 54/55 → 55/55 | 2/2 → 2/2 | 57 / 0 | **100%** | none |

No bugs were found in these modules.

### What the survivors were, and the tests that now kill them

- **notebook_files_service.py**:
  - A path's reason for not opening starts at 260 characters and stops past 32,767, and a path that isn't a notebook gets no reason.
  - Only a symbolic link or a reparse tag with the name-surrogate bit counts as a link; a OneDrive placeholder doesn't.
  - A path that isn't text, or holds a NUL character, is never local. Off Windows, a relative path and a `//host` or `/\host` path aren't either.
  - A relative path has no name, even when the current folder is the notebooks folder.
  - A missing part, or a link, below the notebooks folder stops the walk.
  - A folder named like a notebook has no name.
  - Only a link to the same item in the same workspace, whose notebook is still here, counts as already here.
  - A copy of Fabric's version is saved where the notebook is, unless a place is given.
  - → `test_notebook_files_service_mutation.py`.
- **engines/databricks_discovery.py**:
  - A name of exactly 200 characters is kept, and one past it is cut to 200 with the `…`. An error in the summary is cut the same way at 600.
  - A cluster with auto-termination has no note, and one without has one.
  - An access mode that isn't set is named "not set".
  - After a failed sign-in, every part is not ok.
  - Running compute comes before starting, and starting before anything else.
  - The summary lists 20 catalogs and marks more with ` …`, says which clusters Databricks Connect can use, and names whatever was suggested under "Try first", or leaves the line out.
  - → `test_databricks_discovery_mutation.py`.
- **optimize/schema.py**:
  - A migration can't be changed once made (the dataclass is frozen).
  - → `test_optimize_schema_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `notebook_files_service.py` | 249 | `getattr(st, "st_reparse_tag", 0)` default to `1` | Equivalent: the default is only used where there is no reparse tag, and `1 & 0x20000000` is 0, the same as `0 & 0x20000000`. |
| `engines/databricks_discovery.py` | 168 | `rank.get(w["state"], 2)` default to `3` | Equivalent: any state other than RUNNING (0) or STARTING (1) ranks after both either way, and all such states rank the same. |
| `engines/databricks_discovery.py` | 171 | `rank.get(c["state"], 2)` default to `3` | Equivalent, as above, for clusters. |

