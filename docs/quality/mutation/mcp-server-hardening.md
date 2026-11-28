# Mutation testing, MCP server hardening (#2363)

A score of the four integrations modules the MCP server hardening changed: `integrations/core.py`, `registry.py`, `audit.py` and `http_server.py`. It used `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6, with the hardening changes applied. The first pass ran the module's fast test files. Survivors were then re-run with `--only`, with the new test files added. Every mutant in the functions the hardening changed was also run, including the ones the sample missed.

## Results

| Module | Test files run | Mutants run | Before | After | Left |
| --- | --- | --- | --- | --- | --- |
| `integrations/core.py` | test_integrations_core, test_integrations_core_edges (+ test_integrations_core_mutation) | 150 of 227 | 104 / 46, **69.3%** | 150 / 0, **100%** | none |
| `integrations/registry.py` | test_integrations_registry, _registry_mutation, _mcp, _permissions, _http, _audit (+ test_integrations_registry_mutation2) | 150 of 256 | 128 / 22, **85.3%** | 150 / 0, **100%** | none |
| `integrations/audit.py` | test_integrations_audit, _app, _extensions, test_sqlite_connections_closed, _audit_mutation (+ test_integrations_audit_mutation2) | 44 of 44 | 43 / 1, **97.7%** | 44 / 0, **100%** | none |
| `integrations/http_server.py` | test_integrations_http, _http_guard, _http_mutation, _http_boundary, _peer, _resources (+ test_integrations_http_mutation2) | 150 of 271 | 123 / 27, **82.0%** | 144 / 6, **96.0%** | 6 equivalent |

The test files the hardening added (output leaks, hostile arguments, audit on every call, parser fuzz) kill 27 of core.py's 46 survivors and 20 of registry.py's 22. The new files kill the rest.

No bugs were found. One message was fixed along the way: `error_from` stripped one quote off a missing file's name (`No such file or directory: 'x.ipynb`), because it stripped quotes meant for a KeyError's repr from both ends separately.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `http_server.py` | `STATUS.get(err.code, 500)` | the default changed | Equivalent, as in batch 26: every error code is in the table. |
| `http_server.py` | `getattr(exc, "status_code", 500)` | 500 to 501 | Equivalent: anything other than 404 or 405 is answered 500 either way. |
| `http_server.py` | the client address's `[:2]` and `("", 0)` | `[:3]`, `("", 1)` | Equivalent, as in batch 26. |
| `http_server.py` (extra run) | `abandon_on_cancel=True` | `False` | Equivalent, as in batch 26: it only changes timing. |
| `http_server.py` | the parent watcher's 1 s sleep | 0 s | Equivalent, as in batch 26: it only checks more often. |
| `http_server.py` | the first `messages[-1]` in the body reader | `messages[0]` | Equivalent: the first message is `http.request` unless it is the only one, and a disconnect has no `more_body`. |
| `http_server.py` (extra run) | `except IntegrationError: return refuse(...)` in `invoke` | removed | Unreachable: the guard has already read the body, and `_strict_json` never raises IntegrationError. |
| `registry.py` (extra run) | `{"kind","state","title"} <= set(result)` | `<` | Equivalent: `"id"` is always there too, so the subset is always proper. |
