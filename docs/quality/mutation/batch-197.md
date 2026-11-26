# Mutation testing, batch 197: the data health service

The service side of Data health and day-to-day data engineering (`dataops_service.py`): the tables each PC watches and the alerts acknowledged or snoozed there, one table's health from its Delta log, compare before a promote, meaningful diffs, schema drift, quarantine replay, alert settings and Teams cards.

The run was at `main` bdf2c177 (2026-11-19), with the method of batch 142. The module has 320 mutants, so 150 were sampled. No `-k` filter or `--deselect` was needed. The tests of the two-minute list limit and of a hung check set the limit to under a second, so a mutant that made a run hang would have been stopped by the tool's 120 s timeout and counted as killed; no mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `dataops_service.py` | test_data_health, test_data_health_mutation, test_dataops, test_dataops_edges (+ test_dataops_service_mutation) | 150 of 320 (146 + 4) | 112 / 38 | **74.7%** | 111/146 → 145/146 | 1/4 → 4/4 | 149 / 1 | **99.3%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **dataops_service.py**:
  - The Data health list is checked within 2 minutes, four tables at a time, and a check that doesn't finish says "didn't finish within 2 minutes". A check still waiting for a thread when the time is up is never started.
  - `_dh_duration` says whole minutes only for a whole number of minutes: 90 s stays "90 s".
  - A note that the list started again is shown for just under 7 days, and only when it has a time and a copy's name.
  - Watched entries without an entity or environment, and quiet records that aren't objects, are left out.
  - A save holds this process's lock and a file lock waited on for 10 seconds, and a lock that isn't given up is one `TimeoutError` with its own words.
  - The list, acknowledging and snoozing check the environment, kind and snooze in these words; without a project the list says `no_project`.
  - Quiet alerts are forgotten only for a table that read, in that environment, and nothing is saved when none are.
  - One table's health uses its freshness promise only in the environments the promise names.
  - A diff against the last commit says when the project isn't in Git, and one against an environment says when nothing was promoted there; a committed entity file without a name is named after its file; anything else is refused in these words.
  - The promote impact lists only removed tables as removed.
  - A table of exactly the row limit is still compared in full.
  - Versions give their time in UTC from milliseconds, and none when the log has no time.
  - Drift on an entity source reads no rows.
  - Quarantined rows come newest first, undated last, and checks by count; an emptied value is replayed as no value.
  - Row counts from the statistics leave out the rows deletion vectors remove.
  - Setting alerts returns what they now are, and an unreadable address says so.
  - A Teams card wraps each line and says "and N more" from the 21st line; Teams posts wait 5 s to connect and 15 s to read, an answer of 300 or more is an error, and Teams' own error is cut to 200 characters.
  - → `test_dataops_service_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `dataops_service.py` | 49 | `get_add_actions(flatten=True)` to `False` | Equivalent: `num_records` is a top-level column either way; flattening only splits the per-column statistics, which this function doesn't read. |

