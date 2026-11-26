# Mutation testing, batch 56

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- What each agent publishes about itself: the status record, its incident and shortcut summaries, and writing `agents/<name>.json` (`agent_status.py`).
- The semantic model metadata App Studio may describe: tables, columns, measures and relationships, with caps and cleaned text (`appstudio/discovery.py`).
- Reading a Dataflow Gen2 definition: splitting the M section into statements, each query's attributes, and where it loads (`orchestration/mashup.py`).

The run was at `main` 7bd752a5d (2026-11-16), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The new tests pass on DuckDB 1.5.6 and 2.0.0. Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No test in these sets reads `docs/` or `examples/`, and no `-k` filter was used. `orchestration/mashup.py` has 168 mutants, so the cap of 150 applies; six of those hang (a scanner that no longer moves forward) and count as caught, as in earlier batches.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed + timeout / survived | Before | Point before → after | Boundary before → after | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `agent_status.py` | test_agent_status, test_agent_fleet, test_agent_silent_failures, test_shared_schedules, test_run_as, test_agent_takeover, test_schedule_overlap (+ test_agent_status_mutation) | 40 of 40 (40 + 0) | 21 / 19 | **52.5%** | 21/40 → 40/40 | none | 40 / 0 | **100%** | none |
| `appstudio/discovery.py` | test_appstudio_discovery, test_appstudio_discovery_edges, test_appstudio_edit, test_appstudio_edit_edges, test_appstudio_generate (+ test_appstudio_discovery_mutation) | 90 of 90 (85 + 5) | 79 / 11 | **87.8%** | 75/85 → 85/85 | 4/5 → 5/5 | 90 / 0 | **100%** | none |
| `orchestration/mashup.py` | test_workspace_dataflows (+ test_mashup_mutation) | 150 of 168 (140 + 10) | 112 / 38 | **74.7%** | 111/140 → 136/140 | 1/10 → 6/10 | 142 / 8 | **94.7%** | 8 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **agent_status.py**:
  - A record reads up to 200 of the environment's jobs and lists the 10 newest finished, newest first; a job with no finish time sorts as the oldest.
  - A job's error is cut to 300 characters, and an empty one reads as none.
  - The pipeline shows as enabled only when it is on and serves this environment.
  - Counters an older agent leaves out read as 0, and the waiting count as unknown.
  - The incident summary keeps each route but not the name of its webhook variable, and gives its tuples as lists.
  - Shortcut health is on only when enabled for this environment.
  - `publish` writes two-space JSON under `agents/`, replaces with the current etag, and gives up after three tries.
  - → `test_agent_status_mutation.py`.
- **appstudio/discovery.py**:
  - The caps (300 tables, 4000 columns, 2000 measures, 1000 relationships) and text limits (128, 300, 2000 characters) are pinned, and exactly each cap is read whole while one more is cut and marked.
  - Text of exactly its limit is kept whole; longer text is cut to the limit with an ellipsis.
  - Table, column and measure descriptions leave out emails and user names in paths.
  - A measure expression is cleaned of credential-shaped text only, and otherwise kept as written.
  - → `test_appstudio_discovery_mutation.py`.
- **orchestration/mashup.py**:
  - Statements split from the first character, and blank text after the last semicolon isn't a statement.
  - A semicolon inside a string, a line comment, a block comment or brackets doesn't split; doubled quotes stay in a string, and a stray close bracket doesn't go below zero.
  - Leading spaces and comments are skipped before a query's attributes, and an unclosed comment leaves nothing.
  - The attribute record ends at its own close bracket, with brackets and quotes inside strings (including an empty string and doubled quotes) read as text; an unclosed record or string has no attributes.
  - Quoted query names keep doubled quotes, and what isn't `shared` is skipped.
  - A destination reads its target query's kind, workspace, item, schema, table, update method and new-table flag; an unknown function reads as "Unknown" and a missing query as nothing.
  - Connection ids come from JSON or plain text, and a query loads and shows unless its flags say otherwise.
  - → `test_mashup_mutation.py`.

### Left

| Module | Line | Mutant | Why it stays |
| --- | --- | --- | --- |
| `orchestration/mashup.py` | 51 | `end < 0` to `end < 1`, and to `end <= 0` | Equivalent: a line comment starts with `//` at `i`, so the newline found from `i` is never at 0. |
| `orchestration/mashup.py` | 54 | `end < 0` to `end <= 0` | Equivalent: the close is searched from `i + 2`, so it is never found at 0. |
| `orchestration/mashup.py` | 73 | `end <= 0` to `end <= 1` | Equivalent: the text starts with `/`, so a newline or `*/` is never found at 0, and `end` is never 1. |
| `orchestration/mashup.py` | 86 | `i >= 0` to `i >= 1`, and to `i > 0` | Equivalent: the closing quote is searched from after the opening one, so it is never found at 0. |
| `orchestration/mashup.py` | 86 | `i >= 0` to `i < 0` (the doubled-quote loop never runs) | Equivalent: without the loop, the second quote of a pair opens a new string at once, and the scan ends at the same place. |
| `orchestration/mashup.py` | 88 | `i < 0` to `i <= 0` | Equivalent: as on line 86, a found quote is never at 0. |

