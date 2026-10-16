# Codex review routing

This workflow runs automatically for non-draft PRs targeting `main`, including
new pushes. It can also be started from Actions with a PR number. It starts in
**recommendation-only mode**: it posts one updated PR comment and a routing
label. It contains no approval or merge operation.

- `codex: recommend-approve`: at most five added/modified Markdown documentation
  files, at most 200 added/deleted lines, complete review, no findings, and green
  CI. Operational/security guidance still requires manual review. Deleted and
  renamed files, code comments, and application code are not eligible.
- `codex: manual-review`: everything else, including any P2 finding, missing API
  key, failed API call, incomplete input, unknown CI state, or untrusted author.
  Requests review from `KratosDataGod`; on that user's own PRs, assigns the PR
  and mentions them instead, because GitHub prevents author self-review.

## Setup

1. Merge this setup PR manually. The automatic `pull_request_target` trigger
   uses workflow code from the base branch, so it becomes active after merge.
2. Store an OpenAI API key in the repository Actions secret `OPENAI_API_KEY`.
   API usage is billed separately from ChatGPT. Set an appropriate project
   budget/alert in OpenAI. No API key belongs in source or chat.
3. Open a small editorial docs PR and an application-code PR to verify both
   routing outcomes. For an existing PR, run **Codex review routing** manually.
4. Require CI and human approval in a repository ruleset if these should be
   enforced. This workflow does not change existing branch protection.

Review the first 10–20 decisions before considering a separate change to add
actual bot approvals. Do not use a clean native Codex comment as authorization:
the built-in integration's reporting threshold differs from this workflow's.
Native automatic reviews may also be enabled in Codex settings, but are not a
dependency of this API-backed workflow.

## Execution boundaries

The reviewer runs on a GitHub-hosted Linux runner with a read-only GitHub token,
Codex's read-only sandbox, and `drop-sudo`. The API key is supplied to the pinned
Codex action's proxy. Only trusted base code is checked out. PR text is fetched
as data; PR code, dependencies, and proposed instructions are never executed.
The write-capable publisher runs in a separate job without the API key and
validates the reviewed SHA, report, and deterministic policy independently.
Only owner/member/collaborator PR authors are sent to the API; other authors
are routed directly to the maintainer. The action also checks the triggering
user's repository access.

Oversized PRs (over 50 files or 500 KB of review input), binary/unavailable
patches, changed files over 100 KB, changed base revisions, and invalid reports
go to manual review. For an approval candidate, the publisher waits up to ten
minutes for the existing merge-commit CI checks: both sidecar platforms,
agent-image, engine, shell, and docs `build` for changes under `docs/`. Update
the required names in policy.cjs if CI job names change. Pending/timed-out CI
requires manual review; rerun the workflow after checks finish if desired.

Checks on new pushes cause a fresh review; obsolete head results are discarded.
Labels and comments are advisory, never substitutes for GitHub merge rules.
The bot does not remove previous review requests or human assignments.

Run the routing tests without dependencies:

```sh
node --test .github/codex/policy.test.cjs
```
