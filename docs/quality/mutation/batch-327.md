# Mutation testing, batch 327

One module, run uncapped: every mutant, not a sample. This is the first mutation run of this module.
- Secret values a notebook read, shown as `[REDACTED]` in its output and in the kernel's stderr (`secret_redaction.py`).

The run was at `main` ea988e99e (2026-11-20), with `sidecar/tools/mutate.py` as it is since #2902, and `--cap 100000 --seed 7 --kinds all`, so all 75 of 75 mutants ran (71 point + 4 boundary). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. Each pytest session got a new temporary folder of its own, removed when the session ended (a small plugin, not committed).

The set is test_secret_leaks, test_secret_redaction and test_kernel_host_coverage, about 5 s a session, with test_secret_leaks first: run after test_secret_redaction, its `test_the_overflow_file_never_holds_a_secret_printed_in_pieces` counts the overflow files the earlier file's kernels left in `notebook._spills` too (25 where it wants 5) and fails, on the unmutated module as well. That is an order dependence in the tests, not in the module. The score counts only the files given. Before the "after" run, its tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it, as in batch 11, on DuckDB 1.5.6 and 2.0.0. They passed every way. No `-k` filter was used, and no mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `secret_redaction.py` | test_secret_leaks, test_secret_redaction, test_kernel_host_coverage (+ test_secret_redaction_mutation) | 75 of 75 (71 + 4) | 50 / 25 | **66.7%** | 49/71 → 66/71 | 1/4 → 3/4 | 69 / 6 | **92.0%** | 6 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **secret_redaction.py**:
  - A longer value that holds a shorter one is replaced whole, also when the shorter one was remembered first (the existing test's longer value didn't start with the shorter one, so either order replaced it whole).
  - A value is replaced as its UTF-8 bytes' repr prints it, from its first character.
  - partial_tail waits for a one-character start of a value, and not for a whole value that ends the text.
  - longest() is 0 with no values.
  - RedactedStream: a line goes out whole with no value remembered; a part line is held up to HOLD (65,536) characters and goes out past it, and only what is past the last newline counts; closing writes out what is held and leaves the stream under it open; it is writable, not a TTY, and gives the stream's encoding (utf-8 when it has none) and file number. `write` returns the length written.
  - → `test_secret_redaction_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `secret_redaction.py` | 44 | `return set()` → `return None` for a value that isn't text or bytes | Equivalent: remember reads it only with `not forms`. |
| `secret_redaction.py` | 56 | `forms <= _values` → `<` | Equivalent: when they are equal, the values are updated with nothing new and the same pattern is compiled again. |
| `secret_redaction.py` | 72 (2) | the longest tail tried, `max(...) - 1` → `+ 1` or `- 0` | Equivalent: a tail as long as a value or longer fails `len(v) > n` anyway. |
| `secret_redaction.py` | 72 | `max(..., default=1)` → `default=0` | Equivalent: with no values the range is empty either way. |
| `secret_redaction.py` | 174 | `if not self.closed: self.flush()` → `if self.closed:` | Equivalent: `io.TextIOBase.close`, called next, flushes a stream that isn't closed, which writes out what is held. |
