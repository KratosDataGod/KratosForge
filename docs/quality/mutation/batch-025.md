# Mutation testing, batch 25

Three modules, the three on this batch's list:
- The capability registry: the version a capability starts at, when removing one counts as a change, the timeout limit, paging defaults, a preview capability's status, and the message for an unknown resource option (`integrations/registry.py`).
- The optimization analyzers: how cells are normalized, the run history text, which duplicate notebook is kept and which copies count as running, the tables evidence, how copy sources are compared, the wording for each kind of repeated copy, and the SQL scan limit (`opportunities/analyzers.py`).
- The shortcut command center: storage target keys, an unreadable lakehouse, allowed connections, port paths without a schema, cross-environment risk text, accepted risks, needs, counts, the readers gap, CSV cells, and environment twins (`shortcut_center.py`).

`integrations/http_server.py` was left to batch 26, which ran at the same time.

The run was at `main` a09aa1fe0 (2026-11-15), with `sidecar/tools/mutate.py` and the same method as batches 1 to 24 (sample cap 150, seed 7), on DuckDB 1.5.6.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Every test that was kept passed both ways, so no run below can score a false 100%. Six tests read `docs/`, so they were left out of the `integrations/registry.py` survivor run: `test_activity_registry.py::test_example_report_in_docs_is_current`, `test_integrations_conformance.py::test_capability_ids_in_docs_are_real`, and four in `test_integrations_prompts.py` (`test_workflows_page_matches_the_prompts`, `test_connect_page_carries_every_setup_from_the_app`, `test_agent_instructions_name_every_prompt_and_only_real_tools`, `test_kratosforge_skill_names_only_real_tools_and_every_prompt_and_matches_its_page`).

The first `integrations/registry.py` run used its four fastest test files. The 45 survivors were then run against every `test_integrations_*.py` file and `test_activity_registry.py`, which killed 35. Before writing new tests for `shortcut_center.py`, its survivors were run against `test_fail_closed.py`, `test_shortcut_service_mutation.py` and `test_shortcut_service_paths.py`. The last two killed 6; `test_fail_closed.py` killed none.

None of these modules writes files. The new tests, like the others, point `LOCALAPPDATA` and `HOME` at the test's own temporary folder, and use no network and no timing.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `integrations/registry.py` | test_integrations_registry, test_integrations_mcp, test_integrations_permissions, test_integrations_http; survivors also against every other test_integrations_* file and test_activity_registry (+ test_integrations_registry_mutation) | 150 of 235 | 105 / 45 | **70.0%** | 150 / 0 | **100%** | none |
| `opportunities/analyzers.py` | test_opportunities; survivors also against test_opportunities_share and test_optimize_share_analysis (+ test_opportunities_analyzers_mutation) | 150 of 183 | 101 / 49 | **67.3%** | 146 / 4 | **97.3%** | 4 equivalent |
| `shortcut_center.py` | test_shortcut_center, test_shortcut_center_more_edges, test_shortcut_fixes, test_shortcut_request_envs, test_shortcut_request_retries, test_shortcut_service_mutation, test_shortcut_service_paths (+ test_shortcut_center_mutation) | 150 of 287 | 116 / 34 | **77.3%** | 150 / 0 | **100%** | none |

No mutant timed out. The three new test files pass on DuckDB 1.5.6 and 2.0.0.

### What the survivors were, and the tests that now kill them

- **integrations/registry.py**: 35 were killed by existing tests not in the first run, mostly `test_integrations_registry_edges.py`, which takes about a minute, so it isn't run for every mutant. The other 10:
  - A capability's version starting at 1.
  - Removing a capability that isn't there not counting as a change, and removing one that is counting.
  - A timeout of exactly 3,600 seconds being accepted, and 3,600.5 refused.
  - Paging defaulting to 100, and a paged output allowing more fields.
  - A preview capability listed as preview only when the caller could use it.
  - An unknown resource option named with the resource it was given to.
  - Three more, recorded with the security notes.
  - → `test_integrations_registry_mutation.py`.
- **opportunities/analyzers.py**:
  - A cell in another language keeping its language, Python that doesn't parse being compared as text, and a cell with only a docstring, `pass` or a comment counting as no code.
  - The run history text: unread, counted with seconds, and counted with none.
  - Two notebooks with the same code: the title naming the kept one and the count, the risk, the cells counted from the first notebook, a copy with one run counting as running, a copy read with no runs not counting, and a copy with unread history named in the validation.
  - The notebook with the most runs being kept, then the most scheduled runs, then the first by name, with unread history ranking like no runs.
  - Notebooks and pipelines whose definitions weren't read being left out.
  - The tables evidence naming what is read and saying when nothing is written, and the other way round.
  - The fingerprint shown being 16 characters.
  - Repeated copies: one opportunity for the same source and sink, worded for it; repeated extraction worded for different sinks; an expression in the source checked first; descriptions and annotations ignored even inside lists; sources that differ inside a list kept apart; a copy with no sink left out; a pipeline with no runs not counted as removed; and a copy inside a larger pipeline making the saving partial.
  - A full refresh saying whether it is a notebook or a pipeline.
  - SQL rewrites numbered from cell 1, and the SQL scan stopping exactly at 200,000 characters.
  - → `test_opportunities_analyzers_mutation.py`.
- **shortcut_center.py**: 6 were killed by `test_shortcut_service_mutation.py` and `test_shortcut_service_paths.py`. The other 28:
  - A storage target's key being its type and location, lower case.
  - A lakehouse you can't read (401 or 403) not being checked.
  - No allowed-connections list blocking no connection.
  - A port in a lakehouse without a schema being found by both table paths.
  - A storage shortcut having no same-environment target.
  - Cross-environment risk text for Prod reading Dev, and for Dev reading Test.
  - An accepted risk recording who, why and when, with blanks for what is missing.
  - Only waiting shortcut requests you can decide being needs, with "Someone" when nobody is named.
  - Every count, exactly.
  - At most 50 readers listed as missing from the target.
  - The CSV cells for a OneLake shortcut, a storage shortcut, a health check change and a change seen at scan.
  - A workspace the platform puts in an environment getting twins whatever its name, a shared target keeping its workspace name, and a missing target twin naming the target's workspace.
  - → `test_shortcut_center_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `opportunities/analyzers.py` | 176 | `same[0]` → `same[1]` for the lakehouse | Equivalent: every notebook in the group has the same default lakehouse. |
| `opportunities/analyzers.py` | 270 | `sort_keys=True` → `False` | Equivalent: `_canon` already builds every dictionary in key order. |
| `opportunities/analyzers.py` | 363 | `m.group(1)` → `m.group(0)` | Equivalent: the pattern around group 1 is only `\b`, which matches no characters. |
| `opportunities/analyzers.py` | 375 | `scheduled=True` → `False` | Equivalent: only NB007 is switched on, and it isn't one of the scheduled-only rules. |

### Real bugs found

None.

### Not in this batch

`integrations/http_server.py` (batch 26).

