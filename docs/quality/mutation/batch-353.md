# Mutation testing, batch 353

One module not scored before, run with `--kinds all`:
- Extensions: capabilities that come from outside the core, the checks that keep each to what it declares, the person's switch and the policy, and the built-in notebook outline (`integrations/extensions.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. The module has 115 mutants, so all were run.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0, and against the module as it is on `main` today.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `integrations/extensions.py` | test_integrations_extensions, test_integrations_tool_guards, test_integrations_workspace_scope (+ test_integrations_extensions_mutation) | 115 of 115 (115 + 0) | 101 / 14 | **87.8%** | 101/115 → 115/115 | none | 115 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **integrations/extensions.py**:
  - An extension is a fixed value, compared without its capabilities, and isn't built in unless it says so.
  - An id that makes a `fabric_desktop_` prefix is refused for that reason. The existing test matched "fabric_desktop", which the later capability-id refusal also says, so removing this check passed.
  - A capability that isn't a `Capability` is refused, and so is turning on an extension that isn't there.
  - `describe()` lists every extension.
  - A load that fails keeps a `ValueError`'s message as it is and names any other kind (`RuntimeError: boom`).
  - The outline numbers cells from 1 and cuts a heading at 200 characters; its capability takes a name of 1 to 200 characters, has 30 seconds, and lets a cell carry headings or definitions.
  - → `test_integrations_extensions_mutation.py`.

### Left

None.
