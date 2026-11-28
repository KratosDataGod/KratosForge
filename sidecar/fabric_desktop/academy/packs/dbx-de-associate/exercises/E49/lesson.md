# Unit tests for transformation functions, run before deploy

The orders bundle's CI workflow runs the unit tests before databricks bundle deploy -t prod, but there is only one test, so three releases broke prod and had to be rolled back. The team kept each bad release's transforms file. Write the tests that would have stopped all three, and that today's code passes.

## What you'll do

Use the Databricks CLI in a CI workflow to validate and deploy bundles, with tests before deploy. Here you write the tests that the workflow's test step runs.

## Notes

- Keep transformation logic in plain functions in a module, apart from the notebook that reads and writes tables. Then a test can call the function with a few rows and check what comes back, with no cluster.
- A CI workflow runs the tests after databricks bundle validate and before databricks bundle deploy. A failing test ends the workflow, so nothing is deployed.
- Each production incident is a missing test. Write the test against the bad release first: it should fail there and pass on the fix.
- Test the awkward inputs: blanks, extra spaces, duplicates and late-arriving rows. Checking only a row count misses which row was kept.
- A test that fails on correct code blocks every deploy. Check today's behaviour before you write the assertion.
- In a real repository these are pytest tests in a tests folder. Here a small runner at the end of the file stands in for pytest; don't change it.

## What this run proves

- Your tests ran on this PC against today's transforms and the three rolled-back releases: every test passes on today's code, so CI would deploy it, and each bad release fails at least one test, so CI would have stopped it.

## What it doesn't prove

- That the tests run in a CI system, or that a failing test stops a real databricks bundle deploy: no CI system, CLI or workspace is used here (exercise E48 models the workflow).
- That your tests catch a regression other than these three releases, or run with pytest: a small runner at the end of the file stands in for pytest.
