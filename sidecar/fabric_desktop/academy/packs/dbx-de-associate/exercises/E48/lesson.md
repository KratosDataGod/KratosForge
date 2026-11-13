# A CI workflow that validates, tests and deploys a bundle

The team's CI workflow deploys the bundle before the tests run, signs in with an engineer's personal token pasted into the file, and runs on feature branches too. A broken change reached prod twice this month.

## What you'll do

Use the Databricks CLI in a CI workflow to validate and deploy bundles, with tests before deploy.

## Notes

- databricks bundle validate checks the bundle's configuration; databricks bundle deploy with `-t` and a target name deploys it; with no -t it uses the default target.
- Run the unit tests before the deploy step, so a failing test stops the workflow before anything changes.
- Use a service principal for CI/CD. For OAuth machine-to-machine sign-in, the CLI reads DATABRICKS_HOST, DATABRICKS_CLIENT_ID and DATABRICKS_CLIENT_SECRET.
- Keep the client secret in the CI system's secret store and read it into the environment. Workload identity federation avoids the secret altogether.

## What this run proves

- Your workflow validates the bundle, runs the tests and only then deploys to the prod target, on pushes to main, signed in as a service principal with its secrets kept in the repository's secret store, checked against a model on this PC.

## What it doesn't prove

- That the workflow runs: no CI system, CLI or workspace is used here, and the file is JSON, not a real workflow file.
- Workload identity federation, which avoids a client secret altogether.
