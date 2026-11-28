# Mutation testing, batch 352

One module not scored before, run with `--kinds all`:
- The contract every external caller (an MCP client, the local API) gets: one error shape, arguments checked against the capability's JSON Schema, and a call context with a deadline, cancellation and progress (`integrations/core.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. The module has 175 mutants, so 150 were sampled. `main` has changed this module since this run (#3175); the new tests pass against today's version too.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0, and against the module as it is on `main` today.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `integrations/core.py` | test_integrations_core, test_integrations_core_edges, test_integrations_registry, test_integrations_registry_edges, test_integrations_registry_mutation, test_integrations_app, test_integrations_tool_guards (+ test_integrations_core_mutation_b) | 150 of 175 (142 + 8) | 126 / 24 | **84.0%** | 124/142 → 142/142 | 2/8 → 8/8 | 150 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **integrations/core.py**:
  - The error shape on the wire has a hint and details only when there are some.
  - A Fabric 401 is "permission_denied"; a 503 is a retryable "upstream" error with a hint; a 400 is an "upstream" error that isn't retryable.
  - A boolean is neither an integer nor a number, null is only None, and a boolean isn't held to a number's minimum.
  - `$` is the end of the text outside `[...]` only, and only the one character after a backslash is escaped.
  - A string with no `minLength` may be empty and a list with no `minItems` may be empty; `minimum`, `maximum`, `minItems` and `maxItems` hold at exactly their values.
  - The message lists ten problems, and the details keep up to 50.
  - A call context gives up exactly at its deadline, not before, as a retryable timeout; so does a call run on its worker thread, which is a daemon thread.
  - Progress goes to the callback when there is one.
  - → `test_integrations_core_mutation_b.py`.

### Left

None.
