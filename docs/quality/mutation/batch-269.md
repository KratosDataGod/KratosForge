# Mutation testing, batch 269

One module not covered before, run with `--kinds all`:
- Recording the end of an audit trail in the Windows Application event log, and reading it back (`audit_anchor.py`).

The run was at `main` ea988e99 (2026-11-21), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The module has 147 mutants, so all were run. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None reads `docs/`, `examples/` or `app/`, so nothing was left out. The per-mutant timeout was raised from 120 to 600 seconds with a wrapper that sets `mutate.TIMEOUT` (the tool is unchanged), because the machine was shared with two other mutation runs. No mutant's run timed out.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

Most survivors were in the Windows event log writer and reader, which the existing tests never ran off Windows. The new tests run both against a stand-in for advapi32 that fills the buffer it is given and sets the counts through their pointers, as the Win32 functions do. That lets the reader's loops run on any PC.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `audit_anchor.py` | test_audit_anchor (+ test_audit_anchor_mutation) | 147 of 147 (136 + 11) | 55 / 92 | **37.4%** | 55/136 → 119/136 | 0/11 → 9/11 | 128 / 19 | **87.1%** | 16 equivalent, 3 Windows only |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **audit_anchor.py**:
  - The reader opens the Application log, reads it backwards and sequentially with a 64 KB buffer, and always closes it. A log that won't open is an error.
  - A record bigger than the buffer gets a buffer of the size Windows asks for. An "insufficient buffer" answer that asks for no more room than the buffer has is an error.
  - The end of the log ends the read, and any other error fails it.
  - At most `READ_RECORDS` records are counted. A record of exactly the header's size counts, and fewer bytes than a header after the last record don't. A record with no length stops only that buffer. The read stops at its time limit.
  - Records carry the `LfLe` mark after their length, so a length is read from four bytes, not five.
  - A record's text: no strings means no text, whatever follows the count. The string offset is four bytes. Strings that start at the record's very end are no text. Names with an odd byte before the strings are still read. A last string with no end is read whole, and an odd byte after it is dropped.
  - The writer registers the source once and writes information records with event id 1000. A source that won't register is an error, and a failed report lets go of the source and is an error.
  - An anchor can't be changed, and a fingerprint is 32 characters. Only text is parsed.
  - A fresh copy of the module uses this platform's writer and reader and hasn't warned yet. The warning is logged once.
  - Writing starts again exactly when the pause after a failure is over. A read is reused for less than `CACHE_SECONDS` only.
  - → `test_audit_anchor_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `audit_anchor.py` | 86, 91 (×2) | `_last_error` and `_advapi32` return dropped; `use_last_error=True` to `False` | Windows only: off Windows `ctypes` has no `WinDLL` or `get_last_error`, so the tests replace both. |
| `audit_anchor.py` | 71 (×2) | the prefix cut `len(PREFIX) + 1` to `- 1` or `+ 0` | Equivalent: the extra field before the first `;` has no `=`, so it only adds an unused key. |
| `audit_anchor.py` | 141 | the read flags `0x0001 \| 0x0008` to `0x0001 \| 0x0009` | Equivalent: both are 9. |
| `audit_anchor.py` | 145, 146 (×3) | the `read` and `needed` counts start at 1, not 0 | Equivalent: ReadEventLogW sets them on every call. |
| `audit_anchor.py` | 162 | `length <= 0` to `length <= 1` | Equivalent: no record is one byte long. |
| `audit_anchor.py` | 172 | a record's length read from 5 bytes, not 4 | Equivalent here: the longer length only makes the record run to the buffer's end, and its first string still ends at its own NUL. |
| `audit_anchor.py` | 174 (×2) | `len(record) < 56` to `< 57` or `<= 56` | Equivalent: a 56-byte record has no room for strings, so the next check gives `None` anyway. |
| `audit_anchor.py` | 178 (×2) | `56 < at` to `57 < at` or `56 <= at` | Equivalent: with `at` of 56 or 57 there is no source name, so the name check gives `None`. |
| `audit_anchor.py` | 180 (×4) | the odd-byte trim of the names changed | Equivalent: only the source name, the first one, is compared, and every version keeps it whole. |
