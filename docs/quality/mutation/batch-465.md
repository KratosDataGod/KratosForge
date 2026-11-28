# Mutation testing, batch 465

One module not scored before, run with `--kinds all`:
- The KratosForge Environment manifest: which resources make up one environment, by id, checked field by field (ids, never names; no credentials; an owner by Entra object id only), opened read-only when a newer app wrote it, updated with unknown keys kept, and written as stable bytes (`controlplane/manifest.py`).

The run was at `main` a73ebb71 (2026-11-24), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out for that reason. The module has 231 mutants, so a sample of 150 was run. `main` hasn't changed this module since.

The test set was test_controlplane, test_controlplane_hardening and test_controlplane_service.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0. The "after" run reran the survivors with the new tests added. Four survivors that came late in the "before" run were missed by the first "after" run; a second run checked them, and the table counts both.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `controlplane/manifest.py` | test_controlplane, test_controlplane_hardening, test_controlplane_service (+ test_controlplane_manifest_mutation_b) | 150 of 231 (144 + 6) | 123 / 27 | **82.0%** | 119/144 → 140/144 | 4/6 → 5/6 | 145 / 5 | **96.7%** | 5 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **controlplane/manifest.py**:
  - A name of 120 characters is kept and 121 is refused. A failed migration's step of 400 characters is kept and 401 is refused.
  - A manifest file of exactly 64 KB is read and one byte more is refused, naming 64 KB.
  - A local environment takes folder-safe ids. An empty id, one with a space, one that starts with `-`, one of 65 characters or one that isn't text is refused. An optional local id may be empty, but a bad one is still refused.
  - A key that isn't text is refused, naming where it is.
  - A plural four-letter credential word ("pwds") is refused as a key.
  - The operational schema version starts at 1: 1 is kept and 0 is refused.
  - An update keeps the minimum app version and sets the version that wrote it.
  - The summary names the workspace by id and label.
  - The bytes are sorted keys, two-space indents and UTF-8 text (not `\u` escapes).
  - A request with nothing wrong gives empty text.
  - → `test_controlplane_manifest_mutation_b.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `controlplane/manifest.py` | 78 | `return False` to `return None` in `_secret_key` for an `_id` key | Equivalent: the result is only read by `if _secret_key(k)`, and None is as false as False. |
| `controlplane/manifest.py` | 115 | `return ""` to `return None` in `_text` for an empty optional value | Equivalent: no caller reads what `_text` returns; it only raises or not. |
| `controlplane/manifest.py` | 123 | `return ""` to `return None` in `_guid` for an empty optional value | Equivalent: no caller reads what `_guid` returns. |
| `controlplane/manifest.py` | 196 | `analytical_store.get("schemas", True)` to `False` | Equivalent: the default is only checked for being a Boolean, and both are. |
| `controlplane/manifest.py` | 80 | `len(w) > 3` to `>= 3` when a plural `s` is dropped | Equivalent: a three-letter word loses its `s` and becomes two letters, and no credential word has two letters. The word itself is still checked as written. |
