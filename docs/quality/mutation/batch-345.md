# Mutation testing, batch 345

One module, and the first mutation run of it: no earlier batch scored it. Every mutant was run, not a sample of 150 (`--cap 100000 --seed 7 --kinds all`). The module has no `<` or `>` comparisons, so it has no boundary mutants.
- Extensions for the AI integrations: the checks an extension must pass, the host that turns extensions on and off, and the built-in notebook outline sample (`integrations/extensions.py`).

The run was at `main` 447143424 (2026-11-23), with `sidecar/tools/mutate.py` as it is since #2902. The module has 115 mutants, and the run ran all 115 of 115 (uncapped). Test files were given as paths relative to `sidecar/`, and each was checked to exist before the run. The set is the test files that call `integrations/extensions.py`: test_integrations_extensions, test_integrations_tool_guards and test_integrations_workspace_scope, about 16 seconds together. test_integrations_docs_examples also imports it, but it reads `docs/ai/*.md`, which the tool's copy of `sidecar/` doesn't have, so that file was left out.

Each pytest session got a fresh temporary folder of its own, as in batch 331. After the run no folder in it was nested deeper than 40 levels, and there was no late run of kills. Three kills, picked at random, were checked by hand: each mutant was applied to a copy of `sidecar/` and the set run without `-x`. Each failed real tests (40, 1 and 14).

Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed both ways. No mutant's run timed out. The last survivor (the outline's cell schema) got its test after the first "after" run, so it was re-checked alone with `--only` at `main` 293fb38da; the module was the same there.

## Results

| Module | Test files run | Mutants | Before: killed / survived | Before | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `integrations/extensions.py` | test_integrations_extensions, test_integrations_tool_guards, test_integrations_workspace_scope (+ test_integrations_extensions_mutation) | 115 of 115, uncapped | 101 / 14 | **87.8%** | 115 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **integrations/extensions.py**:
  - An extension is not built in unless it says so, and can't be changed once made. Two extensions compare without their capability builders.
  - An id that would give the core prefix is refused by name, and so is a capability that isn't a `Capability` object.
  - `describe()` lists every extension. Turning on an unknown one is refused by name.
  - A load that fails keeps a readable error: a `ValueError`'s own text, or another error's type and text.
  - The outline numbers cells from 1 and cuts headings at 200 characters. It takes a name of 1 to 200 characters, stops after 30 seconds, and tells tools a cell may carry more keys than it lists.
  - → `test_integrations_extensions_mutation.py`.

### Left

None.
