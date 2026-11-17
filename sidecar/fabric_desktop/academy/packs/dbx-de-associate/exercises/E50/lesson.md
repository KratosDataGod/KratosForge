# Broken: a deploy to prod changed dev resources

The orders bundle deploys dev and prod to one workspace, kept apart by catalog. After databricks bundle deploy -t prod from main, there was still only one nightly_orders job: the dev job had been updated in place, with its [dev] prefix gone and its schedule running, and its runs overwrote tables in the dev catalog. Find out why and fix the bundle without changing anything else.

## What you'll do

Use bundle variables, targets and overrides to promote one codebase across dev, test and prod. Here you find why two targets stepped on each other and fix it with the smallest change.

## Notes

- A bundle deploys into workspace.root_path and keeps its deployment state, the record of which resources it deployed and their IDs, in a state folder under it.
- The default root_path is /Workspace/Users/${workspace.current_user.userName}/.bundle/${bundle.name}/${bundle.target}. The target at the end gives each target its own state.
- If you set root_path yourself, keep ${bundle.target} in it when targets share a workspace.
- Don't use a /Shared path as production's root_path: every workspace user can write there. Prefer a folder whose permissions you control, written to by the service principal.
- A target that doesn't set a variable gets the variable's default. Override it in that target's own variables mapping, such as targets.prod.variables.
- A troubleshooting fix changes what was broken and nothing else.

## What this run proves

- Your bundle gives each target its own deployment folder under the team's folder, sends prod to the prod catalog through the variable, and keeps dev's and prod's other settings as they were, as the bundle docs describe, checked against a model on this PC.

## What it doesn't prove

- That the bundle deploys, or what a deploy does to resources already recorded in a shared state: the Databricks CLI doesn't run here, and the file is JSON, not YAML.
- Cleaning up after the bad deploy, such as databricks bundle destroy or binding an existing job to a target.
