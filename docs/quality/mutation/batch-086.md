# Mutation testing, batch 86

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- How HTTPS failures read behind a corporate network: the Windows certificate store, the root-certificate file for gRPC, and the one-line reason for a refused certificate, a proxy, an unreachable server or a timeout, including pip's output (`net_trust.py`).
- Spark's answers for arrays, maps and generators on this PC, and the map forms that need the bound tables' types (`spark_complex.py`).
- Running several notebooks at once from Home: the limits, the background process and its heartbeat, Stop, and old batches (`notebook_batch_service.py`).

The run was at `main` 61d0e49ee (2026-11-17), with `sidecar/tools/mutate.py` as fixed in #2839, so each mutant ran without a bytecode cache, and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all were run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used. Tests that read `docs/` or `app/` were left out with `--deselect`:
- `test_spark_complex.py::test_docs_list_what_still_differs` reads `docs/`.
- In `test_corporate_network.py` and `test_offline.py`, the update-check tests load the shell's `app/src-tauri/src/updater.py`, and `test_every_outbound_call_names_a_timeout_or_uses_the_shared_session` reads it too; the other tests in both files ran. `test_clock_skew.py` loads `updater.py` when it is imported, so it was left out whole.

`localdata/tsql_types.py` was scored first as the third module. Its tests killed all 150 sampled mutants (of 204) before any new test, so it was replaced by `notebook_batch_service.py`.

The first `net_trust.py` run used only `test_net_trust`, `test_tls_inspection` and `test_offline_start` (68 / 23). `test_corporate_network` and `test_offline` also call the module, so it was run again with them, and the "before" figures below are from that run.

A mutant whose run times out counts as killed, as in earlier batches.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `net_trust.py` | test_net_trust, test_tls_inspection, test_offline_start, test_corporate_network, test_offline (+ test_net_trust_mutation) | 91 of 91 (91 + 0) | 81 / 10 | **89.0%** | 81/91 → 91/91 | none | 91 / 0 | **100%** | none |
| `spark_complex.py` | test_spark_complex, test_spark_higher_order (+ test_spark_complex_mutation) | 106 of 106 (105 + 1) | 89 / 17 | **84.0%** | 88/105 → 97/105 | 1/1 → 1/1 | 98 / 8 | **92.5%** | 8 equivalent |
| `notebook_batch_service.py` | test_notebook_batch, test_notebook_batch_edges, test_notebook_batch_stop, test_regex_anchors (its batch id test) (+ test_notebook_batch_service_mutation) | 130 of 130 (124 + 6) | 91 / 39 | **70.0%** | 89/124 → 121/124 | 2/6 → 6/6 | 127 / 3 | **97.7%** | 3 equivalent |

One bug was found, outside the three modules, and is fixed; its test in `test_spark_complex_mutation.py` now passes:
- `LATERAL VIEW stack(...)` and `LATERAL VIEW json_tuple(...)` gave each row's values as one struct column (`{'col0': 1}`), where Spark gives the values themselves (`1`). The rewrite is in `spark_builtins_sql.py`. In a select list both gave the values. After `LATERAL VIEW` they, and `inline`, now give one column per name.

### What the survivors were, and the tests that now kill them

- **net_trust.py**:
  - A new process hasn't switched to the Windows store yet (checked in a process of its own).
  - A certificate refused for its dates is told apart in any letter case, as a plain `True` or `False`.
  - PEM text splits into one normalised block per certificate, and an unfinished block is dropped.
  - The gRPC root file keeps only X.509 roots that Windows trusts for every purpose or for server sign-in, sorted, with the public list. A stale file is rewritten, and a current one is left alone.
  - A proxy refusal is known by a `ProxyError` class anywhere in the exception's class tree, or by its words.
  - "Couldn't reach the server" needs an HTTP library in the chain, and gives a plain `False` without one.
  - pip's output: the certificate line (with or without the date reason), the proxy line and the unreachable line for each marker, each naming the fix where it should.
  - → `test_net_trust_mutation.py`.
- **spark_complex.py**:
  - A map `size` whose argument holds `rand()` reads it once and still counts the map's keys.
  - `LATERAL VIEW OUTER explode` of a map holding `rand()` reads it once and keeps the empty and NULL rows.
  - `LATERAL VIEW explode` of a map with no table name gets one of its own.
  - `LATERAL VIEW explode_outer` of a map never drops rows.
  - `reverse` of a number or of a map is left to DuckDB, which refuses the map as Spark does.
  - Only the calls the query itself makes are rewritten, and a two-argument call other than `map_concat`, such as a registered UDF, keeps its call.
  - A statement with nothing marked is returned as it is, and `json_tuple` and `stack` in a `LATERAL VIEW` keep their row counts.
  - → `test_spark_complex_mutation.py`.
- **notebook_batch_service.py**:
  - Up to 50 notebooks run at once, and 51 are refused.
  - The parallel count is 2 by default and kept between 1 and 4.
  - The background processes start with no window, the working folder set, and no console. A notebook's own process reads and writes UTF-8 text.
  - A batch is lost only after more than 90 quiet seconds, and a finished batch is never lost.
  - A notebook that ignores Stop is ended once more than 30 seconds have passed, its last output is kept, and it is logged with the grace it had.
  - The last result line is the notebook's result.
  - Seconds are rounded to a tenth, for each notebook, for a notebook ended after Stop, and for a notebook's own run.
  - Notebooks of a batch run side by side, and a batch file without a parallel count runs one at a time.
  - The heartbeat waits 10 seconds and saves only while the batch runs.
  - The heartbeat and the Stop watcher never hold the process open.
  - A batch exactly seven days old is kept, and one a few seconds older goes.
  - Run in Fabric from Home keeps Fabric's changes unless asked.
  - → `test_notebook_batch_service_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `spark_complex.py` | 80 | `and` to `or` in `_once_arg`'s wrapper check | Equivalent: `_once_arg` is only given what `_once` wrote. That is its own wrapper, or a `CASE` that is neither a bracket nor holds a parenthesis, so both forms find the same part. |
| `spark_complex.py` | 85 | `and` to `or` in `_once_arg`'s field check | Equivalent: `_once_arg` is only called for templates with one part, so the first field is the one named. |
| `spark_complex.py` | 144 | `and` to `or` when picking the `LATERAL VIEW` calls that get the map forms | Equivalent: every other generator here (`inline`, `stack`, `json_tuple`) is already an `UNNEST` of a list, which the typed pass never takes for a map. |
| `spark_complex.py` | 178 | `quote_identifiers=False` to `True` | Equivalent: it quotes names only in the copy that is typed and then dropped; the names still match the lower-case schema. |
| `spark_complex.py` | 178 | `identify=False` to `True` | Equivalent: as above. |
| `spark_complex.py` | 191 | `-1` to `0` as the default id | Equivalent: every marked argument is given an id before the copy is typed, so the default is never read. |
| `spark_complex.py` | 192 | `or` to `and` in "no type" | Equivalent: `annotate_types` gives every node a type, `UNKNOWN` when it can't tell, so the type is never `None`. |
| `spark_complex.py` | 211 | `and` to `or` for the `LATERAL VIEW` map form | Equivalent: the only other node with a map type here is a `reverse` or `sequence` call, and `_lateral_map` only rewrites a node whose parent is a select. A call in a select list has an alias above it. |
| `notebook_batch_service.py` | 131 | `parallel or 1` to `parallel or 0` | Equivalent: `max(1, ...)` raises 0 to 1. |
| `notebook_batch_service.py` | 156 | a missing beat read as `0` to `1` | Equivalent: either is decades before now, so a batch with no beat is lost the same way. |
| `notebook_batch_service.py` | 228 | `missing_ok=True` to `False` for progress files | Differs only if a progress file goes between the listing and its removal, which a test can't arrange without patching the folder listing. |

