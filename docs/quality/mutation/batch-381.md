# Mutation testing, batch 381: central/requests_service.py (every mutant)

One module, run uncapped: every mutant, not a sample (`--cap 100000 --seed 7 --kinds all`). This is the first mutation run of this module. It has two boundary mutants (lines 338 and 423).
- Requests that make something in Fabric or the platform (`central/requests_service.py`): a new workspace, workspace access, a domain and a policy exception, made as the approver before the platform approves; what a declined or withdrawn request leaves in Fabric (`left_in_fabric`); the marker an approval writes into descriptions; and `call_or_repair` for an older UDF_Platform.

It has 269 mutants, and this run ran all 269 of 269, uncapped.

The run was at `main` 293fb38daa (2026-11-23), with the method of batch 379. The container restarted during the re-run of the survivors; that re-run was thrown away and run again from the start (`main` unchanged), and only the complete one is reported. Each pytest session had its own fresh `--basetemp`, removed at the end of the session; the nesting-check log stayed empty for the run and the re-run, and the session the restart cut short had no folder 40 levels deep either. The kills don't end in a streak of near-identical times. Three kills chosen at random (mutants 126, 128 and 154) were re-run by hand without `-x`: each failed real tests (76, 39 and 6). One mutant timed out and counts as killed: mutant 1 (line 54) negates `while _MARKER_WORDS.search(typed)`, which then never ends for text without marker words.

The set is test_requests_core_four, test_request_left_in_fabric, test_request_retries, test_request_markers, test_requests_service_cov3, test_shortcut_request_envs, test_domain_adoption and one test of test_typed_errors (`test_call_or_repair_keeps_not_set_up`; the rest of that file is about other modules). It runs in about 12 seconds. test_landing_copy_requests and test_landing_copy_service_edges, which import from this module, were left out: they test `central/landing_copy_service.py` (batch 380).

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `central/requests_service.py` | test_requests_core_four, test_request_left_in_fabric, test_request_retries, test_request_markers, test_requests_service_cov3, test_shortcut_request_envs, test_domain_adoption, test_typed_errors (1 test); after: + test_requests_service_mutation | 269 of 269 (267 + 2), uncapped | 237 / 32 (1 by timeout) | **88.1%** | 236 / 267 (88.4%) → 267 / 267 (100%) | 1 / 2 → 1 / 2 | 268 / 1 | **99.6%** | 1 equivalent |

The 32 survivors were re-run with the new file added (`--only`): 31 are now killed, 1 is left. The new tests in `tests/test_requests_service_mutation.py` check:
- the longest descriptions Fabric takes (4000 for a workspace, 256 for a domain or an item), and that a long reason is cut to exactly that with the request's marker kept whole; a marker that fills the limit leaves no typed text;
- a waiting access request says the role its person or group has today, or that it couldn't be checked, and only waiting access requests say so;
- a new workspace is never moved to its capacity again, even when Fabric's answer leaves `capacityId` out; a retry that finds it on its capacity leaves it there, and a failed move back names the capacity; a failed admin step names the group by its name;
- approving access says it is assigned, whether the role was given or someone already had a higher one; a failed access step names the workspace by its name; a retry keeps the role the first approval raised it from;
- a retry knows its own domain by the name asked for (not its id), and its own subdomain under its Fabric parent; declining a domain request says nothing of other domains;
- a workspace name already taken is refused by the name asked for, not the request's target;
- `call_or_repair` passes a "not found" from another operation through, and turns "Operation ... not found" for this one into the Finish-or-repair message;
- what a shortcut request leaves: nothing when it made nothing, and the removal advice for one it made.

No bugs were found.

### Left

- Mutant 268 (boundary, line 423): `_rank(have) >= _rank(role)` in place of `>` when deciding to keep a higher role. The ranks are the positions in `WORKSPACE_ROLES`, so they are equal only when the roles are, and `have == role` has already returned "same" two lines up. Equivalent.
