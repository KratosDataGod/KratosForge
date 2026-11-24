# Mutation testing, batch 135

One workload module not covered before, run with `--kinds all`, so its score counts the boundary mutants together with the earlier kinds:
- How a notice route sends: checking a Teams, flow or email address, the company mail server's settings, the email itself, sending it encrypted, and posting to a Power Automate flow (`workloads/deliver.py`).

The run was at `main` 5865db95 (2026-11-18), with `sidecar/tools/mutate.py` as it is since #2902, and the method of earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given as paths relative to `sidecar/`, and the tests first passed once on the unmutated copy. The module has fewer than 150 mutants, so all of them ran. No `-k` filter or `--deselect` was needed. Before the "after" run, the new tests were run in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it; they passed both ways. No mutant's run timed out.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `workloads/deliver.py` | test_deliver, test_incidents (+ test_deliver_mutation) | 131 of 131 (125 + 6) | 105 / 26 | **80.2%** | 105/125 → 124/125 | 0/6 → 6/6 | 130 / 1 | **99.2%** | 1 equivalent |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **workloads/deliver.py**:
  - A line is cut to 500 characters, a route sends at most 200 lines, and an email route at most 20 addresses, with exactly 20 allowed.
  - An empty address stays empty.
  - A webhook with only a user name, or only a password, is refused.
  - An address of exactly 254 characters is kept and one of 255 refused; a bad address is named in at most 80 characters.
  - The mail server's port is 587 by default, 1 and 65535 are allowed and 65536 isn't, and its settings can't be changed in place. Its user name is cut to 256 characters.
  - An email's subject is cut to 200 characters, its Message-ID is on the sender's domain, and its date is in local time. It shows 20 lines and says how many more.
  - The mail server gets 20 seconds by default, and a flow 5 seconds to connect and 15 to answer. A flow answering 300 has failed.
  - A server reply given as text, not bytes, still gives its status code.
  - → `test_deliver_mutation.py`.

### Left

| Module | Line | Mutant | Why it is left |
| --- | --- | --- | --- |
| `workloads/deliver.py` | 124 | `1 <= port` to `0 <= port` | Equivalent: a port of 0 is read as the default 587 just before, and the port is a whole number, so no port is 0 here. |

