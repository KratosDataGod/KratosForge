# Mutation testing, batch 361

One module not scored before, run with `--kinds all`:
- Settings > Integrations on the engine: the local API's process and switch, policy refusals, connected tools and their permissions, status, pending approvals, the activity list and Test connection (`integrations/app_service.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. The module has 197 mutants, so 150 were sampled.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0, and against the module as it is on `main` today. The "after" figures come from `--only` runs of the survivors with the new tests added.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `integrations/app_service.py` | test_local_retention_gaps, test_integrations_peer, test_integrations_app_service_cov3, test_integrations_app_edges (+ test_integrations_app_service_mutation) | 150 of 197 (147 + 3) | 90 / 60 | **60.0%** | 90/147 → 142/147 | 0/3 → 3/3 | 145 / 5 | **96.7%** | 5 equivalent |

No bugs were found. The local API's process, the audit trail, the executions list and the MCP client are stand-ins; nothing listens on a port.

### What the survivors were, and the tests that now kill them

- **integrations/app_service.py**:
  - The last line of a short log keeps its first character. The log rolls only past 5 MiB.
  - A tool's config has the API's URL only for HTTP. The API isn't allowed when the organization turns integrations off.
  - Starting the API when its folder is there, and stopping one that never started.
  - The API switch is refused when policy forbids it. Ports outside 1024 to 65535, or not a number, are refused; both ends are taken.
  - A new token comes back. Status, when the organization turns integrations off or the person turns them on, reports both and writes the launcher only when on. Turning integrations on is refused when the organization turned them off.
  - Pending follows the switch and the policy. Approvals (50 by default) and decisions pass through.
  - Activity reads 100 lines by default, shows a finished call once, looks for unrecorded approvals from its oldest line when the page is full, shows an approval with no line as unfinished, and shows an execution's state where it has one.
  - The audit check names its folder. Testing a stdio tool runs the stdio test, which lists the tools and the protocol with a stand-in client.
  - → `test_integrations_app_service_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `integrations/app_service.py` | 33, 132, 301, 309 | `HTTP_START_SECONDS` 15, the stop wait 10 s, the health check 5 s and the stdio test 60 s, each one second longer | Equivalent: only how long a wait may last changes. |
| `integrations/app_service.py` | 58 | `f.seek(0, os.SEEK_END)` to `f.seek(1, ...)` | Equivalent: the read window starts one byte later at most, and only the last line is used. |
