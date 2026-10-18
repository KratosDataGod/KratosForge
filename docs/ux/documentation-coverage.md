# Documentation coverage matrix

Each feature, its doc page, whether its screen has a working help link, and whether the doc covers troubleshooting and limits. Checked on main `3bee0db`: every `learnMore(...)` and `open_doc` call in `app/ui`, the X-ray and readiness doc links in the engine, and every link in `docs/` (headings turned into GitHub-style anchors).

## How help opens today

- `learnMore(anchor)` (`features.js:83-87`) always opens `user-guide.md#anchor`.
- The shell's `open_doc` (`main.rs:958-970`) always builds `https://github.com/KratosDataGod/fabric-desktop/blob/main/docs/<page>`. A Stable user reads docs for `main`, which may describe behavior they don't have yet. The published docs site (built by `.github/workflows/docs.yml`) is not used.
- No test checks a help link the app opens. The docs build runs in strict mode, but only when `docs/` changes, and it can't see links that live in JavaScript.

## Coverage

✓ = present and correct · ✗ = missing · **broken** = the link opens the wrong place

| Feature | Screen | Help link on screen | Doc page | Troubleshooting | Limits | Gap |
| --- | --- | --- | --- | --- | --- | --- |
| Home, Ready to work, Get started | `app.js`, `settings.js` | ✓ | user-guide#home | partial | ✗ | Readiness rows compute doc links (`readiness.py`) but only one is ever shown |
| Create | `app.js:502` | ✓ | user-guide#create, notebooks.md | ✗ | ✗ | |
| Workspaces | `app.js:680` | ✓ | user-guide#workspaces | ✓ | ✗ | |
| Lakehouse page | `app.js` | ✗ | user-guide#a-lakehouse | ✗ | ✗ | No link |
| SQL query page | `app.js:1227` | ✗ | sql-execution.md, sql-compatibility.md | partial | ✓ | No link from the page to sql-execution.md |
| OneLake catalog | `app.js:942` | ✓ | user-guide#onelake | partial | ✗ | |
| Fabric page | `app.js:795` | ✗ | user-guide#fabric | ✓ | ✗ | No link |
| Monitor (runs) | `app.js:516` | ✓ | user-guide#monitor | partial | ✗ | |
| Data mesh page | `meta.js:114` | **broken** (`#metadata`; the heading is Data mesh) | metadata-guide.md, user-guide#data-mesh | ✓ | partial | Fix anchor |
| Entity page | `meta.js` | ✗ | metadata-guide.md | ✓ | ✗ | No link |
| Notebook | `notebook.js` | ✗ | notebooks.md | ✓ | ✗ | No link from the most used screen |
| Settings | `settings.js` | ✓ | user-guide#settings | ✗ | ✗ | |
| Source control (P) | `git.js:112-113` | **broken** (`#source-control`; the heading is "Source control (Preview)") | source-control.md | ✓ | ✓ | Fix anchor; link source-control.md directly |
| Platform (P) | `platform.js` | ✓ | platform.md | ✗ | ✓ | |
| Data mesh domains (P) | `mesh.js` | links to #platform | data-mesh.md | ✗ | ✗ | Link goes to Platform, not data-mesh.md |
| Workloads (P) | `workloads.js` | ✓ | user-guide#workloads-preview | ✗ | partial | Only a plan doc beyond the user guide |
| Incidents (P) | `incidents.js` | ✓ | user-guide#incidents-preview | ✗ | ✗ | |
| Agents (P) | `agents.js` | ✓ | agent.md | ✓ | ✓ | |
| External AI tools (P) | `integrations.js` | ✓ | ai/README.md | ✓ | ✓ | |
| Prove (P) | `prove.js` | ✓ | prove/how-proofs-work.md | ✗ | ✓ | |
| Side by side (P) | `sidebyside.js` | ✓ | side-by-side.md | ✗ | ✓ | |
| Estate X-ray (P) | `xray.js` | ✓ (7 anchors) | xray-fixes.md | per fix | ✗ | No prerequisites or permissions |
| Notebook from a template (P) | `wizard.js` | ✗ | notebooks.md (thin) | ✗ | ✗ | |
| Cell dependencies, Variable history (P) | `deps.js`, `debugging.js` | ✗ | notebooks.md | ✗ | ✗ | |
| Run in Fabric, Schedules (P) | `notebook.js`, `meta.js`, Monitor | Monitor only | notebooks.md, metadata-guide.md | ✓ | ✗ | |
| Cost, Capacity (P) | Monitor > Cost | generic #monitor | finops/cost-evidence.md, finops/capacity.md | capacity only | ✗ | Link the finops pages |
| OneLake shortcuts (P) | `shortcuts.js` | ✗ | shortcuts.md | ✗ | ✓ | Full page exists; the screen never links it |
| OneLake access (P) | `estate.js:375` | ✗ | user-guide#access-preview | ✗ | ✗ | |
| Table history, health, tests, semantic model, lineage, release records (P) | `meta.js`, `dataops.js` | ✗ | metadata-guide.md (a few lines each) | ✗ | ✗ | Thin; no prerequisites |
| Item preview (P) | `item-preview.js` | via #workspaces | user-guide#item-preview-preview | ✗ | ✓ | |
| Local data projects (P) | none yet | — | local-data.md | ✓ | ✓ | Docs ahead of the UI |

(P) = in `PREVIEW_FEATURES`.

**Totals:** 31 features. 18 have a working help link on their screen (some only to a general section), 2 have a broken one, 11 have none. 10 have troubleshooting and 6 have some. 10 document their limits and 2 partly.

## Other findings

- **Preview lists disagree.** The user guide's Preview features section lists 17, the docs home table 21, the code 26 (`features.js:37-64`). Generate the docs list from the code, or check it in a test (UX-019).
- **`troubleshooting.md` covers** startup, sign-in, workspaces, notebooks, metadata and source control only. Nothing for Shortcuts, Platform, Data mesh, Workloads, Incidents, Prove, Side by side, Cost, X-ray, Updates or Item preview.
- **No keyboard shortcut reference** in the app or the docs; `user-guide.md#using-the-keyboard` covers Tab, Esc and the skip link only.
- **Plain-text pointers** such as "docs/permissions.md says…" in error messages (`activity.js:47-50`, `capacity.py:53`) can't be clicked.
- **Stale architecture text:** `architecture/ai-integration.md` still says there is no MCP server or local API, and links to a feature branch.
- **The docs folder itself is healthy:** 1 broken link in 761 (the feature-branch link above), no orphan pages, everything in the site navigation.

## Definition of done for a feature's docs

A feature that adds or changes a screen is documented when:

1. Its page header ends with `learnMore("<anchor>")` or links its own page, and the anchor exists (checked by the test in UX-002).
2. Its doc says what it does, when to use it, prerequisites and permissions, steps, limits, and how local and Fabric runs differ where both exist.
3. Its common errors have a row in `troubleshooting.md`, or a "If it doesn't work" section on its page.
4. If it's in Preview, it's in `PREVIEW_FEATURES` and the generated Preview list.
