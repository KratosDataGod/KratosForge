# Release management

How a change gets from a pull request to people's PCs, who decides each step, and how the release manager integrates work from many parallel workstreams. This page is the process; the live state is in [release status](release-status.md), the machine-readable inventory in [release-manifest.json](release-manifest.json), and every Stable build so far in [release history](release-history.md).

Release gates are not defined here. The pass/fail gates for a pilot or production release are the [readiness checklist](../hardening/readiness-checklist.md) owned by the hardening program; this page says when each one is checked.

Two review threads also give each PR a PASS, CONDITIONAL PASS or FAIL: the UX and simplicity review (`docs/ux/release-review.md`) and the engineering quality review (`docs/quality/`). A FAIL from either one keeps a PR out of a merge round until its owner fixes it or Chris overrides it. A CONDITIONAL PASS goes into the round with its conditions listed in the merge-round question. Neither review merges or releases anything.

## How KratosForge is distributed

There is no installer, store or package feed. The product is two files published as GitHub release assets, and the app updates itself from them.

| Piece | What it is | Built by |
| --- | --- | --- |
| `kratosforge.exe` (and the same file as `fabric-desktop.exe`) | The Tauri 2 shell with the UI (`app/`). Builds before the name changed download only `fabric-desktop.exe`, so CI and Promote publish both | CI `shell` job, `cargo build --release --locked` on Windows |
| `fabric-desktop-engine.zip` | python.org's embeddable Python 3.12 plus the `fabric_desktop` engine (`sidecar/`) and every pinned package in `sidecar/constraints.txt` | CI `engine` job on Windows |
| `fabric-desktop-engine-update.zip` | Only the engine's own files (`fabric_desktop`, its dist-info and launcher, and `engine.json`), a few MB. A PC whose installed engine has the same runtime key (the same Python and packages) updates from this instead of the whole engine (`app/src-tauri/src/engine_patch.rs`) | CI `engine` job on Windows |
| `version.json` | The build's commit, What's new notes, 30 days of history, the SHA-256 of the exe, engine and engine update, and the engine's runtime key (`.github/scripts/engine_runtime.py`) | CI `release` job |
| SBOMs | `sbom-app.cdx.json`, `sbom-engine.cdx.json` (CycloneDX) | CI `sbom-app` and `engine` jobs |

The headless agent's Linux image (`deploy/linux/Dockerfile`) is built and started in CI but not published anywhere. The docs site is published to GitHub Pages by `docs.yml` on every merge that touches `docs/`.

### Channels

| Channel | Release tag | Moves when | Who moves it |
| --- | --- | --- | --- |
| **Preview** | `latest` (prerelease) | Every merge to `main` whose CI is green and is still `main`'s newest commit when the release job runs | Automatic (`ci.yml` `release` job) |
| **Stable** (the default) | `stable` | Someone runs **Promote to stable**, which copies the exe, engine and `version.json` from `latest`, or from a named build | The product owner (Chris), by hand, or the release manager on his explicit go for that promotion |

The app reads the commit from the channel's `version.json`, not from the release's target commit (that field goes stale after a promote). `version.json` carries `published`, the Unix time CI or Promote wrote it. The app installs whatever build the channel names, older or newer than the one running, but refuses a `version.json` published before the newest one it has already accepted from that channel, and one without the exe's checksum (HD-022). So anything that republishes a channel by hand must write a new `published` stamp, or PCs that saw the newer one will refuse it. Both channels publish with one shared concurrency group, `release-assets`, so a promote never copies a half-replaced Preview.

### When a new advisory lands

The CI `release` job waits for the `audit` job (pip-audit on `sidecar/constraints.txt`, cargo audit on `app/src-tauri/Cargo.lock`). So when a new advisory names something the app ships, `audit` turns red on every pull request and on `main`, and Preview stops publishing. PCs on Preview keep the last build that passed, and Stable doesn't move. Two ways unblock it:

1. **Move the package** in its own pull request (CONTRIBUTING). This is the normal path.
2. **Record an exception** in `.github/audit-exceptions.txt`, in a reviewed pull request: the advisory id, a lapse date (`until=YYYY-MM-DD`) and why it's accepted, for example the code isn't reachable from the app or no fixed release exists yet. On the lapse date the audit fails again until someone moves the package or decides again. A line with no date or reason fails the audit too (`.github/scripts/audit_exceptions.py`).

Security details of an advisory that isn't public yet never go in the exception's reason or in a public issue.

### Per-build releases

Each build that the release job publishes to Preview also gets a release of its own, tagged `build-<first 12 characters of the commit>` (for example `build-9715e28a1b2c`). It is a prerelease and is never marked latest. It holds the same five files as `latest` at that moment: the exe, the engine zip, the same `version.json` (same checksums, commit and history) and the two SBOMs. CI publishes it before it updates `latest`, so the build on Preview always has one.

- **It never changes.** A re-run of the release job leaves an existing per-build release as it is.
- **It is not a channel.** The app never updates from it on its own.
- **Only the newest 20 are kept** (`KEEP_BUILDS` in `ci.yml`). The release job deletes older `build-*` releases and their tags. It never touches `latest` or `stable`.
- **Every build promoted to Stable is kept.** Promote adds "promoted to Stable" to that build's release title, and the release job never deletes a release with that title. So Stable can always go back to any earlier Stable build that was published after per-build releases began.

To list them: `gh release list -R KratosDataGod/fabric-desktop | grep build-`.

**Merging to `main` is a Preview release.** About 5,000 people are expected on launch; Preview is the opt-in channel, but anything merged reaches it within one CI run. Stable is the production release.

## Who decides what

| Step | Decided by | Notes |
| --- | --- | --- |
| Open, rebase, fix a PR in its own area | The owning workstream | Fixes in someone else's area go back to the owner |
| Trial integration, conflict analysis, integration fixes on an integration branch, release docs | Release manager | No approval needed; nothing reaches `main` |
| Merge to `main` (= Preview release) | Chris | He merges by hand, or asks for a merge round. Each request covers that round only |
| Promote to Stable (= production release) | Chris | Asked as a separate question, with the candidate commit and its evidence |
| Branch protection, repository variables, signing account | Chris | Repository settings only he can change |
| Workflow changes (`.github/workflows/`) | Chris reviews | Per `AGENTS.md`, workflow changes always need maintainer review |

Only pull requests this project opened are merged in a merge round. A PR from someone else (for example a fork) is listed but held until Chris says otherwise.

## The integration cycle

Run on every release-management pass. It is safe to repeat: it changes nothing on `main` and creates no PRs, tags or releases unless a step below says so.

1. **Discover.** Fetch `main` and every open PR head (`git fetch origin '+refs/pull/*/head:refs/pr/*'`). List open PRs with base, draft state, author and CI on the head commit.
2. **Check each PR against `main`.** `python3 .github/scripts/integration_trial.py --base origin/main <PR numbers>` reports for each PR whether it merges cleanly on its own, and then merges them in the order given onto a throwaway branch and reports the first conflict for each.
3. **Classify** every PR (states below) and record it in [release-manifest.json](release-manifest.json) and [release status](release-status.md).
4. **Order the batch.** Smallest blast radius first: docs-only, then single-module fixes, then features; stacked PRs in stack order; PRs whose CI hasn't finished or is unproven last. Nearly every UI PR appends to `app/tests/ui.test.cjs`, so two UI PRs always conflict after the first merges: chain them (each branch merges the previous) so one CI round proves the whole chain.
5. **Validate the integrated result**, not just each PR. On the trial branch, run what CI runs that can run on Linux:
   - `node --test app/tests/ui.test.cjs`
   - `node --test --test-concurrency=1 "app/tests/browser/*.test.cjs"` with Playwright 1.56.1 on `NODE_PATH` and the sidecar installed (the `ui-browser` job)
   - `cd sidecar && python -m pip install -e ".[dev]" -c constraints.txt && python -m pytest -q`
   Windows-only jobs (engine build, shell build, Windows pytest) are taken from each PR's own CI and are re-proven by the post-merge CI run on `main`.
6. **Repair** only what integration broke: a conflict where both sides' intent is clear, resolved so neither change is lost, on the integration branch or as a corrective PR. Anything that changes a feature's behaviour goes back to its owner with a corrective request (format below). Two incompatible designs go to Chris as a decision, never a compromise invented here.
7. **Ask for the merge round.** One question to Chris naming the PRs, the order and the evidence. Nothing merges without his go for that round.
8. **Merge** in order, waiting for each merge's `main` CI where the next PR conflicts with it. Watch the Preview publish of `main`'s newest commit.
9. **Verify Preview.** The `latest` release's `version.json` names `main`'s tip and its `exe_sha256`, `engine_sha256` and `engine_update_sha256` equal the GitHub digests of the uploaded assets (`curl -s https://api.github.com/repos/KratosDataGod/KratosForge/releases/tags/latest | jq '.assets[] | {name, digest}'`). If the engine zip is missing, re-run the failed release job of `main`'s tip run.
10. **Report** in the thread: what was reviewed, merged, fixed, what failed and what is blocked.

### PR states

| State | Means |
| --- | --- |
| Ready to merge | Real CI green on the head commit (the Codex `review` check is ignored while its quota is exhausted), merges cleanly onto `main` or onto the PR before it in the batch, not a draft, nothing it depends on is unmerged |
| Validating | CI on the head commit hasn't finished yet, or the head changed after the last trial |
| Requires integration fix | Acceptable, but conflicts with `main` or another ready PR in a way the release manager can resolve without changing behaviour |
| Requires developer changes | Needs a change only the owner should make; a corrective request has been sent |
| Blocked by dependency | A PR it builds on (stack base, shared contract) is not merged |
| Failed validation | A real CI check is red on the head, or the integrated trial fails |
| Hold | Draft, awaiting Chris's decision or mockup reaction, or not opened by this project |
| Ready for release | Merged, on Preview, and waiting for the next Stable promotion |

### Corrective request format

Sent to the owning workstream through the project coordinator:

1. Affected PR and head commit.
2. Problem, as the failing command or conflicting lines.
3. Root cause.
4. The change needed.
5. Acceptance criteria.
6. How to validate (exact commands).

### What each PR must say (handoff contract)

The [pull request template](https://github.com/KratosDataGod/KratosForge/blob/main/.github/pull_request_template.md) asks every PR for: what a user sees before and after, the workstream and feature ID, related issue, shared files and contracts touched (engine methods, record schemas, settings, `audit.py`, `server.py`, `app.js`, `features.js`), PRs it depends on or conflicts with, tests added and run, Preview labelling, permission changes, and release notes in user words. The release manager reads these instead of reconstructing intent from diffs.

## Promoting to Stable

A Stable promotion is the production release. Before asking Chris for it:

1. **Pick the candidate.** The commit now on Preview (`latest` `version.json` `commit`). It must be `main`'s tip or an ancestor with no later merge pending on Preview.
2. **Check the evidence.** `main` CI green on that commit; Preview assets match their checksums (step 9 above); the [readiness checklist](../hardening/readiness-checklist.md) gates that apply to a Stable release are no worse than at the last promotion; `FABRIC_CLIENT_ID` is still unset (the engine's `engine.json` `client_id` must be empty, so sign-in uses Microsoft's Fabric CLI client).
3. **Write the notes** for people, not developers: one line per change they will notice, from the PR titles and "Release notes" sections since the last Stable commit (`git log --first-parent --format=%s <last stable>..<candidate>`). Untested-on-a-tenant features say Preview.
4. **Ask Chris** with the candidate commit, the PR list, the evidence and the notes.
5. **On his go,** run **Actions > Promote to stable** with the notes, and put the candidate commit in **build**. Promote then copies that build's own release, not whatever Preview is at that moment. Left empty, it copies `latest` as before. Either way it checks the exe and engine against the SHA-256 in that build's `version.json` and stops without changing Stable if they differ.
6. **Verify Stable:** `stable` `version.json` names the candidate and its checksums match the `stable` asset digests.
7. **Record it** in [release history](release-history.md).

## Rollback

A rollback is a promotion of an earlier build.

**How PCs follow a channel rollback.** The app doesn't refuse a build because it is older than the one running; it refuses a `version.json` that is older than the newest one it has seen on that channel. A rollback republishes the earlier build's exe, engine and `version.json` (same `commit` and checksums) with a new `published` stamp, which Promote writes on every run. PCs then accept it as the channel's newest release and install the earlier build at their next start. A cached copy of the release it replaced is refused, because its stamp is earlier. A `version.json` uploaded by hand (see **Older builds** below) must set `published` to the current time.

**On one PC,** an update ends the running build's local engine (a run still in progress is stopped, not waited for; if a call still holds the engine 10 seconds later, the update is refused and the running build carries on), then keeps the previous exe as `kratosforge.exe.old` until the new build has opened its window; if the new build exits before that, the previous exe is put back, restarts its engine and keeps running. Builds from before HD-022 never confirm their start, so a rollback to one of them is left running after 90 seconds rather than undone. After a successful start `.old` is removed, so it is not a way to go back to an earlier build later.

In order of preference:

1. **Fix forward.** Revert the offending PR on `main` (a revert PR, merged by Chris), let Preview publish, then promote. This is the normal path.
2. **Promote an earlier build to Stable.** Find the previous Stable commit in [release history](release-history.md). Run **Actions > Promote to stable** with **build** set to that commit (or its `build-*` tag) and notes that say what was rolled back and why. Promote checks the files against that build's `version.json` before it copies them. Self-updating Stable PCs move to it at their next start. This replaces production files and needs Chris's explicit go.
3. **Organizations on managed updates** deploy the earlier build's exe themselves (see pinning below).

Two limits today:

- **Older builds.** A build published before per-build releases began has no `build-*` release, so it can't be promoted again. For one of those, rebuild it: re-run that commit's `main` CI run if its artifacts are still retained (GitHub keeps workflow artifacts 90 days by default), verify against the build's provenance attestation, and upload to `stable` with a matching `version.json` stamped with a new `published` time.
- **Preview can't be rolled back by promotion.** `latest` always follows `main`. Fix forward instead.

### Pinning a build

An organization can stay on one build while the channels move on. Turn off self-updating (`"auto_update": false` in the policy file, [enterprise install](../enterprise-install.md)) and deploy the exe from that build's release:

`https://github.com/KratosDataGod/KratosForge/releases/download/build-<12 characters>/kratosforge.exe` (builds from before the name changed have only `fabric-desktop.exe`)

The engine zip, `version.json` and SBOMs are next to it under the same tag. `version.json` gives the SHA-256 of the exe and the engine to check a copy against.

With `"auto_update": false` the app downloads its engine only from its own build's `build-*` release, never the channel's (`stable` or `latest`), so a pinned exe gets the matching engine for as long as its release is kept. Once that release is deleted, a PC that has to download the engine is told this build no longer has one and to ask IT for the current exe. Self-updating PCs take the engine from their channel's current release.

Reverting the app does not revert what a newer build wrote: local settings, schedules, run history, the metadata store and anything in OneLake stay as the newer build left them. Before shipping a change to a stored format, its PR must say whether an older build can still read it.

## Versioning

Builds are identified by their commit. Each published build's release tag, `build-<first 12 characters of the commit>`, names it too. Version numbers in the code do not change between builds today: the shell reports `0.1.0` (`app/src-tauri/Cargo.toml`, `tauri.conf.json`) and the engine `0.20.0` (`sidecar/pyproject.toml`, `fabric_desktop/__init__.py`), which audit logs, agent status records and support bundles copy. So a support bundle cannot say which build it came from without the commit.

Proposal (RM-003, needs Chris's decision): semantic versions `0.MINOR.PATCH` cut at each Stable promotion, with a `v0.x.y` tag on the promoted commit and the version stamped into the shell and engine at build time from the tag (Preview builds report `0.x.y-preview+<sha7>`). `1.0.0` is reserved for the first release that passes every gate in the readiness checklist. This needs workflow changes and pairs with HD-028's per-build assets, so it is a single design with the hardening program.
