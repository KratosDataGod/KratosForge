# Mutation testing, batch 74

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- What Windows' application control refuses to load, and the line an error gets when it does (`app_control.py`).
- The Warehouse page's Monitoring tab: finding the monitoring database, a view, and building the monitoring querysets (`warehouse_monitoring_service.py`).
- Set up for VS Code: the files it plans and writes into a repository (`vscode_setup.py`).

The run was at `main` 238a53d35 (2026-11-17), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all were run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used, and no test in these sets reads `docs/`, `examples/` or `app/`. `test_vscode_setup.py` also reaches `vscode_setup.py` but reads `docs/`, so it was left out, which can only lower that module's "before" score.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `app_control.py` | test_app_control, test_app_control_policy, test_windows_folders_and_app_control_edges (+ test_app_control_mutation) | 73 of 73 (70 + 3) | 65 / 8 | **89.0%** | 63/70 → 70/70 | 2/3 → 3/3 | 73 / 0 | **100%** | none |
| `warehouse_monitoring_service.py` | test_warehouse_monitoring, test_warehouse_monitoring_build_again (+ test_warehouse_monitoring_service_mutation) | 64 of 64 (63 + 1) | 49 / 15 | **76.6%** | 49/63 → 63/63 | 0/1 → 1/1 | 64 / 0 | **100%** | none |
| `vscode_setup.py` | test_vscode_setup_edges (+ test_vscode_setup_mutation) | 33 of 33 (33 + 0) | 12 / 21 | **36.4%** | 12/33 → 32/33 | none | 32 / 1 | **97.0%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **app_control.py**:
  - The file is mapped with the flag that skips its dependencies (1), and the kernel32 handle keeps the last error.
  - The scan checks at most 5,000 binaries.
  - The 20th error in a chain is read and the 21st is not, and an error raised while handling another is followed to the reason.
  - An import names the package without its leading underscore.
  - An engine inside the folder the engine_folder policy names is not the person's own.
  - → `test_app_control_mutation.py`.
- **warehouse_monitoring_service.py**:
  - A queryset is the app's only when its description starts and ends as the app's does.
  - At most 10 databases are tried, names ending in `_Database` first, then by name.
  - The first probe row gives when and how many; no rows is monitored with nothing seen.
  - The views are listed once monitoring is found, not before, and at most three problems are reported.
  - A view with nothing recorded yet is empty and not cut short; one that fails for another reason says so.
  - 100 warehouses are built and 101 refused; a created queryset keeps the id Fabric gave it, or an empty one when Fabric gives none.
  - A remembered workspace is added to the ones already there.
  - → `test_warehouse_monitoring_service_mutation.py`.
- **vscode_setup.py**:
  - JSON files are indented two spaces and end with a line break, and each file has its text.
  - The problem matcher reads each group of a finding, and the Problems panel is cleared.
  - A plan tells new, the same and already there apart, a folder where a file would go included, and counts the new ones.
  - Only a list of the listed files is accepted.
  - Files are written into folders that don't exist yet or already do.
  - → `test_vscode_setup_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `vscode_setup.py` | 125 | `state == "exists" and path.is_file()` to `or` | Equivalent: a path that isn't there isn't a file, so `or` gives the same answer for a new file; for a folder that is there, reading it fails, the error is caught and the state stays "exists", as it does when the read is skipped. |

