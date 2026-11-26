# Mutation testing, batch 177

One module, run uncapped: every mutant, not a sample of 150. Batch 16 sampled 150 of its 515 mutants.
- Proving a notebook and replaying a failure: a proof's two runs, how they are compared, its verdict and receipt, proof rules, and opening, checking and saving replay bundles (`prove_service.py`).

The run was at `main` 82f8e2101 (2026-11-19), with `sidecar/tools/mutate.py` as it is since #2902. `prove_service.py` hasn't changed since batch 16's run. It used `--cap 100000 --seed 7 --kinds all`, so all 515 mutants ran (511 point, 4 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. As batch 178 below explains, each pytest session got a new, empty base folder of its own. A mutant whose run times out counts as killed. One timed out: the loop that finds a free name for an opened bundle's notebook, with its condition changed, never ends.

The module's tests take several minutes together, far over the mutator's 120-second limit for one mutant. So the "before" figures come from four runs, as in batch 95:
- All 515 mutants ran with the 8 quicker files (the first 8 in the table, about 13 seconds together). 181 survived.
- The 181 ran again with group A: test_prove and test_wizard, which run whole proofs against a fake Fabric. Group A killed 36: what Fabric's packages are remembered as, receipts exported and saved, proof rules, the list of proofs, a proof's start, its two runs and its verification, deleting its outputs, and the wizard's notebook. The container restarted after 66 of the 181 had run. The other 115 ran on their own with `--only`, from the same commit and with the same files.
- The 145 left ran with group B: test_replay, which replays bundles in the notebook's own process. Group B killed 79: opening, previewing, listing and saving bundles, a replay's check, its tables and samples, its start, its result and its end, the sandboxes it remembers, and a replay notebook's cells waiting for the replay.
- The 66 left ran with group C: test_kernel_process. Group C killed 1: a proof measures the notebook's own process when notebooks run in their own process.

Mutants killed in any of the four runs count as killed "before". The "after" run gave the 65 survivors the first set and the new file. The groups weren't needed there.

Before the "after" run, the new tests were run in a copy of `sidecar/` on their own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `prove_service.py` | test_prove_service_edges, test_local_retention_gaps, test_replay_edges, test_receipts_edges, test_notebook_cells, test_prove_service_mutation, test_state_writes, test_debugging, then groups A, B and C above (+ test_prove_service_uncapped) | 515 of 515 (511 + 4), uncapped | 450 / 65 | **87.4%** | 449/511 → 504/511 | 1/4 → 2/4 | 506 / 9 | **98.3%** | 7 equivalent, 2 not killed |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **prove_service.py**:
  - A proof asks Fabric for news every 5 seconds. A replay bundle may be up to 100 MiB, and a bundle of exactly that size isn't too large.
  - A notebook's dependencies, the receipt redactions and the list of receipts (50 by default) come from their own modules.
  - Fabric's packages are remembered under the module's name, whether Fabric reported the module or its distribution. The file is written with `indent=1` and sorted keys, in a folder made with its parents.
  - A saved receipt says what it left out. It gives up after 999 taken names, and a bundle after 99. A bundle's file name keeps 60 characters of the notebook's name, and so does the notebook made from an opened bundle.
  - Bundles in Downloads are listed newest first, 50 at most, with the number of samples each has.
  - A replay's copy that is gone names the bundle's notebook. The live replay is looked up without making a session. A notebook whose extras can't be read isn't a replay.
  - A bundle without ids never attaches a lakehouse, even when the lakehouse it names still carries one.
  - A replay's check names the bundle's lakehouse when none is attached, and compares the bundle's environment, for the modules its code cells import, with this PC's.
  - A replay seeds `random` with 0, and the sandboxes it remembers keep the lakehouse's name. A sandbox that can't be deleted names its lakehouse.
  - A saved proof keeps its PC side without the cells' outputs, and only the last 200 proofs are kept. A saved proof is found by its id. Proofs are listed newest first, 20 at most.
  - Cancelling a proof that is verifying doesn't cancel a Fabric job. Cancelling it while Fabric runs ends it cancelled.
  - Verifying a table again reads that table, from the first differing row, and keeps no limitations when the compare gave none. A proof where neither side wrote a table compares only the time.
  - A proof's id ends in 6 hex digits. It seeds `random` with 0, counts its code cells from 1, and keeps a silent cell's outputs as `[]`, not `None`. Its Fabric side is the Python engine for a notebook without Spark. Fabric's elapsed time is in whole seconds, its total and job times in hundredths, and its start time is never below 0.
  - A published notebook that comes back without an id is found by its name.
  - A record that is busy is copied up to 20 times.
  - → `test_prove_service_uncapped.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `prove_service.py` | 248 | an unreadable bundle's copy removed with `missing_ok=True` → `False` | Equivalent: the copy was written just before, so it is always there to remove (batch 16, line 258 then). |
| `prove_service.py` | 644 | `zip(cells, saved["cell_ids"], strict=True)` → `False` | Equivalent: saving a notebook gives one cell id for each cell, so the two always have the same length. |
| `prove_service.py` | 661 | the proof's thread `daemon=True` → `False` | Not killed: it changes only whether the app waits for a running proof when it exits, which no test here can see without ending its own process. |
| `prove_service.py` | 802 | `i < len(suffixes)` → `<=` (boundary) | Equivalent: the parameters bar gives a suffix for every cell, so `i` is always below the length. |
| `prove_service.py` | 806 | the first poll after a cell starts asks from version `-1` → `-0` | Equivalent: at version 0 nothing has been output yet, so both give no outputs; after it, both give them all (batch 16, line 816 then). |
| `prove_service.py` | 807 | `snap.get("outputs") or []` → `and []` on that first poll | Equivalent in the record: it changes only what the panel shows for a running cell, and the cell's final outputs come from a poll of their own (batch 16, line 817 then). |
| `prove_service.py` | 813 | the final poll asks from version `-1` → `-0` | Equivalent: a finished cell's version is past 0, so both give all its outputs. |
| `prove_service.py` | 815 | the final `snap.get("outputs") or outputs` → `and outputs` | Equivalent: the outputs seen last while the cell ran are the final poll's outputs, and when there are none both are empty (batch 16, line 825 then). |
| `prove_service.py` | 861 | `time.monotonic() - started < MAX_FABRIC_SECONDS` → `<=` (boundary) | Not killed: it differs only at the exact instant six hours have passed. |

