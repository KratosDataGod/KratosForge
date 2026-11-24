# Mutation testing, batch 144

Teams messages for platform events (`workloads/platform_notify.py`): checking an event, its message, routes, and the ledger that posts each change once.

The run was at `main` 5865db95 (2026-11-18), with the method of batch 142. The module has 167 mutants, so 150 were sampled. No `-k` filter or `--deselect` was needed, and no mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/platform_notify.py` | test_platform_notify, test_notify_delivery, test_notice_failures, test_deliver, test_governance_notices (+ test_platform_notify_mutation) | 150 of 167 (150 + 0) | 132 / 18 | **88.0%** | 132/150 → 150/150 | none | 150 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **workloads/platform_notify.py**:
  - The scope line is there only when the event has a domain or an environment, and the environment is kept.
  - A decision is news again after a new wait, also when the events name their own keys.
  - The key a fix closes comes from its own key, or from its id when its key is its own.
  - The outbox and the ledger must be one file.
  - Ids and titles are kept to 200 characters and details to 500.
  - A route can't be changed once made.
  - The ledger gives a key's kind and whether it is open, and marks and forgets a key outside a send's transaction.
  - A fix for a break that is closed already isn't posted.
  - A notice for a resting route is queued.
  - → `test_platform_notify_mutation.py`.

### Left

None.

