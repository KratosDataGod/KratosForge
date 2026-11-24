# Mutation testing, batch 24

Three modules, the first three on this batch's list:
- Platform skins: the size of a skin file, how many pages the rail and its labels hold, the limits on descriptions and targets, a label for a command that doesn't exist, which colour pairs a mode must set, the contrast maths, and how a skin is exported (`skins.py`).
- The agent's settings file: the defaults of each section, the lowest value of each number, the shortcut webhook, incident routes, shared schedules, a service principal's secret, and how long names are cut in messages (`agent_config.py`).
- Side by side runs: the saved history, the cores choice, starting a run, polling each cell on this PC, waiting on Fabric, the timings and cost, the upload speed, the tables a notebook writes, and copying a record that is changing (`side_by_side_service.py`).

`appstudio/providers.py` was left for a later batch, because App Studio has open pull requests. Batch 23's modules were left to batch 23.

The run was at `main` 7ce636067 (2026-11-15), with `sidecar/tools/mutate.py` and the same method as batches 1 to 22 (sample cap 150, seed 7), on DuckDB 1.5.6.

Before each run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. Every test that was kept passed both ways, so no run below can score a false 100%. Six tests in `test_skins.py` and two in `test_parity.py` read `app/` or `docs/`, and `test_side_by_side.py::test_examples_in_the_repository_are_the_generated_notebooks` reads `examples/`, so they were left out.

The sampled runs left out some other test files that also use these modules, and those files were then run on the survivors:
- For `agent_config.py`, the incident, shortcut, shared schedule and agent test files killed 28 of the 65 survivors.
- For `side_by_side_service.py`, the other side by side, notebook, OneLake and service test files killed 6 of the 39.

## Results

| Module | Test files run | Mutants | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `skins.py` | test_skins, test_parity (+ test_skins_mutation) | 150 of 173 | 136 / 14 | **90.7%** | 149 / 1 | **99.3%** | 1 equivalent |
| `agent_config.py` | test_agent_config_edges, test_schedule_config, test_agent (+ test_incidents, test_shortcut_watch, test_shared_schedules, test_agent_mutation, test_agent_silent_failures, test_schedule_edges, test_agent_status, test_file_text_one_line, test_agent_config_mutation) | 150 of 267 | 85 / 65 | **56.7%** | 148 / 2 | **98.7%** | 2 equivalent |
| `side_by_side_service.py` | test_side_by_side, test_side_by_side_edges (+ test_side_by_side_cores, test_side_by_side_more_edges, test_json_files, test_onelake_writes, test_notebook_cells, 7 service test files, test_side_by_side_service_mutation) | 150 of 220 | 111 / 39 | **74.0%** | 150 / 0 | **100%** | none |

There were no timeouts. The three new test files pass on DuckDB 1.5.6 and 2.0.0.

### What the survivors were, and the tests that now kill them

- **skins.py**:
  - A file of exactly 64 KB being read and one byte more being refused.
  - The rail and its labels holding 12 pages and not 13, and labels that aren't an object being refused.
  - A description and a target's basis holding 200 characters, and targets holding 4 and not 5, as a list only.
  - A label for an unknown command being one problem, not a crash.
  - A token outside every contrast pair needing no partner in a mode without built-in colours, and setting only the background of a pair naming the missing text token.
  - Short hex colours meaning the same as long ones, and each channel carrying its WCAG weight.
  - An export being two-space JSON in the manifest's own key order.
  - → `test_skins_mutation.py`.
- **agent_config.py**:
  - The pipeline, incidents and shortcut sections being off until written `true`, with their defaults, and three attempts by default.
  - Each of 16 numbers having its default, taking its lowest value, and refusing anything below it as one problem; a number that isn't one being a problem, not a crash.
  - The problem count in words, and a read config that can't be changed.
  - Shortcut breaks going to their own webhook, else the alerts one.
  - Routes being read with their filters, and a route with a URL, without `webhook_env`, with an unknown key, without a name, named `default` or not a table each being one problem named by its place.
  - Escalation naming a route that was read.
  - A schedule without an id named by its place, and long ids, environments, certificate paths and project folders cut to 80 or 200 characters in messages.
  - Shared schedules: reading when the mailbox takes their environment, needing the pipeline on, a backfill needing `allow_backfill`, and an id of at most 40 characters.
  - A service principal reading with a secret variable and needing a certificate or a secret variable, and a project path the system refuses not being a project.
  - → `test_agent_config_mutation.py`.
- **side_by_side_service.py**:
  - Fabric being asked every 5 seconds and given up on after 6 hours (with a stand-in clock).
  - A saved cores choice being kept only when it is one of the choices, a good choice being saved and answered with the new card, a bad one saving nothing, and a PC that can't count its cores taking one.
  - A clean history file being read without being set aside, a torn one staying in place when the repair can't write, the history keeping the newest 200, and the list showing the newest 20.
  - A reason a notebook can't run naming its cell when it has one, a save that doesn't name every cell stopping the start, and a started run having a dated id and running on a background thread.
  - A held run getting a kernel process of its own and closing it, each cell being polled from the start with its last output kept, the error message cut to 1,000 characters, and cells numbered from one.
  - The Fabric run's times rounded to the hundredth, its CU seconds and cost per hour of capacity, and a start-up split never below nothing.
  - Upload time and speed rounded to the hundredth, with a speed for an upload under a second.
  - Writes read from code cells only and named from cell one.
  - A record that keeps changing being tried 20 times and then called busy.
  - → `test_side_by_side_service_mutation.py`.

### Left without a test

| Module | Line | Mutant | Why |
| --- | --- | --- | --- |
| `skins.py` | 176 | `return False` dropped in `_command_id` | Equivalent: the only caller that reads the answer tests it with `not`, and `None` is falsy too. |
| `agent_config.py` | 305 | `or` → `and` inside a shared schedule's `every_minutes` check | Equivalent: the checks before it already refuse an `every_minutes` that isn't a whole number from 1. |
| `agent_config.py` | 305 | `minutes < 1` → `minutes < 0` | Equivalent for the same reason: the earlier check refuses 0. |

### Real bugs found

None.

### Not in this batch

`appstudio/providers.py`, and batch 23's modules.

