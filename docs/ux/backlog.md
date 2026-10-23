# UX backlog and PR specifications

The top 20 simplification opportunities from the October 2026 audit, items added since (UX-021 onward), then the focused pull requests they group into. IDs are `UX-nnn`; the product management thread links them from `docs/product/PRODUCT_BACKLOG.json`.

**Priority:** P0 blocks core work or is a security risk · P1 significant friction in common work · P2 inconsistency, extra clicks, unclear words · P3 polish.

**Owner** is the thread that owns the screen. This oversight specifies and reviews; the owner builds, unless the change is in shared UI code no other thread is changing.

## Overview

| ID | Priority | Title | Owner | PR |
| --- | --- | --- | --- | --- |
| UX-001 | P1 | Command bar prints `null` between results | Shared UI (this oversight) | PR-UX-01 |
| UX-002 | P1 | Broken help links, and a test for every help link | Shared UI (this oversight) | PR-UX-01 |
| UX-003 | P1 | Sign-in errors that say what happened | Enterprise readiness (sign-in) | PR-UX-02 |
| UX-004 | P1 | Screens for local data projects; SQL on this PC | Local data engine (FD-LDE) | PR-UX-09 |
| UX-005 | P1 | One first-run card on Home instead of three sign-in prompts | Enterprise readiness (first run) | PR-UX-03 |
| UX-006 | P1 | App (client) ID and engine limits move to Advanced in Settings | Shared UI (this oversight) | PR-UX-03 |
| UX-007 | P1 | Help opens the docs for the running build | Release management (shell) | PR-UX-05 |
| UX-008 | P1 | Back, forward and reopen where you left off | Shared UI (this oversight) | PR-UX-04 |
| UX-009 | P1 | One home for keys, sources, domains, glossary and platform setup | Data mesh, roadmap and Platform threads | PR-UX-07 |
| UX-010 | P1 | Monitor: at most four segments, with Operations grouping the rest | Workload management | PR-UX-07 |
| UX-011 | P1 | One error panel and Retry for every failure | Shared UI (this oversight) | PR-UX-02 |
| UX-012 | P1 | Keyboard access for rows, menus and tabs | Enterprise readiness (accessibility) | PR-UX-06 |
| UX-013 | P1 | Show when the engine is busy, and let people stop it | Codebase audit and hardening | PR-UX-10 |
| UX-014 | P1 | Home and the bell never claim all is well when they couldn't check | Workload management (Home), shared (bell) | PR-UX-10 |
| UX-015 | P2 | Pickers instead of typed IDs and paths | Each feature owner; folder picker shared | PR-UX-08 |
| UX-016 | P2 | One environment picker for the whole app | Shared UI, with Data mesh | PR-UX-07 |
| UX-017 | P2 | Plain words for Fabric internals | Each feature owner | PR-UX-11 |
| UX-018 | P2 | The same confirmation for every destructive action | Shared UI, with owners | PR-UX-12 |
| UX-019 | P2 | Help on every screen; one generated Preview list; keyboard shortcut list | Shared UI and docs | PR-UX-05 |
| UX-020 | P2 | Show Home at once; shared loading and empty states | Shared UI (this oversight) | PR-UX-04 |
| UX-021 | P1 | Preview tag on every action that writes to Fabric without a tenant test | Each feature owner | PR-UX-13 |

Smaller findings kept for later (P2-P3): policy check calls valid integration keys invalid (`app_settings.py:143-145`); account switcher and tenant choice for guests; placeholder and disabled text at 2.6:1 contrast, signed-out avatar at 3.5:1 (`styles.css:103,196`); no dark or high-contrast theme; duplicated byte and date formatters (four and six copies); Teams webhook stored in plain text; `scheduleNotebook` reads `state.env`, which is never set (`app.js:2168`); `confirmDialog` never resolves when closed with Esc (`meta.js:411-416`); the TOML editor traps Tab (`meta.js:324`); stale `architecture/ai-integration.md`.

## Issues

### UX-001 Command bar prints `null` between results (P1)

- **Workflow:** W10 Find a feature; every Ctrl+K use.
- **Now:** a line reading `null` appears between every result ([images/command-bar-null.png](images/command-bar-null.png)). `draw()` in `features.js:129-134` returns `[head, row]` with `head = null` when the group doesn't change, and `replaceChildren` turns `null` into text.
- **Why it matters:** the command bar is how the guidelines say people find everything; it looks broken to every user.
- **Proposed:** drop `null` entries before `replaceChildren`.
- **Benefit:** the command bar reads cleanly.
- **Scope:** `features.js`, one line, plus a test.
- **Dependencies:** none.
- **Acceptance:** with no text typed, the list shows group headers and items only; no text node reads `null`.
- **Tests:** a `ui.test.cjs` case that renders `openPalette()` with fake commands and asserts no `null` text.

### UX-002 Broken help links, and a test for every help link (P1)

- **Workflow:** all; documentation.
- **Now:** `learnMore("metadata")` (`meta.js:114`) and `learnMore("source-control")` (`git.js:112-113`) point at anchors that don't exist, so the guide opens at the top. Nothing checks help anchors.
- **Proposed:** fix the two anchors (`data-mesh`, `source-control-preview`, or link `source-control.md`); add a test that collects every `learnMore(...)`, `open_doc` page, `XRAY_DOCS` entry and readiness `doc`, and checks the page exists and the anchor matches a heading.
- **Scope:** `meta.js` and `git.js` (one string each), a new test.
- **Dependencies:** none. Tell the Git and Data mesh owners in the PR.
- **Acceptance:** the test passes on main after the fix and fails if any anchor is renamed.
- **Tests:** the new test runs in the existing `ui` CI job.

### UX-003 Sign-in errors that say what happened (P1)

- **Workflow:** W2.
- **Now:** any AADSTS code not in a short list, including 50076 (MFA required), 50079, 50020 (wrong tenant) and 50058, shows "Your sign-in expired. Sign in again and retry." ([images/sign-in-mfa-error.png](images/sign-in-mfa-error.png); `activity.js:51`). The message starts "Sign-in failed: Sign-in failed:" (`auth.py:149` plus the label at `app.js:197`). A cancelled prompt is shown as a failure (*unverified*: exact MSAL cancel payload).
- **Proposed:** the engine returns `{code, description}` for sign-in failures; the UI maps the common Entra codes to one sentence each and a next step (complete MFA in the window, use your work account, ask IT about Conditional Access with the code to quote); cancel shows nothing.
- **Benefit:** first-day failures become self-service.
- **Scope:** `auth.py`, `friendlyError` in `activity.js`, `troubleshooting.md` rows.
- **Dependencies:** enterprise readiness owns sign-in; route there.
- **Acceptance:** each mapped code shows its sentence; no doubled prefix; cancel leaves no toast.
- **Tests:** engine unit tests for the structured error; `ui.test.cjs` cases per code.

### UX-004 Screens for local data projects; SQL on this PC (P1)

- **Workflow:** W4. The product's core promise.
- **Now:** the engine works (`docs/local-data.md`) but has no UI; the SQL page opens only on a Fabric lakehouse.
- **Proposed:** Create gets a Local data section (projects, Add folder with a picker, tables found); the SQL page gets a "This PC" target; every local screen says "Runs on this PC · no Fabric job" and where behavior differs from Fabric.
- **Scope:** owned by the local data engine program (FD-LDE), mockup first (Chris's rule).
- **Dependencies:** FD-LDE local SQL target (in progress); UX-015 folder picker.
- **Acceptance:** a person with no sign-in can add a folder and query a CSV in four steps.
- **Tests:** browser test with the real engine (`app/tests/browser`).

### UX-005 One first-run card on Home (P1)

- **Workflow:** W1.
- **Now:** a new user sees Ready to work (with a Sign in button), a sign-in callout (another Sign in) and, when scrolled, Get started (a third), plus an empty "Environments" heading ([images/home-first-run.png](images/home-first-run.png); `app.js:353-370`, `settings.js:477-540`).
- **Proposed:** one first-run card with two choices: **Sign in to Fabric** or **Work on this PC** (open a notebook or, later, a local folder). Ready to work folds into a one-line status ("Ready · 4 of 6") that expands; Get started shows below once signed in; empty sections are hidden.
- **Scope:** Home only; mockup first.
- **Dependencies:** enterprise readiness owns first run; route there.
- **Acceptance:** at most one Sign in button visible on Home when signed out; nothing reads "Checking…" with no heading content.
- **Tests:** browser screenshot test signed out and signed in.

### UX-006 Advanced settings (P1)

- **Workflow:** W1, W2.
- **Now:** App (client) ID is the second row in Settings > Account for everyone (`settings.js:207-216`); saving it does nothing when the environment variable is set, but says "Saved" (`app_settings.py:205-208`). Five SQL limits, kernel process and cell limits are on the main page.
- **Proposed:** an **Advanced** section, folded by default, holding App ID, kernel, cell and SQL limits and the SQL temp folder. When policy or the environment sets a value, the field is read-only and says which.
- **Scope:** `settings.js`; `app_settings.save` refuses values that the environment overrides.
- **Acceptance:** a default install's Settings shows Account, Updates, Preview features, Usage data, Notifications, Help; Advanced is one click away; no "Saved" for a value that won't apply.
- **Tests:** `ui.test.cjs` for the fold and the read-only state; engine test for the env override.

### UX-007 Help opens the docs for the running build (P1)

- **Now:** `open_doc` always opens `blob/main/docs/...` on GitHub (`main.rs:958-970`).
- **Proposed:** open the published docs site; for Stable, the docs built at the Stable commit (or `blob/<commit>/docs/...` until the site keeps versions).
- **Scope:** `main.rs` `open_doc`, the docs workflow if it keeps a Stable copy.
- **Dependencies:** release management.
- **Acceptance:** on a Stable build, help shows text from that build's commit.
- **Tests:** Rust unit test for the URL.

### UX-008 Back, forward and reopen where you left off (P1)

- **Now:** `go()` keeps no history (`app.js:224-228`); a restart opens Home (`app.js:7`); "Open in Fabric" has no way back.
- **Proposed:** a history stack of `{view, tab}`; Back and Forward in the top bar with Alt+Left and Alt+Right; last view saved and restored on start (Home if it no longer exists); the breadcrumb built from it on every view below the rail.
- **Scope:** `app.js` navigation; each page's tab state exposed as one field.
- **Acceptance:** Back returns to the previous page and tab, including after Open in Fabric; restart reopens the last page.
- **Tests:** `ui.test.cjs` history cases.

### UX-009 One home for platform-wide things (P1)

- **Now:** keys, sources, domains, glossary, data products, requests and platform setup are each in two places, with two "New key table" dialogs writing to different stores (`meta.js:909`, `platform.js:236`).
- **Proposed:** see [navigation.md](navigation.md#target-structure). Platform manages; Data mesh shows read-only views that link to Platform; Settings loses Platform setup.
- **Scope:** owners agree the split first; mockup.
- **Acceptance:** each concept has one editing screen; the command bar finds it.
- **Tests:** browser tests for each moved screen.

### UX-010 Monitor: four segments (P1)

- **Now:** six segments, eight with open PRs (#145, #163, Run as).
- **Proposed:** Runs, Schedules, Operations (Workloads, Queue, Agents, Run as, Incidents as views), Cost.
- **Dependencies:** workload management thread; Chris's open card on Run as.
- **Acceptance:** Monitor shows four segments; every old command still lands on the right view.

### UX-011 One error panel and Retry (P1)

- **Now:** raw exception text is the main message in about ten places (`app.js:1303,1379,1600`, `shortcuts.js:240,245,349,394`, `deps.js:128`, `notebook.js:1134`); toasts have no Retry, use `role=status` and vanish after 8 seconds; 48 silent `.catch(() => …)` hide failures, including help links that do nothing (`settings.js:69,288,290`).
- **Proposed:** `errorPanel(err, {retry})` (sentence, what to do, Retry, Details expander with Copy); `attempt(fn, label, {retry})` adds a Retry action to the toast; error toasts use `role=alert` and pause on hover; data panels show "Couldn't load · Retry".
- **Scope:** `activity.js`, `app.js`; then each call site as owners touch it.
- **Acceptance:** no screen shows a Python exception name as its first line.
- **Tests:** `ui.test.cjs` for the panel; a check that `errorPanel` is used in the listed sites.

### UX-012 Keyboard access (P1)

- **Now:** clickable table rows and cards can't be reached by keyboard (11 places); `showMenu` (account, Publish options, Git) has no keyboard support and Esc doesn't close it (seen in the running UI); 13 tab implementations, 4 with ARIA, none with arrow keys; the command bar doesn't announce the selected item.
- **Proposed:** shared `clickRow()`, `menu()`, `tabs()` components with roles, focus and arrow keys; `aria-current` on the rail (done in HD-044, with the command bar now a modal dialog).
- **Dependencies:** enterprise readiness owns accessibility.
- **Acceptance:** every W1-W10 path can be done with the keyboard alone.
- **Tests:** browser tests driven by keyboard only.

### UX-013 Engine busy (P1)

- **Now:** the shell holds one lock for each engine call (`main.rs:299-327`); sign-in, publish and runs have no time limit, so everything else waits with no sign why.
- **Proposed:** a top-bar indicator "Working: <what> · Stop" after two seconds; long calls move to start-and-poll.
- **Dependencies:** codebase audit and hardening (also in the earlier honest review).

### UX-014 Never claim all is well when unchecked (P1)

- **Now:** Home says "Nothing needs your attention" when run history fails to load (`app.js:411-412`); Fabric job tracking stops after one failed poll (`activity.js:167-170`).
- **Proposed:** "Couldn't read run history · Retry"; poll with backoff and mark items "status unknown" after repeated failures.

### UX-015 Pickers instead of typed IDs and paths (P2)

- **Now and proposed:** the table in [configuration.md](configuration.md#typed-inputs-where-the-app-already-has-a-list).
- **Scope:** one native folder picker in the shell (shared), then one PR per owner.

### UX-016 One environment picker (P2)

- **Now:** five independent pickers; the top-bar one shows on two pages.
- **Proposed:** one `state.env`, shown in the top bar on every environment-aware page, labelled "Environment".

### UX-017 Plain words (P2)

- **Now:** `LH_Keys` (17 places), `LH_Meta`, `SQL_Platform`, save gate, Type 2 dimension, watermark, V-Order, Unknown member -1, MCP (stdio), object ID; Spark SQL vs DuckDB in one page.
- **Proposed:** say what it does, with the technical name in a tooltip or monospace after it ("the business key table (`LH_Keys`)"); a glossary page in docs linked from tooltips.

### UX-018 One confirmation for destructive actions (P2)

- **Now:** removing a Fabric or PC schedule has no confirmation (`schedules.js:49-51,198-201`); overwrites ("Replace the table?", "Overwrite these tables twice?") use a plain confirm with a navy OK.
- **Also:** the engineering quality review (QA-019, PR #176, spec PR-QA-10 to PR-QA-13 in `docs/quality/remediation.md`) found 17 destructive actions that `confirmDialog` shows with the destructive choice as the primary button, and 13 different wordings for the same kind of confirmation.
- **Proposed:** `confirmDialog(…, {danger: true, typeToConfirm})`: danger styling for every delete and overwrite, with Cancel as the default button; one wording pattern ("Delete *name*?", what is lost, then the action verb); typed name for overwriting a table or promoting to Prod.
- **Why:** AGENTS.md: protect OneLake data; no operation bypasses confirmation.

### UX-019 Help everywhere (P2)

- **Proposed:** help links on the 11 screens without one; the Preview list in the docs generated from `PREVIEW_FEATURES` (or checked by a test); a Keyboard shortcuts dialog from one table that the handlers also use; troubleshooting rows for each Preview feature as it leaves Preview.

### UX-021 Preview tag on Fabric writes (P1)

- **Now:** seven actions write to Fabric but have no tenant test and no Preview tag (QA-020, PR #176): Promote, Publish, Create workspace, Sync keys, variable library, quarantine replay, Run all in Fabric.
- **Proposed:** each gets the Preview tag through `PREVIEW_FEATURES` until a tenant smoke test covers it; a UI test fails when a Fabric-writing action is neither tenant-tested nor Preview.
- **Why:** AGENTS.md: untested Fabric integrations must keep Preview labeling. Usability gate UG-2 checks it in each release review.

### UX-020 Show Home at once (P2)

- **Now:** nothing renders until the component and release check returns (`app.js:2619-2622`); about 85 plain "Loading…" strings; no shared empty state.
- **Proposed:** render Home with placeholders immediately; run the release check alongside; `loading(label)` and `emptyState({icon, title, text, action})` shared components.

## Pull requests

Each PR is small, tested and independently mergeable. PRs that change visible layout get a mockup first and stay draft until Chris reacts.

| PR | Contains | Mockup first? | Who builds | Risk |
| --- | --- | --- | --- | --- |
| **PR-UX-01** Command bar and help links | UX-001, UX-002 | No | This oversight (shared code) | Low |
| PR-UX-02 Errors you can act on | UX-003, UX-011 (shared part) | No | This oversight for the shared part; sign-in mapping with enterprise readiness | Low |
| PR-UX-03 First run and Settings | UX-005, UX-006 | Yes | This oversight (Settings), enterprise readiness (Home first run) | Medium |
| PR-UX-04 Navigation history and fast start | UX-008, UX-020 | Small (Back/Forward placement) | This oversight | Medium |
| PR-UX-05 Help that matches the build | UX-007, UX-019 | No | Release management (shell), this oversight (docs, links, shortcuts) | Low |
| PR-UX-06 Keyboard components | UX-012 | No | Enterprise readiness, with this oversight | Medium |
| PR-UX-07 One home per concept | UX-009, UX-010, UX-016 | Yes (attached to the audit PR) | Owners | Medium-high |
| PR-UX-08 Pickers | UX-015 | No | Shell folder picker here, then owners | Low each |
| PR-UX-09 Local work in Create | UX-004 | Yes | Local data engine program | Medium |
| PR-UX-10 Honest status | UX-013, UX-014 | No | Hardening; workload management | Medium |
| PR-UX-11 Plain words | UX-017 | No | Owners, with a glossary from this oversight | Low |
| PR-UX-12 Confirmations | UX-018 | No | Shared helper here, call sites by owners | Low |
| PR-UX-13 Preview tags on Fabric writes | UX-021 | No | Each feature owner | Low |

### PR-UX-01 spec: Command bar and help links

- **Objective:** remove two defects every user meets and stop help links from breaking again.
- **Scope:** `app/ui/features.js` (palette `draw`), `app/ui/meta.js` and `app/ui/git.js` (one anchor each), a test. No layout change.
- **Existing components:** `openPalette`, `learnMore`, `XRAY_DOCS`, readiness `doc` fields.
- **Functional requirements:** the palette list contains only group headers and items; `learnMore` anchors and `open_doc` pages all resolve.
- **UX requirements:** none visible beyond the fix.
- **Technical constraints:** UI scripts share one global scope; new test helpers stay inside the test file.
- **Acceptance:** UX-001 and UX-002 acceptance.
- **Tests:** `node --test app/tests/ui.test.cjs` passes, including the new cases.
- **Docs:** none needed beyond this backlog.
- **Dependencies:** none.
- **Definition of done:** tests pass in CI; Release Manager UX review PASS.

### PR-UX-02 spec: Errors you can act on

- **Objective:** every failure says what happened and what to do, with Retry where it makes sense.
- **Scope:** `activity.js` (`friendlyError` order, Entra codes), `app.js` (`attempt` with `retry`, toast role and hover), new `errorPanel`, `auth.py` structured sign-in errors. Call sites in other owners' files only where listed in UX-011, each with a note to the owner.
- **Functional requirements:** known patterns are checked engine-first (the hung and stopped engine messages first; local exception names before Fabric HTTP patterns), so `FileNotFoundError` is never called "Fabric couldn't find that item" (`activity.js:53,63`).
- **Acceptance:** UX-003 and UX-011 acceptance.
- **Tests:** table-driven `friendlyError` tests for each mapped code and pattern order.
- **Docs:** `troubleshooting.md` rows for each new sign-in message.

### PR-UX-03 spec: First run and Settings

- **Objective:** a first-time user sees one clear next step; Settings shows what most people change.
- **Scope:** Home's first-run cards; Settings sections. Mockup first.
- **Acceptance:** UX-005 and UX-006 acceptance; existing `learnMore` anchors kept (`get-started`, `ready-to-work`, `settings`).
- **Tests:** browser screenshots signed out and signed in; `ui.test.cjs` for Advanced.
- **Docs:** user-guide Get started and Settings sections updated with new screenshots.

### PR-UX-04 to PR-UX-12

Specified by the issues above; each gets a full spec in this format when its owner picks it up, so the spec reflects main at that time.
