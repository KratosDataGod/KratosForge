# Mutation testing, batch 128

Two modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants together with the earlier kinds:
- App Studio conversational edits: the change schema's limits, how each named change becomes patch operations, and what is left out and why (`appstudio/edit.py`).
- The agent's own credentials: the service principal's key file, a refusal Entra gives for good, and reading a certificate (`credentials.py`).

The run was at `main` 5865db954 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, each was checked to exist, and the tests first passed once on the unmutated copy. Each module has fewer than 150 mutants, so all of them ran. No `-k` filter or `--deselect` was used. Of `test_corporate_network.py`, whose other tests read `app/`, only `test_sign_in_calls_have_a_timeout` and `test_managed_identity_uses_a_session_with_a_timeout` call `credentials.py`, so only those were given, by their node ids. `test_tsql_contract.py` (a live run, skipped without a tenant) and `test_cli_tenant_commands.py` (which replaces `credentials.from_config`) were left out.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. The new tests pass with DuckDB 1.5.6 and 2.0.0.

One mutant of `appstudio/edit.py` timed out and counts as killed: with `n += 1` changed to `n += 0`, a new id that is taken is looked for for ever.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `appstudio/edit.py` | test_appstudio_edit, test_appstudio_edit_edges, test_appstudio_service_edges (+ test_appstudio_edit_mutation) | 136 of 136 (136 + 0) | 96 / 40 | **70.6%** | 96/136 → 136/136 | none | 136 / 0 | **100%** | none |
| `credentials.py` | test_agent, test_agent_edges, test_credential_expiry, test_credentials_config, test_run_as, test_secrets_at_rest, two tests of test_corporate_network (+ test_credentials_mutation) | 82 of 82 (81 + 1) | 76 / 6 | **92.7%** | 75/81 → 81/81 | 1/1 → 1/1 | 82 / 0 | **100%** | none |

### Bugs found

- **appstudio/edit.py**: two components added to the same page in one change overlap. `add_component` places a new component under the page's components as the specification had them (lines 192 and 193, which read `spec["pages"]`), not under the ones added earlier in the same change, so the second goes on the same row as the first. The preview accepts it. `test_an_added_component_goes_under_its_own_pages_components` doesn't pin the second one's row, so a fix won't break it. Not fixed here.

### What the survivors were, and the tests that now kill them

- **appstudio/edit.py**:
  - The change schema's limits: 30 changes, the widths, a summary of 300, titles of 200, at most 3 questions of 300, and no other properties.
  - The instruction names the widths and the target's component types.
  - A component without measures or fields is shown to the model with none.
  - Only 30 changes are read.
  - An added component goes under its own page's components, at the next index, with its field, and a text component takes its type as title and text.
  - A component added to a new page starts at the top, at index 0.
  - Titles and names are cut to their limits (200 for components, 120 for pages and the app), and a measure name is read to 128 characters.
  - What is left out is named within its limits, and nothing usable lists at most ten reasons.
  - The summary and questions are cut and kept; without a summary it is what was done, cut to 300.
  - Metadata without tables, or a table without measures, still allows a change.
  - → `test_appstudio_edit_mutation.py`.
- **credentials.py**:
  - Whether the key file is open answers `False`, never `None`: with no key file, for another kind of identity, a file that can't be read, and on Windows.
  - Only "others may read" makes it open: 644 is, 640 and 641 aren't.
  - A refusal says the agent waits 5 minutes before asking again; another error is no refusal.
  - A PFX without its certificate is refused in words.
  - → `test_credentials_mutation.py`.

### Left

None.

