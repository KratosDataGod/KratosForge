# Mutation testing, batch 400

One module not scored before, run with `--kinds all`:
- Carrying out the platform's requests in Fabric: a workspace made or found by its marker, its admins added once, access given or kept, and what a closed request leaves behind (`central/requests_service.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. The module has 269 mutants, so 150 were sampled. None of the test files reads `docs/`, `examples/` or `app/`, so nothing was left out.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0, and against the module as it is on `main` today, which hasn't changed it since ea988e99. The "after" figures come from a `mutate.py --only` run of the survivors with the new tests added.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `central/requests_service.py` | test_requests_service_cov3, test_requests_core_four, test_request_markers, test_request_retries, test_request_left_in_fabric, test_shortcut_request_envs, test_landing_copy_requests, test_landing_copy_service_edges, test_domain_adoption, test_typed_errors (+ test_central_requests_service_mutation_b) | 150 of 269 (148 + 2) | 133 / 17 | **88.7%** | 132/148 → 148/148 | 1/2 → 1/2 | 149 / 1 | **99.3%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **central/requests_service.py**:
  - A "not found" error about something other than the operation that was called isn't read as an older platform.
  - A closed shortcut request with nothing made says nothing. One that made a shortcut names the shortcut and its lakehouse, or the target and "its lakehouse" when the request didn't name them.
  - Only waiting workspace access requests that the viewer can decide get the role their person has today. Each workspace's roles are read once, and one that can't be read says so.
  - A closed domain request only notes its own domain, not another one in the list.
  - A workspace the request has just made isn't put on its capacity a second time.
  - A failed admin step names the admin.
  - Giving a role someone didn't have returns `{"assigned": True}`.
  - → `test_central_requests_service_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `central/requests_service.py` | 423 | `_rank(have) > _rank(role)` to `>=` | Equivalent: two roles of the same rank are the same role, and that case has already returned "same" on line 422. |
