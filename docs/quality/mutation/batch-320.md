# Mutation testing, batch 320: workloads/platform_notify.py (every mutant)

One module, run uncapped: every mutant, not a sample of 150. Batch 144 sampled 150 of its then 167 mutants (88.0% before, 100% after).
- Telling Teams channels about platform requests, broken shortcuts and deprecated products, once each: events, routes, the dedupe ledger and the send loop (`workloads/platform_notify.py`).

The run was at `main` ea988e99e (2026-11-20), with `sidecar/tools/mutate.py` and `--cap 100000 --seed 7 --kinds all`, so all 174 of 174 mutants ran (174 point + 0 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. In both runs each pytest session got a new temporary folder of its own (a small plugin, not committed). Afterwards no folder 40 levels deep was left behind, there was no late streak of kills with the same run time, and a few killed mutants were run again by hand, each killed again.

The set is batch 144's files and the other files that send or read platform notices through the module: test_notice_retries. The score counts only the files given. No `-k` filter was used. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/platform_notify.py` | test_platform_notify, test_platform_notify_mutation, test_notify_delivery, test_notice_failures, test_notice_retries, test_deliver, test_governance_notices (+ test_platform_notify_uncapped) | 174 of 174 (174 + 0) | 172 / 2 | **98.9%** | 172/174 → 174/174 | none | 174 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **workloads/platform_notify.py**:
  - The ledger makes the folders it lives in (`mkdir(parents=True)`); every test put it in a folder that was already there.
  - A message kept for a route that failed earlier in the same send waits in the outbox and isn't counted as sent (`ok = False`). With route names unique, as `PlatformRouting.parse` makes them, the branch is reached only by a routing built in code with two routes under one name: a route that failed for an earlier notice is already in `down` when the next notice is kept, so that notice goes to the outbox before posting. The test builds such a routing.
  - → `test_platform_notify_uncapped.py`.
