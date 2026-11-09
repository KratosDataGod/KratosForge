# Bundle variables, targets and overrides for dev, test and prod

The orders pipeline's bundle deploys the same job to three workspaces. Last week a deploy with no -t went to prod, test wrote into the dev catalog, and the prod job ran as an engineer who has since left. Fix the bundle so one codebase promotes cleanly.

## What you'll do

Use bundle variables, targets and overrides to promote one codebase across dev, test and prod.

## Notes

- Declare a custom variable under variables, with a default, and read it with ${var.name}. Bundle variables are deployment-time: they're filled in when you deploy.
- A target's own variables mapping overrides the value for that target. A value set with --var, a BUNDLE_VAR_ environment variable or variable-overrides.json takes precedence over both.
- One and only one target can be default: true. databricks bundle deploy with no -t deploys to it.
- mode: development prefixes resource names with [dev your-name] and pauses schedules and triggers. mode: production checks the Git branch set in git.branch.
- Databricks recommends service principals for production deployments: run_as.service_principal_name.

## What this run proves

- Your bundle settings send each target to its own catalog through one variable, make dev the default in development mode, and protect prod with production mode, a Git branch and a service principal, as the bundle docs describe, checked against a model on this PC.

## What it doesn't prove

- That the bundle validates or deploys: the Databricks CLI doesn't run here, and the file is JSON, not YAML.
- Variable precedence from --var, BUNDLE_VAR_ environment variables or variable-overrides.json, or lookups.
