# Mutation testing, batch 380: central/landing_copy_service.py (every mutant)

One module, run uncapped: every mutant, not a sample (`--cap 100000 --seed 7 --kinds all`). This is the first mutation run of this module. It has no `<` or `>` comparison, so it has no boundary mutants.
- Source requests that ask for a Copy job (`central/landing_copy_service.py`): where the job lands, making it and its daily schedule as the approver (reusing the one this request made on a retry), the landing page naming each source's job, and refusing to delete a source while its job is still in Fabric.

It has 139 mutants, and this run ran all 139 of 139, uncapped.

The run was at `main` 293fb38daa (2026-11-23), with the method of batch 379. Each pytest session had its own fresh `--basetemp`, removed at the end of the session; the nesting-check log stayed empty for the run and the re-run. The kills don't end in a streak of near-identical times. Three kills chosen at random (mutants 17, 49 and 126) were re-run by hand without `-x`: each failed real tests (10, 25 and 2). No mutant timed out.

The set is test_landing_copy_requests, test_landing_copy_service_edges and test_landing_zones. It runs in about 18 seconds on this loaded machine. No `-k` filter or `--deselect` was needed.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `central/landing_copy_service.py` | test_landing_copy_requests, test_landing_copy_service_edges, test_landing_zones; after: + test_landing_copy_service_mutation | 139 of 139 (139 + 0), uncapped | 112 / 27 | **80.6%** | 112 / 139 (80.6%) → 134 / 139 (96.4%) | none | 134 / 5 | **96.4%** | 5 equivalent |

The 27 survivors were re-run with the new file added (`--only`): 22 are now killed, 5 are left. The new tests in `tests/test_landing_copy_service_mutation.py` check:
- which Fabric answers count as "not found": a 404 with any code, or a not-found code with any status, both from the landing workspace read and from the admin API; and that no access (403) keeps the source even when the admin API says the workspace is deleted;
- a Copy job for another source never holds this one, and a held source's message names the job even when its name wasn't recorded (the landing page too);
- only waiting source requests with a Copy job say where they land, and only with one landing workspace;
- a Copy job this request made but never recorded is recorded on approval, an approval again reuses the recorded job whose description was edited without recording it twice, and a schedule already on the job is reused;
- other items in the landing workspace (another source's Copy job, a notebook of the same name) never block the job, and the job's description carries the reason;
- a request without times gets no schedule, and an environment with a workspace but no LH_Landing says where to set it up.

No bugs were found.

### Left

- Mutants 59 and 60 (line 98): without one landing workspace, `_lc_where` hands back `workspace_name` always empty, or `secure` True. Only `lands_in` shows those fields, and it is set only when there is one landing workspace, which takes the other branch; approving reads only the workspace and lakehouse ids. Equivalent.
- Mutants 69 and 70 (lines 201 and 204): `_lc_workspace_gone` returns `None` instead of `False`. Its only caller tests it in `not (... and ...)`, which treats both alike. Equivalent.
- Mutant 85 (line 141): a newly made Copy job comes back as not made. Its progress is recorded anyway, because the recorded job id (none, or a job since deleted in Fabric) is never the new job's id. Equivalent unless Fabric hands out an id again.
