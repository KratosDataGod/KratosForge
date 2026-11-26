# Mutation testing, batch 323: agent_config.py (every mutant)

One module, run uncapped: every mutant, not a sample of 150. Batch 24 sampled 150 of its then 267 mutants (56.7% before, 98.7% after), and batch 164 ran its 7 boundary mutants (57.1% before, 100% after).
- The headless agent's settings, `agent.toml`: sections, schedules, incident routes, shortcut checks and the checks that refuse a bad file (`agent_config.py`).

The run was at `main` ea988e99e (2026-11-20), with `sidecar/tools/mutate.py` and `--cap 100000 --seed 7 --kinds all`, so all 289 of 289 mutants ran (282 point + 7 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Temporary folders were handled and checked as in batch 320.

The set is the config tests and the agent tests that read `agent.toml` through the module: test_agent_config_boundary, test_agent_config_edges, test_agent_config_mutation, test_agent, test_agent_boundary and test_schedule_config. The other files that use the module (test_shared_schedules, test_incidents, test_shortcut_watch and others) were left out to keep each mutant's run short. The score counts only the files given. No `-k` filter was used. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `agent_config.py` | test_agent_config_boundary, test_agent_config_edges, test_agent_config_mutation, test_agent, test_agent_boundary, test_schedule_config (+ test_agent_config_uncapped) | 289 of 289 (282 + 7) | 266 / 23 | **92.0%** | 259/282 → 277/282 | 7/7 → 7/7 | 284 / 5 | **98.3%** | 5 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **agent_config.py**:
  - A config built in code runs one job at a time and queues up to 1,000, and a schedule built in code isn't shared (the dataclass defaults).
  - `[pipeline]`, `[incidents]` and `[shortcut_health]` can't be changed once read (frozen dataclasses).
  - A list written as a string (`pipeline.environments = "dev"`, a route's `teams = "Sales"`) is named in the error list, not a `TypeError` from the default that `_strings` returns.
  - `schedule = 5` is named, and in `schedule = [5, {...}]` the table is still checked, so its own problem is named too (an `or` to `and` and the negated fallback).
  - A service principal needs both its tenant id and its client id: one of them alone is refused.
  - The agent's `owner` is read, trimmed.
  - Shortcut checks with their own `webhook_env` and no alerts webhook are on (the inner `or` to `and`, and `enabled is True` in the config).
  - A shared schedule whose entities `pipeline.entities` takes is read, and one with an entity it doesn't take is refused.
  - A route name in an error is cut to 80 characters.
  - A project path the system can't look up (5,000 characters, which raises `OSError`) isn't a metadata project.
  - TOML nested too deeply to read is a `ConfigError` (on Python 3.13, `tomllib` raises `RecursionError`; with the `raise` removed, `load` failed with `UnboundLocalError`).
  - → `test_agent_config_uncapped.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `agent_config.py` | 319 | `or` to `and` inside a shared schedule's `every_minutes` check | Equivalent, as in batch 24: the checks before it already refuse an `every_minutes` that isn't a whole number from 1. |
| `agent_config.py` | 319 | `minutes < 1` to `minutes < 0` | Equivalent for the same reason: the earlier check refuses 0. |
| `agent_config.py` | 374 | `_is_file`'s `return False` to `None` | Equivalent: both callers use `not _is_file(...)`, and `None` and `False` are both false. |
| `agent_config.py` | 429 | `pipe.get(key, False)` to `True` in the true-or-false check | Equivalent: the default only goes to `isinstance(..., bool)`, which is true for both. |
| `agent_config.py` | 456 | `inc.get("enabled", False)` to `True` in the true-or-false check | Equivalent for the same reason. |
