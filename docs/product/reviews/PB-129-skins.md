# Implementation review: PB-129 Platform skins and experience parity (FEAT-SHL-04, FEAT-EXT-05)

| Field | Value |
| --- | --- |
| Backlog item | PB-129 Platform skins and experience parity (FEAT-SHL-04, FEAT-EXT-05) |
| Pull request | #1756, #1759, #1770, #1772, #1773, #1816, #1837, #1847, #1879, #1891, #1896, #1904, #1921 to #1925, merged; main at 2394fc1a |
| Package | none |
| Reviewer | Product management thread. It didn't write these PRs. The owner's evidence list is in project files (skins/pb-129-acceptance-evidence.md) |
| Date | 2026-11-06 |
| Environment | Linux container, engine from source in a Python 3.12 venv, Playwright Chromium. No Windows, no tenant, axe-core not installed here (CI runs the axe checks) |

## Outcome

**Accept with documented limitations** (recommended). Limitations:

- Appearance and workflow come together in one skin. Only the color mode is chosen on its own, so you can't pair one skin's rail with another skin's colors.
- No Import skin screen yet. Import validation is built and tested in the engine, but only the four built-in skins can be chosen.
- The demonstration mocks the cloud parts. Nothing ran on Windows (WebView2) or a tenant.

Recommended by: Product management thread. Decided by (product owner): pending.

## Acceptance criteria

| # | Criterion (copied from the backlog item) | Result | Evidence |
| ---: | --- | --- | --- |
| 1 | Appearance, workflow, platform and engine are independent settings | Partly | Platform (connection profiles) and engine (engine registry) are separate from the skin. A skin can't carry `engine` or `credentials` (`test_unknown_keys_are_refused_not_ignored`), and switching makes no engine or sign-in call (skin-switch.test.cjs). Appearance and workflow are bundled in one skin, with only the color mode chosen apart |
| 2 | A skin is declarative configuration, validated on import, and can't change credentials, permissions, engine behaviour or project data | Partly | A skin is JSON (`fabric-desktop.skin/1`), checked by `skins.validate`: only known keys, listed tokens with plain colors, a 4.5:1 text contrast floor, page-id rail entries, plain labels and a 64 KB cap. test_skins.py: 48 passed here. There is no import screen yet, so validation on import is engine-only |
| 3 | Switching skins keeps open work, connections and execution settings | Met | skin-switch.test.cjs and skins-demo.test.cjs (#1904) keep a notebook's unsaved edit and SQL tabs across all four skins, and make no sign-in, engine or workspace call. The lakehouse in the demo is mocked |
| 4 | Every skin keeps local-first, multi-engine, optimization, training and deployment features reachable | Met | Each built-in skin's rail is the same 8 pages, reordered (`test_the_fabric_skin_is_light_and_orders_the_rail_without_hiding_a_page`, `test_the_databricks_and_snowflake_skins_lead_with_their_platforms_pages`). Academy, Source control and Settings stay in the lower rail. The engine chip "This PC · no Fabric job" stays visible. skins-a11y.test.cjs (#1922) opens every rail page in each skin |
| 5 | The end-to-end demonstration in the brief is recorded, with mocked parts named | Met | docs/skins-demo.md and skins-demo.test.cjs (#1904): every mocked name starts with MOCK, and a label shows for the whole recording. The page lists its own limits: Snowflake isn't a SQL page target, Cost and savings doesn't count SQL page runs, and the "project" is an open notebook and SQL tab |

## Checks

| Check | Result | Evidence or note |
| --- | --- | --- |
| Full suites pass | Partly | test_skins.py 48 passed. skin-switch, skins-demo and skins-a11y browser tests: 17 passed, 5 skipped (the 4 axe checks need axe-core, which CI has, and 1 more) |
| Docs match the code | Yes | docs/skins.md, docs/skins-demo.md, docs/parity.md |
| Accessibility of new controls | Partly | Keyboard and reduced-motion checks passed here; axe ran in CI only |

## Findings

None blocking. Dark mode, the Import skin screen, a Windows WebView2 check and the command-placement screen are not done. None of them is an acceptance criterion, and the command-placement screen waits on the product owner's mockup reaction.
