# Mutation testing, batch 78

Three modules not covered before, none touched by an open pull request, run with `--kinds all`, so each score counts the boundary mutants (`<` to `<=`, `>` to `>=`) together with the earlier kinds. None of the three has a `<`, `<=`, `>` or `>=` comparison, so there are no boundary mutants this time.
- Moving surrogate keys from LH_Keys into the platform's keys database, keeping or renumbering them, and checking the copy (`central/migrate.py`).
- The demonstration fixture, turned into what Fabric's definition and job instance APIs return (`opportunities/fixture.py`).
- Trust badges: reading endorsement with Fabric's admin tenant scan, and a product's semantic models and reports (`central/trust_service.py`).

The run was at `main` 5279c61dc (2026-11-17), with `sidecar/tools/mutate.py` and the same method as earlier batches (sample cap 150, seed 7, `--kinds all`). Test files were given to the tool as paths relative to `sidecar/`, and each was checked to exist before the run. Each module has fewer than 150 mutants, so all were run.

Every run in this batch, before and after, was made with `PYTHONDONTWRITEBYTECODE=1`. Without it, a mutant could be imported from the previous mutant's cached bytecode when the two have the same size and are written in the same second, so it took that mutant's result ([#2836](https://github.com/kratosdatagod/fabric-desktop/issues/2836)). With it, two runs gave the same result for every mutant.

Before each "after" run, its tests were run in a copy of `sidecar/` on its own and against each module as `ast.unparse` writes it, as in batch 11. They passed both ways, so no run below can score a false 100%. No `-k` filter was used, and no test in these sets reads `docs/`, `examples/` or `app/`. `test_opportunities.py` also reaches `fixture.py` but reads `docs/`, so it was left out, which can only lower that module's "before" score.

## Results

| Module | Test files run | Mutants (point + boundary) | Before: killed / survived | Before | Point before → after | Boundary before → after | After: killed / survived | After | Left |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `central/migrate.py` | test_central_migrate_edges (+ test_central_migrate_mutation) | 21 of 21 (21 + 0) | 17 / 4 | **81.0%** | 17/21 → 21/21 | none | 21 / 0 | **100%** | none |
| `opportunities/fixture.py` | test_opportunities_share (+ test_opportunities_fixture_mutation) | 16 of 16 (16 + 0) | 8 / 8 | **50.0%** | 8/16 → 16/16 | none | 16 / 0 | **100%** | none |
| `central/trust_service.py` | test_trust_badges, test_trust_service_edges (+ test_trust_service_mutation) | 53 of 53 (53 + 0) | 48 / 5 | **90.6%** | 48/53 → 53/53 | none | 53 / 0 | **100%** | none |

No bugs were found in these modules. One was found in the mutation tool itself (#2836, above).

### What the survivors were, and the tests that now kill them

- **central/migrate.py**:
  - Keys go to the platform in calls of 5,000, in order, with none left out: 12,001 keys go as 5,000, 5,000 and 2,001, and exactly 5,000 go in one call.
  - An answer without a count counts nothing as copied.
  - At most ten differing keys are listed, though every one of them counts against the match.
  - Renumbered counts add up across tables, and a table without one adds nothing.
  - → `test_central_migrate_mutation.py`.
- **opportunities/fixture.py**:
  - A notebook becomes an nbformat 4.5 `.ipynb` part, cell by cell, with its default lakehouse.
  - A pipeline becomes one inline part.
  - Daily runs go back one day at a time from the day before, each at 06:00:00 whatever the time of day the fixture is dated, and each ends its own number of seconds after it starts; one daily run is one run, and no schedule is none.
  - The loaded demonstration has every scheduled run of every item, and only the price fields it uses.
  - → `test_opportunities_fixture_mutation.py`.
- **central/trust_service.py**:
  - A published product's semantic models and reports are scanned with their own workspaces and reported by name.
  - A draft product's semantic models and reports aren't scanned.
  - A group entry for an item already behind one of the product's ports keeps the port's details.
  - A group entry without a name is reported by its id, and fields it leaves out are recorded as blank.
  - → `test_trust_service_mutation.py`.

### Left

Nothing is left: every mutant in the three modules is killed.

