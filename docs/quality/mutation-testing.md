---
site_group: Internal engineering
---

# Mutation testing, batch 1

> **Internal — engineering working material.** This is a dated implementation, design, or planning record. Use the user guides for current instructions and check releases for shipped behavior.

How strong are the tests on the modules where a quiet bug costs the most? Line coverage says a line
ran; mutation testing says whether a test would notice if that line were wrong. This batch covers the
local Delta write path, the OneLake write queue, the local writes clear-up, the retry rules and the
redactor. Run at `main` 68b298c3f (2026-11-11).

## Method

- **Mutator.** A small AST mutator (about 130 lines of Python, kept outside the repository so it adds
  no dependency). For each mutation point it rewrites the module with one change, runs the module's
  own test files with `pytest -x` and a 120 s timeout, and restores the file. mutmut was not used:
  it doesn't fit the sidecar layout (package under `sidecar/`, tests importing `fabric_desktop`
  from `PYTHONPATH`) without configuration that would live in the repository.
- **Mutations.** Comparison flips (`==`/`!=`, `<`/`>=`, `>`/`<=`, `is`/`is not`, `in`/`not in`),
  `and`/`or` swaps, negated `if`/`while`/conditional expressions, integer and boolean constants
  (0↔1, n→n+1, True↔False), a return value dropped to `None`, a `raise` removed, and `+`/`-`,
  `*`/`//` swaps. Docstrings are never mutated. Regular expressions are not mutated by this tool, so
  the redactor got a second run that deletes each secret pattern in turn (below).
- **Sampling.** At most 150 mutants a module, sampled with a fixed seed (7), so a re-run mutates the
  same points. Modules with fewer points are run in full.
- **Tests run against each mutant.** Only the module's own test files, listed in the table. A
  mutant that another file in the suite would catch still counts as surviving here: the point is
  whether the tests *about* the module pin its behavior.
- **Score** = (killed + timed out) / mutants run. A mutant that hangs the tests has been noticed,
  so a timeout counts as killed.
- **After.** The new tests were added to each module's set and the same sample re-run. The last
  few survivors in localdata/delta and retrying were then re-checked one by one after their final
  tests went in. Adding a test can't revive a killed mutant, so those re-checks give the final
  count.

## Results

| Module | Test files run | Mutants (of points) | Before: killed + timeout / survived | Before | After: killed + timeout / survived | After | Equivalent left |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `redact.py` | test_redact, test_redactor_characterization, test_secret_detectors_userinfo, test_git_secrets_shapes, test_secrets_at_rest | 58 (58) | 58 / 0 | **100%** | no change needed | **100%** | 0 |
| `redact.py` `_SECRETS` (each pattern deleted) | same | 31 (31) | 31 / 0 | **100%** | no change needed | **100%** | 0 |
| `retrying.py` | test_retrying, test_retrying_more_edges, test_retry_after, test_remote_retry (+ test_retrying_mutation) | 150 (166) | 106 / 44 | **70.7%** | 149 / 1 | **99.3%** | 1 |
| `localdata/delta.py` | test_append_conversions, test_conversions_everywhere, test_local_delta_types, test_parse_errors_plain (+ test_localdata_delta_mutation) | 150 (322) | 115 / 35 | **76.7%** | 145 / 5 | **96.7%** | 5 |
| `write_queue.py` | test_write_queue_edges, test_onelake_writes (+ test_write_queue_mutation) | 94 (94) | 77 / 17 | **81.9%** | 91 / 3 | **96.8%** | 3 |
| `local_writes_service.py` | test_local_writes_clear, test_local_writes_service_edges, test_retention (+ test_local_writes_service_mutation) | 76 (76) | 69 / 7 | **90.8%** | 74 / 2 | **97.4%** | 2 |

Every survivor still left after this batch is an equivalent mutant (listed below): no change to
the code would make a test fail on it, because the mutant behaves the same as the original.

### What the survivors were, and the tests that now kill them

- **retrying.py (44).** The predicates that decide what is safe to send or write again had no direct
  tests in the module's own files: the Windows Delta log race (`os error 5`/`32` on `_delta_log`,
  and its staged `#n` commit form), a dropped data-file upload versus a dropped log commit, an
  expired write token, `is_retryable` and `status_of`. The exact transient status sets
  (`{408, 429, 500, 502, 503, 504}` and `{429, 502, 503, 504}`) and the backoff arithmetic
  (doubling, the cap, jitter scaling between half and full, the total cap on remote waits and on
  dropped connections) could also change unnoticed. `local_retry` could lose its attempt count, its
  waits or its "give up" raise. A wrong "retry" here writes an append twice; a wrong "don't" fails
  a write that would have gone in. → `tests/test_retrying_mutation.py`.
- **localdata/delta.py (35).** Name handling (`unquote` of doubled quotes, `split_name` of a
  three-part name, the reserved `__fd_` prefix), the text parser's OVERWRITE/append and BY NAME
  answers, the `CREATE ... USING DELTA` without `AS` refusal, `AS OF` placeholder numbering and
  aliasing, the 38-digit decimal limit, nullable columns in the Fabric-safe schema, an all-NULL
  column taking the table's type, `_assignable` for dates, lists, maps and structs, the order of
  `fits` problems, and `Conform`/`Counter`. → `tests/test_localdata_delta_mutation.py`.
- **write_queue.py (17).** The default of three uploads at once; a new queue holding zero bytes (one
  that started at 1 would make the first write as large as the cap wait forever); the fingerprint of
  no rows and its 32-character commit form; a write submitted while the worker idles; worker threads
  being daemons; a spill making its folder and removing its file without logging a failure; stats
  rounding. → `tests/test_write_queue_mutation.py`.
- **local_writes_service.py (7).** `pid_running` on POSIX must send signal 0 (any other number would
  signal, or end, someone else's process) and must answer False for a process that is gone; on
  Windows kernel32 must be loaded with `use_last_error=True` or the "access denied means it's running"
  check reads a stale error. "Reveal" must make a missing staging folder with its parents.
  → `tests/test_local_writes_service_mutation.py`.

### Equivalent mutants (left on purpose)

| Module | Line | Mutant | Why it can't be caught |
| --- | --- | --- | --- |
| retrying.py | 58 | `return min(default, cap)` after `when is None` dropped | `parsedate_to_datetime` raises on a bad date in every Python the app supports; it never returns None, so the line can't run. |
| localdata/delta.py | 175 | `isinstance(target, exp.Schema) and ...` → `or` | A plain `exp.Table` target's `this` is an identifier, never a table, so both forms give the same answer. |
| localdata/delta.py | 273, 287 | `lambda _: False` → `True` | Fallbacks for an older pyarrow without `is_string_view`/`is_list_view`; the installed pyarrow has them, so the fallback never runs. |
| localdata/delta.py | 452 | `and`/`or` in the string/binary clause of `_assignable` | Unreachable: `is_string`/`is_binary` only match `pa.string()`/`pa.binary()`, so a pair that passes is already equal and returned by `src.equals(dst)` first. Dead code, not a bug. |
| localdata/delta.py | 486 | `zip(..., strict=True)` → `strict=False` | A short batch still fails in `RecordBatch.from_arrays` (an `ArrowInvalid`, itself a `ValueError`), so the write stops either way. |
| local_writes_service.py | 41 | `out["newest"] or 0` → `or 1` | Only the first file's time meets the default, and every real mtime is far above 1. |
| local_writes_service.py | 101 | `_usage(path, 0)` → `_usage(path, 1)` | That argument is the time used for ages; `_remove` reads only `bytes`. |
| write_queue.py | 30 | `if TYPE_CHECKING` negated | It only moves the pyarrow import to module load, so nothing a test can see changes. |
| write_queue.py | 227 | `self._running = 0` → `1` | `_running` is never read (a dead attribute). |
| write_queue.py | 245 | `return self._release(nbytes)` → `return None` | `_release` returns None. |

### Real bugs found

None. No surviving mutant showed wrong behavior: each was either untested behavior (now pinned) or
one of the equivalents above. Two pieces of dead code turned up and are noted rather than changed:
`Queue._running` in write_queue.py, and the string/binary clause of `_assignable` in
localdata/delta.py.

### Not in this batch

- `audit_chain.py`: an open pull request (#2305) changes it; it is left for the next batch so the
  tests are written against the code that lands.
- `delta_dml.py`: an open pull request (#2510) changes it; same reason.
- The policy and approval gates are covered by other work and were not touched.

## How to re-run

The mutator is a scratch tool, deliberately not in the repository. Its copy is kept in the
project's shared coverage folder as `mutate.py` (and `mut_entries.py` for the pattern-deletion run).
From a copy of `sidecar/` (a copy, so a run in progress can't leave a mutant in your working tree):

```sh
cd /path/to/copy/of/sidecar
PYTHONPATH=$PWD python mutate.py fabric_desktop/retrying.py 150 7 retrying.json -- \
  tests/test_retrying.py tests/test_retrying_more_edges.py tests/test_retry_after.py \
  tests/test_remote_retry.py tests/test_retrying_mutation.py
PYTHONPATH=$PWD python mut_entries.py fabric_desktop/redact.py _SECRETS redact-entries.json -- \
  tests/test_redact.py tests/test_redactor_characterization.py tests/test_secret_detectors_userinfo.py \
  tests/test_git_secrets_shapes.py tests/test_secrets_at_rest.py
```

Arguments: module, cap, seed, output JSON, then the test files. Each line of output is
`<index> <line> <kind> <change> killed|survived|timeout`, and the JSON has the totals. Keep the
seed at 7 to compare with the table above. One module at a time takes 5 to 25 minutes on four
cores; write_queue is the slowest, because a mutant that stops the queue's workers hangs until the
120 s timeout.
