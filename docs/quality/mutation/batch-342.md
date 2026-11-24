# Mutation testing, batch 342

One module, run uncapped: every mutant, not a sample. This is the first mutation run of this module.
- The contract every external caller gets: one error shape, checked inputs, timeouts and cancellation (`integrations/core.py`).

The run was at `main` 71b8c5945 (2026-11-23), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 227 of 227 mutants ran (218 point + 9 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Each pytest session got a new temporary folder of its own, removed when the session ended (a small plugin, not committed).

The set is test_integrations_core, test_integrations_core_edges, test_integrations_tool_guards, test_integrations_registry, test_integrations_registry_mutation, test_integrations_registry_edges, test_integrations_http_mutation and test_retrying, about 9 s a session. Some 30 other files reach the module through the registry; the slow ones (test_integrations_parser_fuzz 60 s, test_integrations_limits 32 s, test_integrations_output_leaks 26 s, test_integrations_hostile_args 22 s) and the service-level ones were left out to keep each mutant's run short. The score counts only the files given. Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed every way. No `-k` filter was used, and no mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `integrations/core.py` | test_integrations_core, test_integrations_core_edges, test_integrations_tool_guards, test_integrations_registry, test_integrations_registry_mutation, test_integrations_registry_edges, test_integrations_http_mutation, test_retrying (+ test_integrations_core_mutation) | 227 of 227 (218 + 9) | 177 / 50 | **78.0%** | 175/218 → 218/218 | 2/9 → 9/9 | 227 / 0 | **100.0%** | none |

## Bugs found

- **A file error's name loses its closing quote.** `error_from` strips quotes from both ends of every not_found message, which is meant for a KeyError's repr. A FileNotFoundError's message ends with the file's quoted name, so it comes out as `No such file or directory: 'x.ipynb` with the closing quote cut off. This only affects how the message looks: no folder is shown and the code is right. Pinned with `xfail(strict=True)` in `test_a_file_errors_name_keeps_both_its_quotes`; not fixed here.

### What the survivors were, and the tests that now kill them

- **integrations/core.py**:
  - `too_deep` is past the limit, not at it. Arguments may nest 64 deep and results 200, and one level more is refused (invalid_input with its problems, too_large).
  - Redaction: a key holding a secret is redacted; a non-text key, None and numbers stay; an object becomes its text, redacted; everything under a field named like a secret is blanked, however deep.
  - A file error names the file and not its folders, and a KeyError reads as its own text, while an error with two parts reads as Python writes it.
  - An IntegrationError is passed on as itself with its details redacted, or emptied when they nest too deep.
  - A 401 from Fabric is permission_denied.
  - Each schema bound at its edge: number and null types, a bool against a minimum, minimum and maximum equal to the value, an empty string and list with no minimum, minItems and maxItems equal to the length.
  - A number that isn't finite is refused by name.
  - Ten problems are named in the message and fifty kept in the details.
  - `$` becomes `\Z` after an escaped character and outside a class only.
  - A Caller can't be changed. Progress is reported, the time left counts down, the deadline is reached at exactly its time (in `check` and in `run_call`, retryable), and a call runs on a daemon thread.
  - → `test_integrations_core_mutation.py`.
