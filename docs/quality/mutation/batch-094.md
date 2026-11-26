# Mutation testing, batch 94

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- What KratosForge can do with each pipeline activity type: the rules an entry must meet, the classification order and the compatibility report for a pipeline (`orchestration/registry.py`).
- Failure replay bundles: the code and its notes, the inputs and their hashes, taking ids out, writing and reading the bundle file with its name and size checks, and the replay verdicts (`replay.py`).
- A kernel session per notebook: lookups, the session cap, idle sessions, renaming, stopping, deleting, shared sessions and the Sessions list (`kernel_sessions.py`).

The run was at `main` 7be2906d5 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902: each mutant ran without a bytecode cache, and the tests were first run once on the unmutated copy of `sidecar/`, where they passed. The method is the same as earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. `replay.py` has 203 mutants, so 150 were sampled; the other two have fewer than 150 (101 and 148), so all were run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. Three tests read `docs/` and were left out with `--deselect`: `test_activity_registry.py::test_example_report_in_docs_is_current`, `test_pipeline_coverage.py::test_the_page_is_up_to_date` and `test_pipeline_coverage.py::test_the_page_is_in_the_docs_site`. No other test in these sets reads `docs/`, `examples/` or `app/`.

The new tests for `kernel_sessions.py` use stand-in kernels, so they start no engine and no process. Seven of its survivors were run again with `--only` after the last tests were added, with the same test files, and the "after" figures include that run. No mutant timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `orchestration/registry.py` | test_activity_registry, test_pipeline_coverage, test_orchestration_runner_mutation, test_pipeline_gotchas, test_pipeline_adapters_mutation (+ test_orchestration_registry_mutation) | 101 of 101 (100 + 1) | 87 / 14 | **86.1%** | 86/100 → 98/100 | 1/1 → 1/1 | 99 / 2 | **98.0%** | 2 equivalent |
| `replay.py` | test_replay, test_replay_edges, test_prove_service_mutation (+ test_replay_mutation) | 150 of 203 (144 + 6) | 125 / 25 | **83.3%** | 123/144 → 140/144 | 2/6 → 6/6 | 146 / 4 | **97.3%** | 4 equivalent |
| `kernel_sessions.py` | test_kernel_sessions, test_shared_sessions, test_engines, test_frames, test_wrangler, test_kernel_default (+ test_kernel_sessions_mutation) | 148 of 148 (145 + 3) | 109 / 39 | **73.6%** | 107/145 → 144/145 | 2/3 → 3/3 | 147 / 1 | **99.3%** | 1 equivalent |

One bug was found, in `orchestration/registry.py`: an entry whose planned classification isn't one of the known ones makes `check_entry` raise `KeyError`, instead of listing "planned classification is unknown" with the entry's other problems. `test_an_unknown_planned_classification_is_reported_not_raised` pinned it with `xfail(strict=True)`. Fixed: the marker is gone.

### What the survivors were, and the tests that now kill them

- **orchestration/registry.py**:
  - A pipeline's overall classification is the less capable of any two neighbouring classifications, in either order, and `static` for a pipeline with no activities.
  - A plan one step more capable than today's classification is accepted; a plan as capable or less capable is refused.
  - `local` or `simulated` with no adapter at all is refused.
  - A pipeline runs on this PC only when every activity is `local` or `simulated`, and the others are listed as needing Fabric capacity.
  - A row is verified only when its evidence is `fabric_tested`.
  - `StaticAdapter.execute` refuses to run, `register` returns the entry, and the registry's records can't be changed.
  - → `test_orchestration_registry_mutation.py`.
- **replay.py**:
  - Bundles are version 1, a version 1 bundle opens, and a version 2 bundle asks for an update.
  - A sample holds at most 1,000 rows, and sample files are numbered from 1.
  - A failed cell must be a code cell in range. The parameters tag and frozen are kept; a cell that hadn't run, a cell that ran out of order or ran twice, and a cell that held a secret are each noted once.
  - The error is the first error output, with the home folder replaced, unless the home folder is a single character.
  - Inputs are sorted by lakehouse and name; a file is hashed only when both its size and its modified time still match, and a table is marked created only when the notebook wrote it first and it had no version.
  - Ids are taken out of known values, OneLake addresses in either case and whole-word GUIDs, in keys and lists too, longest id first. Only the lakehouse name is kept, and a note is added only when the code changed.
  - A file that isn't a zip, has no bundle.json, holds text that isn't JSON or isn't a bundle is refused. bundle.json and each sample file may be exactly as large as the limit, and no larger.
  - Table and sample names are checked, and a sample file must be in the samples folder and in the zip.
  - A failed exclusive write leaves no file.
  - The tables to pin, the replay verdicts (with ids compared only for a bundle saved without them) and the input differences.
  - → `test_replay_mutation.py`.
- **kernel_sessions.py**:
  - Five sessions are kept by default, and when no number is set; the cap is at least 1, and the note names it.
  - A running session, the open notebook's session and a dead session are never stopped to make room.
  - `any_running` returns true or false and skips the notebook named.
  - A guest kernel runs calls as its notebook, passes `alive` through, and refuses proofs. A session counts as running when a guest's cell is running.
  - In the engine every notebook shares one kernel; in their own process each notebook gets a prepared kernel, and `put` sets which kind from the kernel.
  - Sessions aren't reaped unless Settings ask; one idle for exactly the limit is kept, and one that started running meanwhile is put back.
  - Switching, renaming, stopping, deleting, sharing, leaving and moving a guest to another host.
  - The Sessions list: a live process is polled, a stopped one isn't, and cells run count 0 when the kernel doesn't say.
  - → `test_kernel_sessions_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `orchestration/registry.py` | 52 | the rank of `unsupported` from 4 to 5 | Equivalent: it is still the highest rank, and still below the 9 given to a classification that isn't known. |
| `orchestration/registry.py` | 289 | the rank of a classification that isn't known from 9 to 10 | Equivalent: both are above every rank. |
| `replay.py` | 93 | the hash read size from `1 << 20` to `1 << 21` | Equivalent: the chunk size doesn't change the hash. |
| `replay.py` | 214 | `indent=1` to `indent=0` for bundle.json | Equivalent: only the layout of the text changes; it reads back as the same bundle. |
| `replay.py` | 217 | `missing_ok=True` to `False` when an exclusive write fails | Equivalent: the call created the file, so it is there to remove. |
| `replay.py` | 239 | the version of a bundle with none from 0 to 1 | Equivalent: 1 is the current version, so the bundle opens either way. |
| `kernel_sessions.py` | 186 | `self.in_process and new not in self._sessions` to `or` in rename | Equivalent: in the engine no session is kept under a notebook's name, so nothing moves, and when the new name has a session the check inside the loop moves nothing. |

