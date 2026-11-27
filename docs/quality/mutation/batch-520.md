# Mutation testing, batch 520

Four modules, run with `--kinds all`:
- Whether the policy file and Group Policy's key can be trusted with settings that widen what KratosForge trusts: owned by SYSTEM or Administrators, and no one else granted a right to change them (`policy_trust.py`). Not scored before.
- The hash chain both audit trails use: appending under a lock, `head.json`, the Windows event log record, pruning old months, and exports IT can check on their own (`audit_chain.py`). Not scored before: batches 1 to 7 left it while an open pull request changed it.
- What a SQL query page run is allowed to do, by class of statement, on the lakehouse and the local target (`sql_policy.py`). Scored in batch 6 (130 mutants then, 133 now).
- Secret values a notebook read, shown as `[REDACTED]`, and the helpers for values kept elsewhere, `redact_values` and `mask_cut` (`secret_redaction.py`). Scored in batch 327; it has gained those two helpers since.

The run was at branch `claude/project-thread-y36q98-wr420`, on 1d8e6b0bf (2026-11-26, which adds edge tests for policy_trust and central policy; test_policy_trust_windows_paths is counted here), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because other runs shared the machine. One mutant's run timed out (audit_chain, line 54: `_TAIL = 64 // 1024`, so the search for a file's last line never ends), and it counts as killed. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy.

The test files are the ones grep finds naming the module, less those about another module that only call it in passing. Two tests read files outside `sidecar/` and fail in the copy whatever the mutant, so they were left out: test_sql_exec's `test_service_calls_are_wired_and_audited_as_intended` (reads `app/`), deselected, and test_clock_skew (reads `app/` when it is collected). audit_chain has 415 mutants, so 150 were sampled; the other three modules were run in full. The "after" figures for policy_trust and secret_redaction are full reruns; for audit_chain and sql_policy the survivors were re-run with `--only`, since adding a test can't revive a killed mutant.

Before the "after" run, the new tests passed in a copy of `sidecar/` on their own, in a shuffled and in reversed order, and against the modules as `ast.unparse` writes them.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `policy_trust.py` | test_policy_trust, test_policy_trust_windows_paths (+ test_policy_trust_mutation_520) | 92 of 92 (91 + 1) | 85 / 7 | **92.4%** | 84/91 → 87/91 | 1/1 → 1/1 | 88 / 4 | **95.7%** | 4 equivalent |
| `audit_chain.py` | test_audit_chain, test_audit_chain_cov3, test_audit_tamper, test_audit_fuzz, test_audit_export, test_audit_moves, test_audit_anchor, test_audit_anchor_failures, test_retention_edges (+ test_audit_chain_mutation_520) | 150 of 415 (142 + 8) | 123 / 27 (1 timeout) | **82.0%** | 118/142 → 138/142 | 5/8 → 7/8 | 145 / 5 | **96.7%** | 5 equivalent |
| `sql_policy.py` | test_tsql_contract, test_warehouse_queries, test_sql_exec (+ test_sql_policy_mutation_520) | 133 of 133 (133 + 0) | 112 / 21 | **84.2%** | 112/133 → 129/133 | none | 129 / 4 | **97.0%** | 4 equivalent |
| `secret_redaction.py` | test_secret_leaks, test_secret_redaction, test_kernel_host_coverage, test_secret_redaction_mutation, test_variables_secrets (+ test_secret_redaction_mutation_520) | 93 of 93 (88 + 5) | 81 / 12 | **87.1%** | 78/88 → 82/88 | 3/5 → 3/5 | 85 / 8 | **91.4%** | 8 equivalent |

secret_redaction stays under 95%: its last eight survivors are equivalent (below), so 91.4% is as high as its sample can go.

No bugs were found.

### What the survivors were, and the tests that now kill them

- **policy_trust.py**:
  - The registry rights have the values Windows defines (`KEY_SET_VALUE` 0x2, `KEY_CREATE_SUB_KEY` 0x4) and `INHERIT_ONLY` is 0x8.
  - Users reading Group Policy's key and its parent (`KEY_READ`) is trusted; setting values or adding subkeys isn't.
  - An entry that children inherit (object or container inherit, no propagate) still applies to the path itself; only an inherit-only entry leaves the path itself out.
  - → `test_policy_trust_mutation_520.py`.
- **audit_chain.py**:
  - A line whose time is earlier than the line before it is noted only past five minutes (300 s isn't noted, 302 s is), and how far back is said in minutes up to 119, in hours from 120 minutes to 47 hours, and in days from 48 hours; one minute is "1 minute". A later line is judged against the latest time before it, not the earlier one just noted.
  - An export refuses to write over an earlier export or its manifest, either one alone, unless told to replace it, and makes the folders it goes in.
  - A manifest that isn't an export manifest checks no line (`lines` is 0).
  - A file a power cut left without its last newline, even one byte long, is kept as it is and the next line starts on its own; the line `head.json` names is still there, so nothing is recorded as lost. The same holds when every line is gone and the same line is written again at the same place.
  - A prune of several months starts the trail after the last line of the newest month deleted, and a head that counted fewer lines than a prune deletes counts none left, not a negative number.
  - Against the event log after a prune: a record of the trail's start that matches `base` is no problem, one of another start is reported, and a trail that ends before the record counts its pruned lines in "the trail has".
  - → `test_audit_chain_mutation_520.py`, with the event log in memory (conftest's `event_log`); nothing is written to this PC's.
- **sql_policy.py**:
  - On the local target, DROP, TRUNCATE and DELETE without WHERE are refused when `confirmed` is left out, and run with it; an INSERT needs no confirming. A verdict can't be changed once made.
  - Each local change names its table and what it removes (DROP VIEW says VIEW), and on the lakehouse a refusal names the table, or the kind of CREATE, or the command.
  - USE and RESET change only the run's session, in DuckDB and Spark SQL.
  - On the local target only SET, RESET, PRAGMA, LOAD and INSTALL are called settings: VACUUM isn't run, for its own reason.
  - A table in a schema (`main.sales`) and a quoted table name (`"sales"`) are the project's own; a quoted path (`'data/x.csv'`) and Spark's `parquet.`...`` read a file.
  - → `test_sql_policy_mutation_520.py`.
- **secret_redaction.py**:
  - `redact_values` and `mask_cut` try the longest value first, whatever order the values come in, so a value that holds another, or a cut inside a long value that a shorter value also starts in, is replaced whole.
  - `mask_cut` acts only on a cut inside a value: a whole value before the "..." is `redact_values`' job, which the preview does first.
  - → `test_secret_redaction_mutation_520.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `policy_trust.py` | 37 | `range(12, 22)` → `range(12, 23)` in `GRANTS_NOTHING` | Equivalent: a type past 21 is refused by the `LAST_ACE_TYPE` check before `GRANTS_NOTHING` is read. |
| `policy_trust.py` | 130 | `0x1 \| 0x4` → `0x1 \| 0x5` | Equivalent: both are 0x5 (owner and DACL). |
| `policy_trust.py` | 144 | the mask of an entry that isn't an allow entry, `0` → `1` | Equivalent: `judge` reads the mask only of an allow entry. |
| `policy_trust.py` | 145 | the "is admin" of an entry that isn't an allow entry, `False` → `True` | Equivalent: as above. |
| `audit_chain.py` | 95 | `f.read(end - start)` → `f.read(end + start)` | Equivalent: reading past the end of the file returns what is there. |
| `audit_chain.py` | 291 | `every[-1]` → `every[0]` when no file is from this month or earlier | Equivalent: then every file is named after a later month, which hasn't ended, so none is pruned whichever is kept. |
| `audit_chain.py` | 414 (2) | `writes >= 0` → `writes >= 1` or `writes > 0` | Equivalent: a count of 0 reads as 0 either way. |
| `audit_chain.py` | 492 | `1 << 20` → `1 << 21` (the block size `_sha256` reads) | Equivalent: the digest doesn't depend on the block size. |
| `sql_policy.py` | 127 | `return ""` → `return None` in `_reads_files` | Equivalent: its one caller reads it only as true or false. |
| `sql_policy.py` | 179 | `return ""` → `return None` in `_name` | Equivalent for SQL sqlglot parses: every CREATE and write it builds names a table; a statement that names none is a Command, which doesn't reach `_name`. |
| `sql_policy.py` | 217 (2) | `word in _FABRIC_COMMANDS` negated, or `in` → `not in` | Equivalent: both branches say "<word> isn't run here", and differ only for a command with no word, which sqlglot never makes. |
| `secret_redaction.py` | 44 | `return set()` → `return None` for a value that isn't text or bytes | Equivalent, as in batch 327: remember reads it only with `not forms`. |
| `secret_redaction.py` | 56 | `forms <= _values` → `<` | Equivalent, as in batch 327. |
| `secret_redaction.py` | 72 (3) | the longest tail tried, `max(...) - 1` → `+ 1` or `- 0`, and `default=1` → `default=0` | Equivalent, as in batch 327. |
| `secret_redaction.py` | 106 (2) | `at < 0` → `at < 1` or `at <= 0` | Equivalent: with "..." at the start there is nothing before the cut, so no value's start can end there and the text comes back as it is either way. |
| `secret_redaction.py` | 197 | `if not self.closed: self.flush()` → `if self.closed:` | Equivalent, as in batch 327: `io.TextIOBase.close` flushes a stream that isn't closed. |
