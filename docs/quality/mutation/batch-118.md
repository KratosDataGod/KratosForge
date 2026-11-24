# Mutation testing, batch 118

Two modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants together with the earlier kinds:
- Evidence records for the excellence assessment: canonical text, subjects, records and their ids, and the bundle that keeps bad records out (`excellence/evidence.py`).
- The audit trail for external AI tools: the line for each call, the rows the store keeps, Recent activity and the approvals that have a line (`integrations/audit.py`).

The run was at `main` 5865db954 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, each was checked to exist, and the tests first passed once on the unmutated copy. Each module has fewer than 150 mutants, so all of them ran. Neither module has a boundary mutant. No `-k` filter or `--deselect` was needed: none of these tests reads `docs/`, `examples/` or `app/`. `test_uninstall_script.py` also imports the integration audit, but it reads `app/`, so it was not given to the tool. A mutant whose run times out counts as killed; none timed out.

Before the "after" runs, the new tests were run in a copy of `sidecar/` on its own and against each module as `ast.unparse` writes it, as in batch 11. They passed both ways.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `excellence/evidence.py` | test_excellence, test_excellence_catalog (+ test_excellence_evidence_mutation) | 53 of 53 (53 + 0) | 49 / 4 | **92.5%** | 49/53 → 53/53 | none | 53 / 0 | **100%** | none |
| `integrations/audit.py` | test_integrations_audit, test_integrations_app, test_integrations_extensions, test_sqlite_connections_closed (+ test_integrations_audit_mutation) | 42 of 42 (42 + 0) | 33 / 9 | **78.6%** | 33/42 → 42/42 | none | 42 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **excellence/evidence.py**:
  - Canonical text keeps non-ASCII characters as they are ("Café", not `é`).
  - A subject needs an id that is non-empty text.
  - An item subject needs both its workspace and its type.
  - A record's id is "ev-" and 16 hex digits.
  - A bundle lists the ids it kept, once each.
  - → `test_excellence_evidence_mutation.py`.
- **integrations/audit.py**:
  - A call's seconds are rounded to three places, and an error message is cut to 500 characters.
  - The store keeps 50,000 rows.
  - Recent activity returns the newest lines first, 100 by default, and asks for at least 1 and at most 1,000.
  - No approvals asked is an empty set; otherwise only the approvals with a line, started or outcome, are named.
  - → `test_integrations_audit_mutation.py`.

### Left

None.

