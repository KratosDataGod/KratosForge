# Release status

A snapshot from the release manager's last integration cycle. GitHub is the source of truth for each PR's state and checks; this page records what the cycle found and decided. The same data, one entry per PR, is in [release-manifest.json](release-manifest.json). The process is in [release management](README.md).

**Snapshot:** 2026-10-18 11:20 UTC, `main` at `5f4407b`.

## At a glance

| Question | Answer |
| --- | --- |
| What is released (Stable)? | `9715e28`, promoted 2026-10-17 22:56 UTC. Checksums match its assets; engine `client_id` empty (Microsoft's Fabric sign-in) |
| What is on Preview? | `68198a4`, built 2026-10-18 09:46 UTC. Checksums match; engine `client_id` empty. `main`'s tip `5f4407b` publishes next if its CI is green |
| What changed since round 4? | The hardening audit batch (#138, #152, #154 to #159, #162, #179, #180, #157, docs #184), plus #140, #183 and #186, merged 2026-10-18 between 08:59 and 10:55 UTC |
| Next planned release | Merge round 5 to Preview, then a Stable promotion that carries the QA-001 and QA-002 fixes (#177, #178) once Preview has run with them |
| Which PRs are ready? | 16 non-draft PRs with green CI that merge onto `main` in the round 5 order below |
| Which are still validating? | #139 (`7a5ed18`), #150 (`6c7de8a`) and #181 (`adc0b85`) merged `main` in at 11:10 UTC and their CI is running; #189 (PR-OPT-002) is new |
| Which need their owner first? | #142 (then #148) and #153 (then #167, #174) conflict with `main` and need `main` merged in. Requests sent 2026-10-18 11:00 UTC |
| Which are held? | The workload stack #144 to #172 (#164, #168 and #172 are ready but stacked on drafts #144, #145, #146, #149, #160, #163; Chris's card in the workload thread decides); draft #185; #131 (not opened by this project) |
| Are builds passing? | Every open PR from this project has green CI on its head apart from the Codex `review` check, which fails on quota and is not required |
| Do the PRs pass together? | Yes: the round 5 batch merged in order onto `5f4407b` passes the UI and engine suites (below) |
| What blocks the next Stable release? | QA-001 (Critical: the local Spark writer ignored `.option()`/`.options()`, so a `replaceWhere` overwrite replaced the whole OneLake table) and QA-002 (High: a failed notebook save said Saved), both in every Stable build so far. Fixes #177 and #178 are first in round 5. For an enterprise pilot, the [readiness checklist](../hardening/readiness-checklist.md) is failing too (signing, live tenant test, rollback) |
| Is the release process healthy? | Partly. See risks below |

## Integration trial

Merged in this order onto `5f4407b`: #177, #178, #147, #141, #165, #182, #176, #166, #175, #151, #170, #169, #171, #173, #143. The first ten merge without conflicts. #170, #169, #171 and #173 each add tests at the end of `app/tests/ui.test.cjs`, so each conflicts with the one before; keeping both tests resolves it. #169 also adds Preview names to the list in `app/ui/features.js` where #151 adds `rollback`; keeping both lists resolves it.

| Check | Command | Result |
| --- | --- | --- |
| UI unit tests | `node --test app/tests/ui.test.cjs` | 58 passed, 0 failed |
| Engine tests (Linux, Python 3.12) | `cd sidecar && python -m pytest -q` | 1,603 passed, 2 skipped, 0 failed (8 min 24 s) |
| Windows engine tests, engine and shell builds | CI on each PR | Taken from each PR's own CI; not run on the combined set |
| Rust unit tests | `cargo test` | Not executed: not in CI (HD-011) and not run here |
| Browser tests (`app/tests/browser/`) | | Not executed: not in CI |

## Recommended merge order (round 5)

Stable blockers first, then small fixes and docs, then UI changes as a chain. Chris's go is needed for the round.

1. Stable blockers: #177 (QA-001), #178 (QA-002)
2. Small fixes: #147, #141, #165, #182
3. Docs: #176 (quality baseline), #166 (UX audit)
4. Features without UI test changes: #175, #151, #143
5. UI chain: #170, #169, #171, #173. They conflict only on appended tests, so either each owner merges `main` in after the one before lands, or the four branches are chained (each merges the previous) so one CI round proves them all
6. This page: #161

Then, as each one's CI goes green: #139 and #150 (same `audit.py` `READ_ONLY` line, so whichever merges second needs `main` again), #181, #142 then #148, and the FinOps stack #153, #167, #174. #175 and #153 both edit the last paragraph of `docs/finops/cost-evidence.md`; keep both sentences, Prove's first.

## Release risks found in this audit

| ID | Risk | Owner | Proposed fix |
| --- | --- | --- | --- |
| RM-001 | `main` has no branch protection or required checks, so a merge can land with red or unfinished CI and publish to Preview straight away (Preview's release job does wait for green, but `main` itself can go red) | Chris (repository settings) | Protect `main`: require the `sidecar (ubuntu-latest)`, `sidecar (windows-latest)`, `ui`, `engine`, `shell` and `sbom-app` checks, without requiring branches to be up to date (chained merges handle that); no force pushes; no deletion. Leave out `review` (Codex) and `audit` (an advisory turns it red on every PR) |
| RM-002 | No PR template, so intent, dependencies and shared-file changes are reconstructed from diffs | Release manager | `.github/pull_request_template.md` (this PR) |
| RM-003 | Version numbers never change (shell `0.1.0`, engine `0.20.0`); audit lines, agent status and support bundles all report `0.20.0` whatever the build | Chris decides; release manager with hardening HD-028 | Semver at each Stable promotion, stamped at build time ([versioning](README.md#versioning)) |
| RM-004 | `audit.py` `READ_ONLY`, `app/tests/ui.test.cjs` and the Preview list in `app/ui/features.js` are edited by nearly every feature PR, so parallel PRs conflict on them every round | Owners of `audit.py` (enterprise readiness) and the UI test file | One name per line, sorted, in `READ_ONLY` and the Preview list; split `ui.test.cjs` into a file per page so new tests land in new files |
| RM-005 | CI queue saturation: 30 open PRs each run three 3 to 14 minute Windows jobs, so checks for a new push queue behind others | Release manager | Chain branches for a merge round so only the chain's head needs a full run; draft PRs could skip the Windows jobs (workflow change, Chris's review) |
| RM-006 | Promote copies whatever Preview is at that moment, doesn't re-check checksums, and doesn't copy the SBOMs to Stable | Hardening (HD-023) | Tracked there; checked by hand in [promoting to Stable](README.md#promoting-to-stable) until it lands |
| RM-007 | No rollback: one build per channel, engine always from the channel's current release | Hardening (HD-028) | Tracked there; manual procedure in [rollback today](README.md#rollback-today) |
| RM-008 | Unsigned builds; Smart App Control blocks the engine on many PCs | Chris (Trusted Signing account, D-C3) | Tracked as readiness gate 4 |
| RM-009 | Learn more links open docs from `main` (`open_doc` in `app/src-tauri/src/main.rs`), so a Stable PC can read about Preview-only features (UX-007 in the UX audit) | Shell owner, as UX-007 | Open `blob/<FABRIC_RELEASE_COMMIT>/docs/...` when the build carries its commit, `main` otherwise |
| RM-010 | Stable `9715e28` carries a Critical data-loss defect (QA-001) and a High lost-work defect (QA-002). Rolling Stable back doesn't help: every earlier Stable build has them too | Prove (fixes); Chris (when to promote) | #177 and #178 are first in round 5. Promote to Stable as soon as both are on Preview; say in the release notes what was fixed |

## Held PRs and what they wait on

| PR | Waits on |
| --- | --- |
| #144, #145, #146, #149, #160, #163 | Workload drafts: Chris's card in the workload thread on taking them out of draft. #164, #168 and #172 are stacked on them; the stack merges in order #143 to #172 |
| #185 | Draft: FINOPS-004 part 2, UI mockup with Chris; stacked on #174 |
| #131 | Not opened by this project (fork `as200219`); its CI has never run (fork workflows wait for a maintainer's approval) and it conflicts with `main`. Chris decides whether to take it |
