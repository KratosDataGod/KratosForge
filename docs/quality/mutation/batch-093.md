# Mutation testing, batch 93

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- Fabric hints, query labels and statistics statements in local T-SQL: the text pass that rewrites what sqlglot can't read, and the tree pass that leaves out or refuses each hint by name (`localdata/tsql_hints.py`).
- The savings waterfall for one run: each optimization's layer, this PC's share, what is left unattributed, gross, overhead and net, and whether the result is complete (`savings/attribution.py`).
- Which Windows account is on the other end of a loopback connection to the local API: the TCP tables, the token's user SID, and the checks built on them (`integrations/peer.py`).

The run was at `main` 7be2906d5 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902: each mutant ran without a bytecode cache, and the tests were first run once on the unmutated copy of `sidecar/`, where they passed. The method is the same as earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. `localdata/tsql_hints.py` has 313 mutants, so 150 were sampled; `savings/attribution.py` (112) and `integrations/peer.py` (145) were run whole.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter or `--deselect` was needed: no test in these sets reads `docs/`, `examples/` or `app/`. `test_editor_channel.py` only replaces `peer`'s check with a stand-in, so it isn't in the `integrations/peer.py` set.

A mutant whose run times out counts as killed, as in earlier batches. Four `localdata/tsql_hints.py` mutants timed out in both runs, each a loop that no longer moves on: `j += 1` to `+= 0` in the OPTION list (line 131), `i += 1` to `+= 0` for a table called `statistics` (line 93) and for a token that isn't a hint (line 105), and the `raise` removed for an unknown statistics option (line 226). Two more time out in the "after" run: `i = end + 1` to `end - 1` and to `end + 0` (line 111), which an OPTION list that isn't closed now reaches.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `localdata/tsql_hints.py` | test_tsql_hints, test_warehouse_queries, test_tsql_time_travel, test_tsql_ctes (+ test_tsql_hints_mutation) | 150 of 313 (139 + 11) | 126 / 24 | **84.0%** | 121/139 → 139/139 | 5/11 → 11/11 | 150 / 0 | **100%** | none |
| `savings/attribution.py` | test_savings_attribution (+ test_savings_attribution_mutation) | 112 of 112 (106 + 6) | 87 / 25 | **77.7%** | 86/106 → 104/106 | 1/6 → 6/6 | 110 / 2 | **98.2%** | 2 equivalent |
| `integrations/peer.py` | test_integrations_peer, test_integrations_peer_calls (+ test_integrations_peer_mutation) | 145 of 145 (145 + 0) | 112 / 33 | **77.2%** | 112/145 → 145/145 | none | 145 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **localdata/tsql_hints.py**:
  - A batch the tokenizer can't read, or an UPDATE STATISTICS whose rest it can't read, is given back as written for the parse to report.
  - PRINT is a command token, but not UPDATE STATISTICS, so a PRINT that mentions statistics is left alone.
  - A batch that ends in OPTION, or in CREATE or DROP, and an OPTION list that isn't closed, are left for the parse.
  - An UPDATE of a table called `[statistics]` is an update, not UPDATE STATISTICS.
  - An OPTION list with nothing to carry is left exactly as written. A carried hint keeps the items beside it as written, one-word items such as RECOMPILE included, before it or after it.
  - CREATE STATISTICS without a name, or that stops after its name or its table, and DROP STATISTICS with nothing after it, are refused with the local engine's message.
  - In the tree pass, MAXRECURSION is kept for the CTE check when a label beside it is left out, an index hint is refused by its name alone (`INDEX`, not `INDEX(ix)`), and a carried plan hint's note names the hint.
  - → `test_tsql_hints_mutation.py`.
- **savings/attribution.py**:
  - A scenario figure, an unknown classification, a run time that isn't a time, a run that ran somewhere other than Fabric or this PC, and a local basis other than original or optimized are each refused in their own words.
  - A Fabric run without a baseline has no local layer, and a run on this PC without one does.
  - An optimization implemented at the very moment of the run applies; one reverted at that moment doesn't.
  - An optimization that changed nothing is an accrual of 0, not a regression, and a saving or a regression under one CU hour is still marked as one.
  - This PC avoiding exactly what remains isn't marked as capped; avoiding more is.
  - An unknown layer leaves the result incomplete, even with the overhead known.
  - Every figure is rounded to six decimals, gross included when it is capped at a baseline that isn't.
  - → `test_savings_attribution_mutation.py`.
- **integrations/peer.py**:
  - The call tests' stand-in for Windows checks the engine against the module's own constants, so a wrong constant passed. The documented Win32 values (address families, table classes, the established state, the loopback address, error codes, access rights and the TokenUser class) are now written out as numbers.
  - Each table is asked for unsorted, with reserved 0, the right family and class, and a 1-byte probe at size 0 first. A table that keeps growing is asked for five times, then refused.
  - Another account's process is opened for limited query only and not inherited; its token is opened for query, and asked its size first with no buffer and length 0.
  - A token without a valid SID is an OSError with errno 0.
  - Only `win32` is checked, and no listener on the port is a plain `False`.
  - → `test_integrations_peer_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `savings/attribution.py` | 58 | the strength of `unknown` from 2 to 3 | Equivalent: the weakest classification is the one with the highest strength, and `unknown` is still the highest. |
| `savings/attribution.py` | 218 | `complete = False` to `True` when the overhead isn't known | Equivalent: the result's `complete` is `complete and o is not None`, which is false whenever this line runs. |

