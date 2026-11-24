# Mutation testing, batch 379: central/governance_service.py (every mutant)

One module, run uncapped: every mutant, not a sample (`--cap 100000 --seed 7 --kinds all`). This is the first mutation run of this module. It has one boundary mutant (`left <= EXCEPTION_WARN_DAYS`, line 100).
- What the Governance page calls on this PC (`central/governance_service.py`): policies and their versions, drafts, status, assignment and resolution, and the policy exceptions, including which are about to end, whom they concern and the claimed Teams notice for them.

It has 87 mutants, and this run ran all 87 of 87, uncapped.

The run was at `main` 293fb38daa (2026-11-23). Each pytest session had its own fresh `--basetemp`, removed at the end of the session; the nesting-check log (every session whose tree reached 40 levels) stayed empty for the first run and both re-runs. The kills don't end in a streak of near-identical times. Three kills chosen at random (mutants 1, 19 and 46) were re-run by hand without `-x`: each failed real tests (2, 9 and 2). No mutant timed out.

The set is test_governance, test_audit, test_checks_service_cov2, test_governance_notices, test_best_practice_checkers and 14 named tests from test_requests_core_four (the exception tests: granting, ending early, the ending notice and who claims it). It runs in about 25 to 35 seconds on this loaded machine. Left out for time: test_platform_repo (64 s), test_policy_promote_gate (53 s), the rest of test_requests_core_four (32 s for the file), test_best_practice_checks (11 s), test_policy_package (13 s), test_learn_team and test_model_practices. test_rpc_surface was left out because it reads `app/`, which the tool's copy of `sidecar/` doesn't have.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `central/governance_service.py` | test_governance, test_audit, test_checks_service_cov2, test_governance_notices, test_best_practice_checkers, test_requests_core_four (14 tests); after: + test_governance_service_mutation | 87 of 87 (86 + 1), uncapped | 60 / 27 | **69.0%** | 60 / 86 (69.8%) → 83 / 86 (96.5%) | 0 / 1 → 1 / 1 | 84 / 3 | **96.6%** | 3 equivalent |

The 27 survivors were re-run with the new file added (`--only`): 24 are now killed, 3 are left. The new tests in `tests/test_governance_service_mutation.py` check:
- what each policy call hands back (create, publish, version, save and discard a draft, status, assign with settings, unassign, applicability), which nothing checked: mutants that dropped the return value all survived;
- resolving for an environment applies only the policies for it, and the open project resolves with the domain and subdomain its project file names, and an entity it doesn't have resolves without a layer;
- an ending exception whose notice is sent reports no problem, quiet hours saved with seconds still let it go, and a notice another PC claimed isn't sent;
- whom an ending exception is for: a project exception on the open project is for you whoever asked, a project exception named like your domain isn't, and a domain exception is for you only when it covers your domain;
- an exception ending in exactly 14 days (EXCEPTION_WARN_DAYS) is ending (the boundary mutant), and one past its date is listed as ended with no days left.

No bugs were found.

### Left

- Mutants 48 and 62 (lines 217 and 230): `_claim_warnings` returns `None` instead of `[]` when the Teams routes can't be read or the platform can't claim. Its only caller tests `if events:`, which treats both alike. Equivalent.
- Mutant 84 (line 94): the exception's end date is cut to 11 characters instead of 10 before it's read. The platform always stores and returns `until` as a 10-character date (`policy._date` normalizes it with `isoformat()`), so the 11th character is never there. Equivalent.
