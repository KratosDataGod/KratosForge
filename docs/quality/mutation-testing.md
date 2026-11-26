# Mutation testing

How strong are the tests on the modules where a quiet bug costs the most? Line coverage says a line
ran; mutation testing says whether a test would notice if that line were wrong. Each batch scores a
few modules, adds tests for the survivors that matter, and explains every mutant it leaves.

## Where the results are

Each batch has its own page in `docs/quality/mutation/`, named `batch-NNN.md` with the batch
number padded to three digits, for example [batch 1](mutation/batch-001.md) and
[batch 36](mutation/batch-036.md), which added boundary mutants. Each page has the batch's
results, the survivors and the tests that now kill them, the mutants left and why, and any real
bugs found. Two pages describe property tests rather than one batch:
[the savings package never invents a saving](mutation/properties-savings.md) (before batches 191 to
193) and [the optimizer never invents a saving](mutation/properties-optimizer.md) (before batches
198 to 201).

Until 2026-11-24 every batch was a section of this page. Each page in the folder is its old section,
unchanged byte for byte, so the history reads as before.

**Adding a batch:** add one new `docs/quality/mutation/batch-NNN.md` and change no other doc page.
There is no hand-kept list of batches here, so two batch PRs never touch the same line. Take batch
numbers from the ledger the coordinating thread keeps, so two lanes don't use the same one.

The method and how to re-run below are batch 1's, and they still hold. Later pages say where a
batch changed them, such as the boundary mutants added in batch 36, the bytecode-cache fix
(#2836) and the unmutated pre-run (#2902).

## Method

- **Mutator.** A small AST mutator, run in batch 1 from outside the repository and since batch 2 kept
  as the dev-only script `sidecar/tools/mutate.py` (standard library and pytest only; the engine never
  imports it, so it adds no dependency). For each mutation point it rewrites the module with one
  change, runs the module's own test files with `pytest -x` and a 120 s timeout, and restores the
  file. mutmut was not used:
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

## How to re-run

`sidecar/tools/mutate.py` (dev only: never run in CI, never imported by the engine) works in a
temporary copy of `sidecar/`, so a run in progress, or one stopped halfway, can't leave a mutant in
your working tree, and several modules can run at once. From `sidecar/`:

```sh
python tools/mutate.py fabric_desktop/retrying.py --out retrying.json -- \
  tests/test_retrying.py tests/test_retrying_more_edges.py tests/test_retry_after.py \
  tests/test_remote_retry.py tests/test_retrying_mutation.py
python tools/mutate.py fabric_desktop/redact.py --entries _SECRETS --out redact-entries.json -- \
  tests/test_redact.py tests/test_redactor_characterization.py tests/test_secret_detectors_userinfo.py \
  tests/test_git_secrets_shapes.py tests/test_secrets_at_rest.py
python tools/mutate.py fabric_desktop/delta_dml.py --only 186,222 --out recheck.json -- \
  tests/test_delta_dml_edges.py tests/test_delta_dml_mutation.py
```

Options: `--cap` (150) and `--seed` (7) choose the sample; `--entries NAME` deletes each entry of a
module-level list instead of mutating points; `--only` runs just the mutants with those indexes again,
to re-check survivors once tests are added. After `--`, the test files. Each line of output is
`<index> <line> <kind> <change> killed|survived|timeout`, and the `--out` JSON has the totals and every
result. Keep the seed at 7 to compare with the tables here: the script samples and mutates the same
points as the batch 1 scratch copy did. One module at a time takes 5 to 25
minutes on four cores; write_queue is the slowest, because a mutant that stops the queue's workers
hangs until the 120 s timeout. A timed-out run is stopped with every process it started (its own
process group, or `taskkill /T` on Windows); a kill that hasn't finished within 30 s is given up on,
so one mutant can't hang the run. Caches (anywhere) and a local `.venv` and build output (at the top of
`sidecar/` only, so a test fixture folder of the same name is still copied) aren't copied into the
temporary `sidecar/`, and `--only` names any index that isn't in the run's sample instead of quietly
skipping it.

Each mutant's tests run with no bytecode cache (`PYTHONDONTWRITEBYTECODE=1`), since #2836. Before
that, Python could reuse the cached code of the previous mutant when the two were the same size and
written in the same second, so a mutant could be scored as the one before it, in either direction.
Scores from batches 1 to 77 were taken without the fix and may be off by such a mutant; batch 78 and
later were taken with it. Two runs with the fix give the same result for every mutant.

Before any mutant, the tests run once on the unmutated copy, and the run stops with a message if they
fail or time out there. Only `sidecar/` is copied, so a test that reads `docs/` or `app/` fails in the
copy whatever the mutant, and without this check every mutant would count as killed. Earlier batches
left such tests out by hand; leave them out (or `--deselect` them) in the same way.
