# Implementation review process

How a merged or ready pull request becomes an **accepted** capability of Fabric Desktop by
KratosBI. A merge says the code is in main. Acceptance says the product does what its backlog item
promised, for the people it was built for, with evidence. They are different decisions, made at
different times, and **a merged PR is not automatically accepted**.

## When a review happens

- For every PR that claims a backlog item (PB-nnn) or an owned item tracked by one
  (FD-1xx, PR-AI-0xx, SQL-00x, GOV-00x, FINOPS-00x, ORCH-00x).
- Before merge when possible, so findings can be fixed in the same PR; otherwise within the cycle
  the PR merged in. An item stays **In review** until the review records an outcome.
- Again for any item whose Accepted status is challenged by a defect report.

## Who reviews

| Role | Does |
| --- | --- |
| Reviewer (a Claude thread or a person who didn't write the PR) | Runs the checks below and writes the review record |
| Owning workstream | Answers questions and fixes Changes Required findings |
| Master product manager | Checks the record is complete and recommends an outcome |
| Product owner (Chris) | Decides the outcome; only the product owner accepts or rejects |

The author can't review their own PR. AGENTS.md routing still applies: automated routing never
approves or merges, and application code, authentication, permissions, data mutations, updates,
installers, signing and workflows need maintainer review.

## Outcomes

| Outcome | Meaning | Backlog state | Inventory |
| --- | --- | --- | --- |
| **Accepted** | Every acceptance criterion is met with evidence, no open findings | Accepted | Status and verification updated with the new evidence |
| **Accepted with Documented Limitations** | Criteria met except named limitations that the product owner agrees can ship; each limitation is documented for users and has a backlog item | Accepted | Limitations listed on the feature row |
| **Changes Required** | One or more criteria not met or a finding must be fixed first | In review (or In progress) | Unchanged |
| **Rejected** | The change doesn't solve the problem, or does harm; it should be reverted or replaced | Rejected (or back to Proposed with a new approach) | Unchanged; revert recorded if merged |
| **Unable to Verify** | The reviewer couldn't run what the criteria need (no tenant, no Windows, missing access) | In review | Verification stays as it was; the record says what is needed to finish |

**Unable to Verify is not a soft accept.** Use it whenever evidence is missing, and say exactly who
needs to do what (for example "the maintainer runs flow 6 of the tenant smoke test").

## The checks

Each check produces evidence in the record: a command and its output, a file and line, a screenshot
or a named person's observation. "Looks fine" is not evidence.

1. **Scope.** The PR does what its package or backlog item says, and nothing it says is out of scope.
2. **Acceptance criteria.** Each criterion is marked met, not met or unable to verify, with evidence.
3. **Tests.** The PR's new tests fail without the change and pass with it (or the reviewer explains why
   that can't be shown). The full suites pass: `pytest sidecar/tests`, `node --test app/tests/ui.test.cjs`,
   and `cargo test` once PB-071 lands.
4. **Behavior users receive.** The feature is reachable where the docs say (page, command, CLI), works
   with Preview features hidden or shown as intended, and is packaged into the engine (AGENTS.md).
5. **OneLake data safety.** No unintended writes, overwrites or deletes; dry runs mutate nothing;
   retries are safe; tenant, workspace and lakehouse boundaries hold.
6. **Authentication and distribution.** Token storage, scopes (any new scope is in
   `docs/permissions.md`, alphabetical), localhost exposure, updater provenance, signing and installer
   behavior unchanged or improved.
7. **Fabric compatibility claims.** Nothing says local results equal Fabric results unless a proof or a
   test on a tenant shows it. Untested Fabric integrations keep a Preview tag.
8. **Documentation.** Every doc claim the PR adds or changes matches the code; docs are linked from
   `README.md` and `docs/README.md`; `zensical build` passes in strict mode.
9. **Accessibility.** New controls have names, are keyboard reachable and respect reduced motion.
10. **Product data.** The inventory and backlog are updated and
    `python docs/product/tools/product_data.py all` ends with "Product data is consistent."

## Recording the review

Copy [reviews/TEMPLATE.md](reviews/TEMPLATE.md) to `docs/product/reviews/<PB-id>-<pr-number>.md`,
fill it in, and commit it with the inventory and backlog updates (in the PR itself when reviewed
before merge, or in a small follow-up docs PR). Link the record from the PR.

## After the outcome

- **Accepted / Accepted with Documented Limitations:** set the backlog item's `state` to Accepted; update
  the feature rows (status, verification, evidence, limitations); run `product_data.py all`.
- **Changes Required:** list findings on the PR; the owner fixes them; re-review only what changed.
- **Rejected:** open a revert PR if merged, or close the PR; record why in the backlog item.
- **Unable to Verify:** ask the named person for the missing step; the record stays open.
