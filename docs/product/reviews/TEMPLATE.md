# Implementation review: PB-nnn, PR #nnn

Copy this file to `docs/product/reviews/<PB-id>-<pr-number>.md`. Fill in every section. Evidence is
a command and its output, a file and line, a screenshot, or a named person's observation.

| Field | Value |
| --- | --- |
| Backlog item | PB-nnn (and the owning program's id, if any) |
| Pull request | #nnn, merged or not, head commit |
| Package | `docs/product/pr-ready/PB-nnn.md` or "none" |
| Reviewer | name or thread |
| Date | YYYY-MM-DD |
| Environment | OS, app build, engine version, tenant used (non-production) or "none" |

## Outcome

One of: **Accepted** / **Accepted with Documented Limitations** / **Changes Required** /
**Rejected** / **Unable to Verify**.

Recommended by: … Decided by (product owner): … on YYYY-MM-DD.

## Acceptance criteria

| # | Criterion (copied from the backlog item) | Result | Evidence |
| ---: | --- | --- | --- |
| 1 | | Met / Not met / Unable to verify | |

## Checks

| Check | Result | Evidence or note |
| --- | --- | --- |
| Scope matches the item and package | | |
| New tests fail without the change, pass with it | | |
| Full suites pass (pytest, UI, cargo) | | |
| Reachable where docs say; Preview behavior right; packaged into the engine | | |
| OneLake data safety (no unintended writes, dry runs mutate nothing, safe retries, boundaries) | | |
| Authentication and distribution (tokens, scopes in permissions.md, localhost, updates, signing) | | |
| No unproven local-equals-Fabric claim; Preview tags kept where untested | | |
| Docs match the code; linked; strict docs build passes | | |
| Accessibility of new controls | | |
| Inventory and backlog updated; `product_data.py all` consistent | | |

## Findings

| # | Severity (blocking / should fix / note) | Finding | Where | Status |
| ---: | --- | --- | --- | --- |

## Documented limitations (only for Accepted with Documented Limitations)

| Limitation | Where users are told | Backlog item that tracks it |
| --- | --- | --- |

## What is needed to finish (only for Unable to Verify)

Who needs to do what, on which environment.

## Inventory and backlog changes

- Features changed: FEAT-… (status → …, verification → …, evidence added: …)
- Backlog item state: … → …
