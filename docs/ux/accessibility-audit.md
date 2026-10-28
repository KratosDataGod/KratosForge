# Accessibility audit: automated, per page

*Made by `app/tests/browser/a11y-audit.test.cjs` (PB-063 part 1). Do not edit by hand: run*
`A11Y_REPORT=docs/ux/accessibility-audit.md node --test app/tests/browser/a11y-audit.test.cjs` *to refresh it.*

Rules: axe-core 4.10.2 with the WCAG 2.1 A and AA tags (wcag2a, wcag2aa, wcag21a, wcag21aa), color contrast among them. Keyboard: everything with a pointer cursor must be a Tab stop or inside one, and pressing Tab from the top must reach every Tab stop without looping (Escape then Tab leaves a code editor). Focus: each Tab stop on the page must show an outline or ring. Each page is checked in light and dark (the window's color scheme); the app has one theme, so dark is checked to show nothing breaks when Windows is set to dark.

The pages are opened signed in with test data (two workspaces, one lakehouse, two notebooks), so lists show rows where they can. Screens that need a live tenant to fill are audited in their empty or signed-in state. Part 2 (Narrator and keyboard on Windows) is a separate pass.

| Page | Scheme | Rules passed | Violations (serious + critical) | Needs a look | Tab stops | Unreachable | No focus ring |
|---|---|---|---|---|---|---|---|
| Home | light | 15 | 0 (0) | color-contrast (2) | 12 | 0 | 0 |
| Home | dark | 15 | 0 (0) | color-contrast (2) | 0 | 0 | 0 |
| Create | light | 4 | 0 (0) | none | 5 | 0 | 0 |
| Create | dark | 4 | 0 (0) | none | 0 | 0 | 0 |
| Workspaces | light | 17 | 0 (0) | color-contrast (2) | 15 | 0 | 0 |
| Workspaces | dark | 17 | 0 (0) | color-contrast (2) | 0 | 0 | 0 |
| OneLake | light | 17 | 0 (0) | none | 8 | 0 | 0 |
| OneLake | dark | 17 | 0 (0) | none | 0 | 0 | 0 |
| Data mesh | light | 14 | 0 (0) | none | 9 | 0 | 0 |
| Data mesh | dark | 14 | 0 (0) | none | 0 | 0 | 0 |
| Platform | light | 14 | 0 (0) | none | 8 | 0 | 0 |
| Platform | dark | 14 | 0 (0) | none | 0 | 0 | 0 |
| Optimize | light | 14 | 0 (0) | none | 5 | 0 | 0 |
| Optimize | dark | 14 | 0 (0) | none | 0 | 0 | 0 |
| Monitor | light | 18 | 0 (0) | none | 16 | 0 | 0 |
| Monitor | dark | 18 | 0 (0) | none | 0 | 0 | 0 |
| Source control | light | 5 | 0 (0) | none | 0 | 0 | 0 |
| Source control | dark | 5 | 0 (0) | none | 0 | 0 | 0 |
| Settings | light | 21 | 0 (0) | none | 32 | 0 | 0 |
| Settings | dark | 21 | 0 (0) | none | 0 | 0 | 0 |
| Notebook | light | 17 | 1 (1) | color-contrast (5) | 32 | 1 | 0 |
| Notebook | dark | 17 | 1 (1) | color-contrast (5) | 0 | 0 | 0 |
| SQL query | light | 22 | 1 (1) | color-contrast (2) | 21 | 0 | 1 |
| SQL query | dark | 22 | 1 (1) | color-contrast (2) | 0 | 0 | 0 |
| Command bar (Ctrl+K) | light | 17 | 1 (1) | none | 98 | 0 | 0 |
| Command bar (Ctrl+K) | dark | 17 | 1 (1) | none | 0 | 0 | 0 |
| Shell (top bar and rail) | light | 13 | 0 (0) | none | 18 | 0 | 0 |
| Shell (top bar and rail) | dark | 13 | 0 (0) | none | 0 | 0 | 0 |
| Confirm dialog | light | 12 | 0 (0) | none | 3 | 0 | 0 |
| Confirm dialog | dark | 12 | 0 (0) | none | 0 | 0 | 0 |

## Findings

Filed to be fixed in [#905](https://github.com/KratosDataGod/KratosForge/issues/905); the test fails on any serious or critical rule violation, or keyboard or focus finding, not listed here.

- **Notebook** (light): aria-input-field-name, serious: ARIA input fields must have an accessible name. At `div[data-language="python"]`, `div[data-language="sql"]`
- **Notebook**: keyboard: Tab returns to button.tool.small "Run cell", 12 Tab stops later never reached (Escape, then Tab, used to leave each code editor)
- **Notebook** (dark): aria-input-field-name, serious: ARIA input fields must have an accessible name. At `div[data-language="python"]`, `div[data-language="sql"]`
- **SQL query** (light): aria-required-children, critical: Certain ARIA roles must contain particular children. At `.sql-tabs`
- **SQL query**: `div.cm-content "SQL query"` shows no focus ring
- **SQL query** (dark): aria-required-children, critical: Certain ARIA roles must contain particular children. At `.sql-tabs`
- **Command bar (Ctrl+K)** (light): aria-input-field-name, serious: ARIA input fields must have an accessible name. At `.palette-list`
- **Command bar (Ctrl+K)** (dark): aria-input-field-name, serious: ARIA input fields must have an accessible name. At `.palette-list`

## Needs a look

Checks axe couldn't decide by itself (for example text over a gradient or an image). Each needs a person to judge.

- **Home**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `.foldable.card.pad:nth-child(4) > .fold-head > .fold-toggle[title="Hide"] > .chev`, `.foldable.card.pad:nth-child(10) > .fold-head > .fold-toggle[title="Hide"] > .chev`
- **Workspaces**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `.click:nth-child(1) > td:nth-child(3) > .star[title="Add to favorites"][aria-label="Add to favorites"]`, `.click:nth-child(2) > td:nth-child(3) > .star[title="Add to favorites"][aria-label="Add to favorites"]`
- **Notebook**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `.markdown > .gutter > .cell-grip[draggable="true"][title="Drag to move this cell"]`, `div[data-cell="2"] > .cell-box > .editor > .cm-editor.ͼ1.ͼ2 > .cm-scroller > .cm-gutters.cm-gutters-before[aria-hidden="true"] > .cm-lineNumbers.cm-gutter > .cm-gutterElement:nth-child(2)`, `div[data-cell="2"] > .cell-box > .editor > .cm-editor.ͼ1.ͼ2 > .cm-scroller > .cm-gutters.cm-gutters-before[aria-hidden="true"] > .cm-lineNumbers.cm-gutter > .cm-gutterElement:nth-child(3)`, `div[data-cell="3"] > .cell-box > .editor > .cm-editor.ͼ1.ͼ2 > .cm-scroller > .cm-gutters.cm-gutters-before[aria-hidden="true"] > .cm-lineNumbers.cm-gutter > .cm-gutterElement:nth-child(2)`, `div[data-cell="3"] > .cell-box > .editor > .cm-editor.ͼ1.ͼ2 > .cm-scroller > .cm-gutters.cm-gutters-before[aria-hidden="true"] > .cm-lineNumbers.cm-gutter > .cm-gutterElement:nth-child(3)`
- **SQL query**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `.cm-lineNumbers > .cm-activeLineGutter.cm-gutterElement`, `.cm-gutterElement:nth-child(3)`
