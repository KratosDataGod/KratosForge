# Mutation testing, batch 125

Two modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants together with the earlier kinds (neither module has a boundary mutant):
- Creating the platform in Fabric, or joining one: each step's detail, workspaces found or made, and the variable library (`central/setup.py`).
- The best practices check of a semantic model, run in Fabric: which runs are its checks, the risk check, findings kept on this PC, the request id and the check notebook (`model_bpa_service.py`).

The run was at `main` 5865db954 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, each was checked to exist, and the tests first passed once on the unmutated copy. Each module has fewer than 150 mutants, so all of them ran. No `-k` filter or `--deselect` was needed: none of these tests reads `docs/`, `examples/` or `app/`. Of `test_platform.py`, which takes about 30 s, only `test_platform_setup_makes_every_item_fabric_accepts` calls `central/setup.py`, so only that test was given, by its node id.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. The new tests pass with DuckDB 1.5.6 and 2.0.0.

One mutant of `model_bpa_service.py` timed out and counts as killed: with `_STARTING.acquire(blocking=True)`, a second check starting at the same time waits for ever instead of being refused.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `central/setup.py` | test_platform_setup_steps, test_landing_zones, test_platform_service_paths, test_platformtest, one test of test_platform (+ test_central_setup_mutation) | 91 of 91 (91 + 0) | 83 / 8 | **91.2%** | 83/91 → 90/91 | none | 90 / 1 | **98.9%** | 1 equivalent |
| `model_bpa_service.py` | test_model_bpa (+ test_model_bpa_service_mutation) | 103 of 103 (103 + 0) | 85 / 18 | **82.5%** | 85/103 → 103/103 | none | 103 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **central/setup.py**:
  - The LH_Landing step says "Created LH_Landing" on a first run and "Kept LH_Landing" on the next.
  - A new workspace that Fabric answers without its name takes the name asked for, in both setups.
  - The variable library's files are JSON indented by two.
  - → `test_central_setup_mutation.py`.
- **model_bpa_service.py**:
  - A model's checks are only runs of this kind for this model, read from the last 200 runs without a refresh; a running one is asked of Fabric by its job.
  - The risk check is refreshed only when asked; otherwise the kept one is read, or an unknown one.
  - An environment without LH_Meta isn't set up.
  - A missing model's message names the workspace, or says "the workspace".
  - Findings kept on this PC aren't read from OneLake again.
  - A request id is the time and four hex digits.
  - A 404, or an `ItemNotFound` or `EntityNotFound` code on its own, publishes the notebook again.
  - An acknowledgement names the workload, or else the model, and is stamped in UTC to the second.
  - The notebook's id is Fabric's answer, or else the one found by name; with neither it is an error.
  - → `test_model_bpa_service_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `central/setup.py` | 68 | a failed step's `return False` dropped to `None` | Equivalent: `step` is only read as true or false (`if not step(...)` and `step(...) and step(...)`), and `None` is false like `False`. |

