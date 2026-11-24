# Mutation testing, batch 370

One module not scored before, run with `--kinds all`:
- A team asks for a source with a Copy job, and approving makes it: where the job lands, finding or making it once, its daily schedule, which waiting request lands where, a source kept while its Copy job is still in Fabric, and the landing zone naming each source's job (`central/landing_copy_service.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. The module has 139 mutants, so all were run; none is a boundary mutant. `main` hasn't changed this module since.

The first run used test_landing_copy_service_edges and test_landing_zones only, and scored 63 / 139 (45.3%). Each survivor was run again with test_landing_copy_requests, test_request_left_in_fabric, test_zone_delete_waiting and test_request_audit_trail added, which reach this module through the service, and the "before" figures are from that run.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `central/landing_copy_service.py` | test_landing_copy_service_edges, test_landing_zones, test_landing_copy_requests, test_request_left_in_fabric, test_zone_delete_waiting, test_request_audit_trail (+ test_central_landing_copy_service_mutation) | 139 of 139 (139 + 0) | 112 / 27 | **80.6%** | 112/139 → 137/139 | none | 137 / 2 | **98.6%** | 2 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **central/landing_copy_service.py**:
  - A workspace is gone on a 404 or a not-found code, and not on a 403 or a 405. The admin API's answer decides, and a state of Deleted counts as gone.
  - Without one landing workspace, a job lands in the environment's LH_Landing, with no workspace name, zone or secure flag. An environment needs both its workspace and its LH_Landing ids.
  - Only waiting source requests that ask for a Copy job are told where they land, and none is without one landing workspace.
  - A new Copy job is made, recorded and scheduled, and its description keeps the reason (none too). The job this request made is found by its id, or by its marker in a name that differs only in case, and isn't recorded again. Its existing schedule is kept. A job of another type or name isn't it.
  - A request with no times gets no schedule, and one already scheduled keeps its schedule.
  - Only source requests for this source, with a job id, hold the source.
  - The landing zone shows the Copy jobs of approved source requests only, with the job's name or the default one, and no tables or times when the copy details are missing.
  - → `test_central_landing_copy_service_mutation.py`, with a stand-in catalog and Fabric.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `central/landing_copy_service.py` | 204 | `return False` to a bare `return` when the admin API fails with an error that isn't Fabric's | Equivalent: the only caller tests the result for truth, and `None` and `False` are both false. |
| `central/landing_copy_service.py` | 141 | a new job's `created` flag `True` to `False` | Equivalent: a new job's id is never the recorded one, so `made.get("copy_job_id") != item["id"]` still records it, and nothing else reads the flag. |
