# Mutation testing, batch 369

One module not scored before, run with `--kinds all`:
- The offline policy package: taking it from the platform's resolution, checking its hashes and parts, reading it from a file with a pinned hash and a maximum age, resolving from it alone, and saying how old it is (`central/policy_package.py`).

The run was at `main` ea988e99 (2026-11-23), with `sidecar/tools/mutate.py` and the method of earlier batches (sample cap 150, seed 7, `--kinds all`), on DuckDB 1.5.6. The per-mutant timeout was raised from 120 to 600 seconds by a wrapper that sets `mutate.TIMEOUT`, with the tool unchanged, because three runs shared the machine; no mutant's run timed out. Each pytest session got a fresh temporary folder, so a mutant can't leave one that breaks later runs. Each test set was run with `-x`. Test files were given as paths relative to `sidecar/`, and they first passed once on the unmutated copy. None of them reads `docs/`, `examples/` or `app/`, so nothing was left out. The module has 143 mutants, so all were run. `main` has changed this module since this run (`verify` checks the shape of each part, `read` also refuses a package nested too deep to read, and `resolve_offline` turns a broken package's errors into a refusal); the new tests pass against today's version too.

The first run used test_policy_package, test_policy_promote_gate and test_policy_package_gateway, and scored 101 / 143 (70.6%). Each survivor was run again with test_best_practice_checkers, test_checks_service_cov2 and test_policy_promote_gate_gateway added. That run killed none of the 42 survivors, so the "before" figures are the first run's.

Before the "after" run, the new tests passed in a copy of `sidecar/` on its own and against the module as `ast.unparse` writes it. They also pass on DuckDB 2.0.0.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `central/policy_package.py` | test_policy_package, test_policy_promote_gate, test_policy_package_gateway (+ test_central_policy_package_mutation) | 143 of 143 (137 + 6) | 101 / 42 | **70.6%** | 100/137 → 137/137 | 1/6 → 6/6 | 143 / 0 | **100%** | none |

No bugs were found.

### What the survivors were, and the tests that now kill them

- **central/policy_package.py**:
  - A package is format version 1, and its hash is over sorted keys in ASCII JSON.
  - The platform is named by its function and workspace ids, with question marks when they are missing, or as the platform on this PC by file name.
  - No inheritance chain takes no package. Only exceptions for the package's own policies that are active and not ended go in.
  - `verify` refuses a newer or missing format version, and its messages give the first 12 characters of both hashes, or "missing".
  - A resolved policy whose version isn't in the package, or a package missing a part, is refused with the missing part named.
  - `read` says a missing file "isn't there". A maximum age that is negative, infinite or not a number is refused, and an age equal to the maximum is allowed.
  - An unreadable `taken_at` is "of unknown age", and one without a time zone is read as UTC.
  - `age` changes from minutes to hours at 60 minutes, and from hours to days at 48 hours.
  - `resolve_offline` passes the context on, and no context as an empty one.
  - → `test_central_policy_package_mutation.py`.

### Left

None.
