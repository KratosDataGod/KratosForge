# Mutation testing, batch 167

A boundary re-score, as in batch 163, of six more modules first scored before batch 36. Only the boundary mutants were run (`--kinds boundary`); each module's point score from its first batch stands. The modules, with the batch that first scored each:
- Structured Streaming on this PC (`spark_stream.py`, batch 20).
- The capability registry for external tools (`integrations/registry.py`, batch 25).
- The Shortcut Center's health checks (`shortcut_center.py`, batch 25).
- The notebook dependency graph (`depgraph.py`, batch 26).
- App Studio patches (`appstudio/patch.py`, batch 29).
- Notebook validation (`validate.py`, batch 32).

The run was at `main` 4ddd3662 (2026-11-18) with batches 163 to 166's tests added, and the same method as batch 163. For `integrations/registry.py` only its own three test files were run: with all 29 files that use it, the unmutated run took over the 120 s limit, so no mutant could be scored.

Before each "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. No mutant's run timed out.

## Results

| Module | Test files run | Boundary mutants | Before: killed / survived | Before | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `shortcut_center.py` | the 6 test files that use it (+ test_shortcut_center_boundary) | 4 of 4 | 3 / 1 | **75.0%** | 4 / 0 | **100%** | none |
| `depgraph.py` | the 5 test files that use it (+ test_depgraph_boundary) | 4 of 4 | 3 / 1 | **75.0%** | 4 / 0 | **100%** | none |
| `appstudio/patch.py` | test_appstudio, test_appstudio_patch_edges, test_appstudio_patch_mutation, test_appstudio_pbir | 4 of 4 | 4 / 0 | **100%** | 4 / 0 | **100%** | none |
| `spark_stream.py` | test_regex_anchors, test_spark_stream_edges, test_spark_stream_mutation, test_startup_imports (+ test_spark_stream_boundary) | 3 of 3 | 1 / 2 | **33.3%** | 2 / 1 | **66.7%** | 1 equivalent |
| `integrations/registry.py` | test_integrations_registry, test_integrations_registry_edges, test_integrations_registry_mutation | 3 of 3 | 3 / 0 | **100%** | 3 / 0 | **100%** | none |
| `validate.py` | the 5 test files that use it | 3 of 3 | 3 / 0 | **100%** | 3 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **shortcut_center.py**: a probe answering 300 isn't healthy. → `test_shortcut_center_boundary.py`.
- **depgraph.py**: `open()` with only a path reads, so it isn't a write. → `test_depgraph_boundary.py`.
- **spark_stream.py**: a second run with no new commit reads nothing and says so. → `test_spark_stream_boundary.py`.

`appstudio/patch.py`, `integrations/registry.py` and `validate.py` had no survivors.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `spark_stream.py` | 149 | `len(tables) > 1` to `>=` | Equivalent: `pa.concat_tables` of one table gives that table. |

