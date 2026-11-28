# Mutation testing, batch 385

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`), so the score counts the boundary mutants together with the earlier kinds. The table shows both parts.
- Entra ID security groups for a domain's roles: their names, and the scripts and CSV that make them and give them their Fabric roles (`central/entra.py`).

The run and the "after" run were both at `main` 293fb38da (2026-11-24), with `sidecar/tools/mutate.py` as it is since #2902. The module has 137 mutants, and the run ran all 137 of 137 (uncapped). It has no boundary mutants. Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is test_entra_graph, test_entra_service_mutation and test_entra_groups, about 9 seconds together. test_csv_cells also calls the module, but it takes about 80 seconds and uses it for one CSV, so it was left out; the score counts only the files given. The module calls nothing outside: every test checks text.

Each pytest session got a fresh temporary folder of its own, as in batch 331. After the run no folder in it was nested deeper than 40 levels. The run ends on a survivor, not on a streak of kills. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (11, 1 and 2).

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `central/entra.py` | test_entra_graph, test_entra_service_mutation, test_entra_groups (+ test_entra_mutation) | 137 of 137 (137 + 0), uncapped | 114 / 23 | **83.2%** | 114/137 → 137/137 | none | 137 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **central/entra.py**:
  - A role is frozen.
  - Groups are named by the domain's id unless the platform's naming says name.
  - A domain that isn't there is refused by its id. Roles the platform turned off get no groups.
  - A group name is cut at 256 characters and its mail nickname at 64.
  - A row's description uses the domain's name (its id when it has none) and gives the environment only when there is one; the row keeps its Fabric domain.
  - The scripts' header names the domain from the first row (an empty plan names none), says who made it only when someone is named, and counts "1 new group" or "2 new groups" and "0 new groups".
  - Only new groups whose names a script can't carry are left for by hand.
  - The CSV gives a mail nickname only for a group to make.
  - The role script's by-hand lines name the role each group gets (Admins, Contributors, Admin, Viewer), readers get their OneLake line, and stewards get none. Its Write-Host line names the domain role given.
  - → `test_entra_mutation.py`.

### Left

None.
