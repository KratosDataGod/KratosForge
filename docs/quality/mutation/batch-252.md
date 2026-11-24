# Mutation testing, batch 252: workloads/deliver.py (every mutant)

One module, run uncapped: every mutant, not a sample of 150. Batch 135 ran all of its then 131 mutants (80.2% before, 99.2% after).
- How a notice route sends: a Teams webhook, a Power Automate flow, or email through the company mail server (`workloads/deliver.py`).

The run was at `main` ddc89182d (2026-11-20), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 136 of 136 mutants ran (130 point + 6 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. The "before" run gave pytest `--basetemp` in a folder of this run's own, and that folder was checked afterwards: no mutant left a deep folder behind that could have failed the next mutants at startup. The "after" run gave each pytest session a new temporary folder of its own (a small plugin, not committed). A few killed mutants were run again by hand with that plugin, and each was killed again.

The set is batch 135's files and the other files that send through the module: test_notice_failure_kinds, test_notice_retries, test_regex_anchors, test_platform_notify and test_platform_notify_mutation. The score counts only the files given. No `-k` filter was used. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/deliver.py` | test_deliver, test_deliver_mutation, test_incidents, test_notice_failure_kinds, test_notice_retries, test_regex_anchors, test_platform_notify, test_platform_notify_mutation (+ test_deliver_uncapped) | 136 of 136 (130 + 6) | 134 / 2 | **98.5%** | 128/130 → 129/130 | 6/6 → 6/6 | 135 / 1 | **99.3%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **workloads/deliver.py**: a port given as the text `"0"` is refused. Batch 135 counted `1 <= port` to `0 <= port` as equivalent, since a port of 0 is read as 587; that holds for the number 0 only, as `"0" or 587` is `"0"`. → `test_deliver_uncapped.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `workloads/deliver.py` | 202 | the last `raise` (any other `SMTPException`) removed | Equivalent: `SMTPException` is a subclass of `OSError`, so the `except (OSError, SMTPServerDisconnected)` clause above takes every one of them with the same message, and this clause is never reached. |

