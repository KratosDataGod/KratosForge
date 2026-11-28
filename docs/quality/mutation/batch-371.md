# Mutation testing, batch 371

One module not scored before, run with `--kinds all`:
- Enterprise governance on the desktop: the calls that pass policy changes on to the platform, a project's `governance.toml`, resolving the policies in force, and the exceptions about to end with their one Teams notice per organization (`central/governance_service.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. The module has 87 mutants, so all were run. `main` hasn't changed this module since.

The first run used test_governance only, and scored 26 / 87 (29.9%). Each survivor was run again with test_best_practice_checks, test_requests_core_four, test_central_roles, test_approvals_guard and test_policy_package added, which reach this module through the service, and the "before" figures are from that run.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `central/governance_service.py` | test_governance, test_best_practice_checks, test_requests_core_four, test_central_roles, test_approvals_guard, test_policy_package (+ test_central_governance_service_mutation) | 87 of 87 (86 + 1) | 61 / 26 | **70.1%** | 61/86 → 84/86 | 0/1 → 1/1 | 85 / 2 | **97.7%** | 2 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **central/governance_service.py**:
  - Each policy call (a version, create, save or discard a draft, publish, set a status, applicability) sends its arguments and returns the platform's answer.
  - Assigning sends the settings, or none as `{}`, and unassigning; both return the policy again.
  - A policy shows `problems` only when its exceptions can't be read (an older UDF_Platform).
  - An exception with an `ended_at` isn't active even when its row says so. An end date with a time still counts its days. One that ends in exactly 14 days is ending; 15 isn't.
  - Resolving sends the environment only when one is named. A named domain and subdomain win over the open project's, and a missing one takes the project's. An entity the open project doesn't have adds no layer.
  - "For you" marks the person who asked, the open project and its domain or subdomain, and nothing else: not a project named like a domain, or a domain named like the project.
  - A claimed notice is sent, and what sending says is a problem only when there is something to say. An unclaimed one is left to the PC that claimed it. Quiet hours saved with seconds still let a notice be claimed.
  - → `test_central_governance_service_mutation.py`, with a stand-in platform that records each call.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `central/governance_service.py` | 217, 230 | `return []` to a bare `return` in `_claim_warnings` | Equivalent: its only caller tests the result for truth (`if events:`), and `None` and `[]` are both false. |
