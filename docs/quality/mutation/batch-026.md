# Mutation testing, batch 26

Three modules, none touched by an open pull request:
- The local HTTP API: the status each error code gets, the size, rate and concurrency limits at their boundaries, why a request is refused, the OpenAPI document, which resources are listed, the server's settings, `serve()` staying off unless the organization and the person both have the API on, and the parent watcher, on Windows too (`integrations/http_server.py`).
- Before a run starts: ages and durations at each unit boundary, the defaults, each check's evidence and wording, the last five finished runs, CU measurements without a value, input sizes, schedules, and which finding the summary names (`risk.py`).
- Notebook cell dependencies: dotted imports, imports inside nested functions, keyword-only defaults, names read after a function, write calls without a literal name, file-system calls, a cell magic below a line magic, and the line a parse error or a network call is reported on (`depgraph.py`).

Batch 25's modules (`integrations/registry.py`, `opportunities/analyzers.py` and `shortcut_center.py`) were left to batch 25.

The run was at `main` a09aa1fe0 (2026-11-15), with `sidecar/tools/mutate.py` and the same method as batches 1 to 25 (sample cap 150, seed 7), on DuckDB 1.5.6.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Every test that was kept passed both ways, so no run below can score a false 100%. `test_integrations_conformance.py::test_capability_ids_in_docs_are_real` reads `docs/`, so it was left out of the `integrations/http_server.py` run.

`test_risk_service_edges.py` wasn't found by the first search, so `risk.py`'s "before" run was done again with it. It killed no more mutants (101 / 49 both times), since its stand-in service hands the analyzer facts that the other tests already cover.

The new HTTP tests don't open a socket. Requests go through the guard in-process (Starlette's test client, or the guard called directly), and `serve()` runs with a stand-in for uvicorn's server. Like the other new tests, they point `LOCALAPPDATA` and `HOME` at the test's own temporary folder, and they wait on events, not on timing margins.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `integrations/http_server.py` | test_integrations_http, test_integrations_http_guard, test_integrations_extensions, test_integrations_audit, test_integrations_peer, test_integrations_resources, test_integrations_conformance, test_cli_local_commands (+ test_integrations_http_mutation) | 150 of 227 | 107 / 43 | **71.3%** | 144 / 6 | **96.0%** | 4 equivalent, 2 not tested |
| `risk.py` | test_risk, test_risk_service_edges, test_compute_check_ast, test_model_bpa (+ test_risk_mutation) | 150 of 257 | 101 / 49 | **67.3%** | 149 / 1 | **99.3%** | 1 equivalent |
| `depgraph.py` | test_depgraph, test_depgraph_edges, test_notebook_magics, test_notebook_resources (+ test_depgraph_mutation) | 150 of 187 | 128 / 22 | **85.3%** | 149 / 1 | **99.3%** | 1 equivalent |

One `http_server.py` mutant timed out before and after, and was counted as killed: it keeps the parent watcher from stopping the server once its parent has gone. The three new test files pass on DuckDB 1.5.6 and 2.0.0.

### What the survivors were, and the tests that now kill them

- **integrations/http_server.py**:
  - The status of every error code, and the error body saying `"ok": false`.
  - A Content-Length of exactly 1 MiB passing and one byte more being refused, and the same for a streamed body.
  - A result of exactly 1,000,000 bytes being returned and one byte more being refused.
  - A burst of 30 calls per tool, then five a second.
  - Four calls running at once.
  - Refusals saying why: the size limit in KB, no token, a wrong token, and a rate limit that is retryable with a hint and `Retry-After: 1`.
  - The OpenAPI document's error schema, its 503 response, the resource URI's 2,000-character limit, and an invoke body that is optional and takes only `arguments`.
  - Resources that are switched off not being listed.
  - The server's settings: loopback, keep-alive 5 s, 33 connections counting the new one, graceful shutdown 10 s, no access log and no server header.
  - `serve()` staying off when either of the person's two switches, or either of the organization's, is off; and going on to start when all are on.
  - A server that never listened stopping cleanly, with its parent watcher on a daemon thread.
  - A live parent keeping the server running, and no parent pid meaning it follows nobody.
  - A parent that has ended being reported as not alive, and on Windows, only a process that is still active counting as alive, with its handle closed.
  - → `test_integrations_http_mutation.py`.
- **risk.py**:
  - Ages and durations at each unit boundary, and rounding at the next one.
  - The defaults and the worst-first order.
  - A finding's fields, and only a warning being overridable.
  - The policy passing by default, and sign-in needed for Fabric or when asked.
  - A cached capacity reading saying so.
  - The title of a capacity that can't be read, and a paused capacity's status text.
  - An unknown state naming the capacity.
  - The first three definition errors, and an old version naming the environment.
  - Compatibility naming the place the run goes.
  - A running run saying when it started and where, with at most three listed.
  - Only the last five finished runs counting, "1 successful run" being singular, and the failure threshold, including 0 turning it off.
  - CU measurements without a value being skipped, with the evidence, source and threshold wording.
  - Input notes and defaults, and the memory guard only blocking runs on this PC.
  - At most four schedules listed, with the window in minutes.
  - The summary naming the first blocked finding, warnings being counted, acknowledgement following the setting, a non-essential unknown still passing, and the id's length.
  - → `test_risk_mutation.py`.
- **depgraph.py**:
  - `import a.b.c` defining `a`, and an alias or a name from `from m import`.
  - An import inside a nested function staying local to it.
  - Keyword-only defaults being read when the function is defined.
  - Names after a function being read at once, not deferred.
  - `write_table` and file writes needing a literal name or path.
  - Only `sql` calls being read as SQL, and only calls on `fs` changing files.
  - A copy writing its destination.
  - A cell magic one or two lines below a line magic not repeating it.
  - A parse error and a network call being reported on their own lines.
  - A declared dependency carrying no names.
  - → `test_depgraph_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `integrations/http_server.py` | 68 | default status 500 → 501 | Equivalent: an `IntegrationError` can only have a known code, and every known code is in the table. |
| `integrations/http_server.py` | 139 (2) | `[:2]` → `[:3]`, and `("", 0)` → `("", 1)` | Equivalent: a client is a host and a port, and a request with no client is refused either way. |
| `integrations/http_server.py` | 396 | `or` → `and` in the header deadline's close check | Needs a live connection that is part way through a response when the deadline passes; a test of it would depend on timing. |
| `integrations/http_server.py` | 313 | `abandon_on_cancel=True` → `False` | Only changes how soon a call whose client hung up stops waiting for its thread; a test of it would depend on timing. |
| `integrations/http_server.py` | 478 | the parent watcher sleeps 0 s, not 1 s | Equivalent in behaviour: it only checks the parent more often. |
| `risk.py` | 351 | `sort_keys=True` → `False` | Equivalent: the key is a list of strings and tuples, with no dictionary to sort. |
| `depgraph.py` | 257 | `exc.lineno or 1` → `or 0` | Equivalent in practice: `ast.parse` always gives a line number for a syntax error. |

### Real bugs found

None.

### Not in this batch

Batch 25's modules.

