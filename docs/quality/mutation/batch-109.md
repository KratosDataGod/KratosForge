# Mutation testing, batch 109

Two modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. The table shows both parts.
- The app's incident calls: the list, the summary, the check and the watch, the agents and queue pages, the product status told to the catalog, and the openings an older app already posted (`incident_service.py`).
- The incident desk: the board's counts, the bell's For you list, reported incidents, who an incident reaches and telling them, and the incident history and measures kept in the central lakehouse (`incident_desk.py`).

The run was at `main` ad1ac4cf8 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902: each mutant ran without a bytecode cache, and the tests were first run once on the unmutated copy of `sidecar/`, where they passed. The method is the same as earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants (96 and 149), so all of them ran. Each test set was run with `-x`, so a mutant's run stops at its first failing test.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter or `--deselect` was needed: no test in these sets reads `docs/`, `examples/` or `app/`. `test_rpc_surface.py` names the desk's calls but reads `app/`, and doesn't call them, so it isn't in the `incident_desk.py` set. `test_dataops.py` calls `incidents_watch` for late and failed tables, so it is in the `incident_service.py` set, as are `test_agent_fleet.py`, `test_workload_queue.py` and `test_run_as.py` for the agents, queue and run-as pages.

No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `incident_service.py` | test_incident_service_paths, test_incident_partial_work, test_incidents, test_product_status, test_dataops, test_agent_fleet, test_workload_queue, test_run_as (+ test_incident_service_mutation) | 96 of 96 (96 + 0) | 73 / 23 | **76.0%** | 73/96 → 95/96 | none | 95 / 1 | **99.0%** | 1 equivalent |
| `incident_desk.py` | test_incident_desk, test_incident_partial_work (+ test_incident_desk_mutation) | 149 of 149 (148 + 1) | 112 / 37 | **75.2%** | 112/148 → 144/148 | 0/1 → 1/1 | 145 / 4 | **97.3%** | 4 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **incident_service.py**:
  - The list shows resolved incidents unless asked not to, the queue looks 24 hours ahead, and a check or the watch tells Teams only when asked (no outbox is opened otherwise).
  - Acknowledging gives back the acknowledged incident.
  - The check reads the latest 2,000 runs, and the queue the last 100 Fabric jobs as last checked, never asking Fabric for fresh statuses.
  - A OneLake environment's control area is read with the silent storage token, and an empty one when there is none.
  - The incidents list says its agents could be read, and agents that can't be read say why in words, not as a state.
  - The queue and run-as pages say whether the Fabric schedules were never read, are being read, failed (with Fabric's words) or were read (with when).
  - A Fabric schedule shows the environment whose workspace it is in.
  - Only this project's assignments set how critical a table is, and an assignment without a criticality sets none.
  - The watch still tells the catalog the product status when an agent checks the environment, and only a live agent that checks incidents leaves the check to it.
  - An agent offline with no other agent live is red.
  - The log says whether the platform will be asked for the product status again.
  - A late table without evidence is posted, even when an older app's record has a key that looks alike.
  - → `test_incident_service_mutation.py`.
- **incident_desk.py**:
  - The board shows resolved incidents unless asked not to, says which incidents were detected and which reported, and counts as mine only my own, not someone else's.
  - The bell puts breached before at risk before due, and missed before at risk, whatever was seen last, and gives each incident's last-seen time.
  - An incident that is gone says so.
  - A platform that keeps no followers, or an older one without the followers call, still names the owners.
  - Impact shows the latest time people were told; telling says which routes it went to; a list of people too long for one note, already told, is noted once.
  - The central lakehouse is read with schemas unless it says otherwise.
  - History before any copy is empty, whether the table is missing by message or by type; a history that can't be read for another reason is an error.
  - Within one second the later stage of handling wins (an unknown state counts as open), and a snapshot with no time never replaces a dated one.
  - History is newest first, with undated incidents last and no bookkeeping columns; it shows 500 by default, at least one and at most 5,000, and one environment's when asked.
  - Measures read up to 5,000 incidents, and say they were capped at 5,000 exactly.
  - The history copy keeps every timeline entry and the workspace it came from.
  - → `test_incident_desk_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `incident_service.py` | 179 | `split(":", 2)` to `split(":", 3)` for an assignment's table | Equivalent: a project's name (the save gate's pattern) and a table's name (a file name on Windows) have no `:`, so a workload id has exactly three parts either way. |
| `incident_desk.py` | 95 | the bell's rank for any other SLA state from 2 to 3 | Equivalent: every ranked state is 0 or 1, so 2 and 3 sort the same. |
| `incident_desk.py` | 237 | the history's rank for `resolved` from 2 to 3 | Equivalent: it is still the highest, so the same row wins. |
| `incident_desk.py` | 239 | `unique(maintain_order=True)` to `False` | Equivalent: the rows are sorted by opening time right after, and that sort doesn't keep the order of ties either way. |
| `incident_desk.py` | 258 | the measures' history limit from 5,000 to 5,001 | Equivalent: the history itself shows at most 5,000. |

