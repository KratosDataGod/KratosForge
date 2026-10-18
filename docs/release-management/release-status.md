# Release status

A snapshot from the release manager's last integration cycle. GitHub is the source of truth for each PR's state and checks; this page records what the cycle found and decided. The same data, one entry per PR, is in [release-manifest.json](release-manifest.json). The process is in [release management](README.md).

**Snapshot:** 2026-10-18 01:05 UTC, `main` at `3bee0db`.

## At a glance

| Question | Answer |
| --- | --- |
| What is released (Stable)? | `9715e28`, promoted 2026-10-17 22:56 UTC. Checksums match its assets |
| What is on Preview? | `3bee0db` (`main`'s tip), published 2026-10-18 00:45 UTC. Checksums match. Two docs-only merges ahead of Stable (#135, #136) |
| Next planned release | Merge round 5 to Preview, then a Stable promotion once Preview has run with it. No version number yet (see [versioning](README.md#versioning)) |
| Which PRs are ready? | 7 with green CI that merge cleanly: #138, #140, #141, #142, #143, #147, #150 |
| Which are still validating? | 10 whose CI is running: #137, #139, #148, #152, #154 to #159 |
| Which failed validation? | None now. #148's Windows engine tests failed in `test_local_data.py::test_cancel_a_background_query`, a file it doesn't touch: the cancel race on `main` that #139 fixes. Prove cherry-picked that fix into #148, so either merge order works |
| Which are held? | 9: drafts #144, #145, #146, #149, #160 (workload stack), #151 (rollback), #153 (FINOPS-003); #131 (not opened by this project) |
| What conflicts? | #150 and #139 both add names to the same line of `audit.py` `READ_ONLY`; whichever merges second needs `main` merged in, keeping both sets of names. #131 conflicts with `main` in `app/tests/ui.test.cjs`, `docs/user-guide.md`, `docs/metadata-guide.md` |
| Are builds passing? | `main` CI green at `3bee0db` (run 452). Every non-draft PR whose CI has finished is green apart from the Codex `review` check, which fails on quota and is not required |
| Do the PRs pass together? | Trial of 17 PRs merged onto `3bee0db`: UI tests 55 of 55 passed; engine suite result below |
| What blocks the next Stable release? | QA-001 and QA-002 from the [engineering quality baseline](https://github.com/KratosDataGod/fabric-desktop/pull/176), both already in Stable: the local Spark writer ignores `.option()`/`.options()`, so a `replaceWhere` overwrite replaces the whole OneLake table and reports success (Critical, data loss), and a failed notebook save still says Saved (High). Owner: Prove. The next Stable promotion should carry both fixes. For an enterprise pilot, the [readiness checklist](../hardening/readiness-checklist.md) is failing too (signing, live tenant test, rollback) |
| Is the release process healthy? | Partly. See risks below |

## Integration trial

Merged in this order onto `3bee0db` with `.github/scripts/integration_trial.py`: #140, #147, #141, #138, #143, #137, #139, #152, #154, #155, #156, #157, #158, #159, #150, #142, #148. All merge cleanly except #150 after #139 (`audit.py`, above).

| Check | Command | Result |
| --- | --- | --- |
| UI unit tests | `node --test app/tests/ui.test.cjs` | 55 passed, 0 failed |
| Engine tests (Linux, Python 3.12) | `cd sidecar && python -m pytest -q` | 1,575 passed, 2 skipped, 0 failed (6 min 51 s). This trial had #139 at `53073e2` (the cancel fix, before FD-LDE-005 was added) and #137 at `8a5ad6e`; both have new heads since |
| Windows engine tests, engine and shell builds | CI on each PR | Taken from each PR's own CI; not run on the combined set |
| Rust unit tests | `cargo test` | Not executed: not in CI (HD-011) and not run here |
| Browser tests (`app/tests/browser/`) | | Not executed: not in CI |

## Recommended merge order (round 5)

Smallest blast radius first, stacks in order, CI still running last. Chris's go is needed for the round.

1. Docs and records: #137 (hardening audit; the fixes below cite it), #140 (product records)
2. Small single-area fixes: #147, #141, #138, #152, #154, #155, #156, #157, #159
3. Update path: #158 (engine download refuses a missing checksum and never falls back to Preview on a network error). Both current releases carry `engine_sha256`, so no existing PC is affected. Its Rust tests don't run in CI.
4. Features: #143, #139, #150 (after #139, with `main` merged in for `audit.py`), #142, then #148 (stacked on #142)

## Release risks found in this audit

| ID | Risk | Owner | Proposed fix |
| --- | --- | --- | --- |
| RM-001 | `main` has no branch protection or required checks, so a merge can land with red or unfinished CI and publish to Preview straight away (Preview's release job does wait for green, but `main` itself can go red) | Chris (repository settings) | Protect `main`: require the `sidecar (ubuntu-latest)`, `sidecar (windows-latest)`, `ui`, `engine`, `shell` and `sbom-app` checks, without requiring branches to be up to date (chained merges handle that); no force pushes; no deletion. Leave out `review` (Codex) and `audit` (an advisory turns it red on every PR) |
| RM-002 | No PR template, so intent, dependencies and shared-file changes are reconstructed from diffs | Release manager | `.github/pull_request_template.md` (this PR) |
| RM-003 | Version numbers never change (shell `0.1.0`, engine `0.20.0`); audit lines, agent status and support bundles all report `0.20.0` whatever the build | Chris decides; release manager with hardening HD-028 | Semver at each Stable promotion, stamped at build time ([versioning](README.md#versioning)) |
| RM-004 | `audit.py` `READ_ONLY` and `app/tests/ui.test.cjs` are edited by nearly every feature PR, so parallel PRs conflict on them every round | Owners of `audit.py` (enterprise readiness) and the UI test file | One name per line, sorted, in `READ_ONLY`; split `ui.test.cjs` into a file per page so new tests land in new files |
| RM-005 | CI queue saturation: 25 open PRs each run three 3 to 14 minute Windows jobs, so checks for a new push queue behind others | Release manager | Chain branches for a merge round so only the chain's head needs a full run; draft PRs could skip the Windows jobs (workflow change, Chris's review) |
| RM-006 | Promote copies whatever Preview is at that moment, doesn't re-check checksums, and doesn't copy the SBOMs to Stable | Hardening (HD-023) | Tracked there; checked by hand in [promoting to Stable](README.md#promoting-to-stable) until it lands |
| RM-007 | No rollback: one build per channel, engine always from the channel's current release | Hardening (HD-028) | Tracked there; manual procedure in [rollback today](README.md#rollback-today) |
| RM-008 | Unsigned builds; Smart App Control blocks the engine on many PCs | Chris (Trusted Signing account, D-C3) | Tracked as readiness gate 4 |
| RM-009 | Learn more links open docs from `main` (`open_doc` in `app/src-tauri/src/main.rs`), so a Stable PC can read about Preview-only features (UX-007 in the UX audit) | Shell owner, as UX-007 | Open `blob/<FABRIC_RELEASE_COMMIT>/docs/...` when the build carries its commit, `main` otherwise |
| RM-010 | Stable `9715e28` carries a Critical data-loss defect (QA-001) and a High lost-work defect (QA-002). Rolling Stable back doesn't help: every earlier Stable build has them too | Prove (fixes); Chris (when to promote) | Promote to Stable as soon as both fixes are merged and on Preview; say in the release notes what was fixed |

## Held PRs and what they wait on

| PR | Waits on |
| --- | --- |
| #144, #145, #146, #149, #160 | Workload stack drafts: Chris's reaction to mockup boards; stacked on #143 |
| #151 | Draft: Chris's decision card on environment rollback |
| #153 | Draft: FINOPS-003 under review in its thread |
| #131 | Not opened by this project (fork `as200219`); its CI has never run (fork workflows wait for a maintainer's approval) and it conflicts with `main`. Chris decides whether to take it |
