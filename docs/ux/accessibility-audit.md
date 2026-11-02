# Accessibility audit: automated, per page

*Made by `app/tests/browser/a11y-audit.test.cjs` (PB-063 part 1). Do not edit by hand: run*
`A11Y_REPORT=docs/ux/accessibility-audit.md node --test app/tests/browser/a11y-audit.test.cjs` *to refresh it.*

Rules: axe-core 4.10.2 with the WCAG 2.1 A and AA tags (wcag2a, wcag2aa, wcag21a, wcag21aa), color contrast among them. Keyboard: everything with a pointer cursor must be a Tab stop or inside one, and pressing Tab from the top must reach every Tab stop without looping (Escape then Tab leaves a code editor). Focus: each Tab stop on the page must show an outline or ring. Each page is checked in light and dark (the window's color scheme); the app has one theme, so dark is checked to show nothing breaks when Windows is set to dark.

The pages are opened signed in with test data (two workspaces, one lakehouse, two notebooks), so lists show rows where they can. Screens that need a live tenant to fill are audited in their empty or signed-in state. Part 2 (Narrator and keyboard on Windows) is a separate pass.

| Page | Scheme | Rules passed | Violations (serious + critical) | Needs a look | Tab stops | Unreachable | No focus ring |
|---|---|---|---|---|---|---|---|
| Home | light | 15 | 0 (0) | color-contrast (2) | 21 | 0 | 0 |
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
| Learn | light | 19 | 0 (0) | none | 14 | 0 | 0 |
| Learn | dark | 19 | 0 (0) | none | 0 | 0 | 0 |
| Source control | light | 5 | 0 (0) | none | 0 | 0 | 0 |
| Source control | dark | 5 | 0 (0) | none | 0 | 0 | 0 |
| Settings | light | 21 | 0 (0) | none | 35 | 0 | 0 |
| Settings | dark | 21 | 0 (0) | none | 0 | 0 | 0 |
| Notebook | light | 18 | 0 (0) | color-contrast (2) | 38 | 0 | 0 |
| Notebook | dark | 18 | 0 (0) | color-contrast (2) | 0 | 0 | 0 |
| SQL query | light | 22 | 0 (0) | none | 21 | 0 | 0 |
| SQL query | dark | 22 | 0 (0) | none | 0 | 0 | 0 |
| Command bar (Ctrl+K) | light | 18 | 0 (0) | none | 102 | 0 | 0 |
| Command bar (Ctrl+K) | dark | 18 | 0 (0) | none | 0 | 0 | 0 |
| Data mesh entity | light | 15 | 0 (0) | color-contrast (5) | 14 | 0 | 0 |
| Data mesh entity | dark | 15 | 0 (0) | color-contrast (5) | 0 | 0 | 0 |
| Shell (top bar and rail) | light | 13 | 0 (0) | none | 19 | 0 | 0 |
| Shell (top bar and rail) | dark | 13 | 0 (0) | none | 0 | 0 | 0 |
| Confirm dialog | light | 12 | 0 (0) | none | 3 | 0 | 0 |
| Confirm dialog | dark | 12 | 0 (0) | none | 0 | 0 | 0 |
| Profile | light | 18 | 0 (0) | none | 8 | 0 | 0 |
| Profile | dark | 18 | 0 (0) | none | 0 | 0 | 0 |
| Learn lab | light | 16 | 0 (0) | color-contrast (1) | 8 | 0 | 0 |
| Learn lab | dark | 16 | 0 (0) | color-contrast (1) | 0 | 0 | 0 |
| Settings > Integrations | light | 19 | 0 (0) | none | 12 | 0 | 0 |
| Settings > Integrations | dark | 19 | 0 (0) | none | 0 | 0 | 0 |
| AI tool approval dialog | light | 15 | 0 (0) | none | 3 | 0 | 0 |
| AI tool approval dialog | dark | 15 | 0 (0) | none | 0 | 0 | 0 |
| Report a problem dialog | light | 14 | 0 (0) | color-contrast (2) | 6 | 0 | 0 |
| Report a problem dialog | dark | 14 | 0 (0) | color-contrast (2) | 0 | 0 | 0 |

## Findings

Filed to be fixed in [#905](https://github.com/KratosDataGod/KratosForge/issues/905); the test fails on any serious or critical rule violation, or keyboard or focus finding, not listed here.

None.

## Needs a look

Checks axe couldn't decide by itself (for example a glyph, a line number, or text over a gradient or an image), each with a person's verdict and the measured contrast.

- **Home**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `.get-started > .fold-head > .fold-toggle[title="Hide"] > .chev`, `.foldable.card.pad:nth-child(6) > .fold-head > .fold-toggle[title="Hide"] > .chev`. Pass: the fold arrow is #616161 on white, 6.2:1 (3:1 needed for a control's graphic).
- **Workspaces**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `.click:nth-child(1) > td:nth-child(3) > .star[title="Add to favorites"][aria-label="Add to favorites"]`, `.click:nth-child(2) > td:nth-child(3) > .star[title="Add to favorites"][aria-label="Add to favorites"]`. Pass: the favorite star is #8a8a8a, 3.4:1 on white and 3.3:1 on #fafafa (3:1 needed for a control's graphic); its button is named.
- **Notebook**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `.markdown > .gutter > .cell-grip[draggable="true"][title="Drag to move this cell"]`, `span[title="Fold line"]`. Pass: the drag grip is #616161, 6.2:1, and hidden from assistive tech; the cell's Move up and Move down buttons do the same from the keyboard. Pass: the fold marker is #616161 on white, 6.2:1.
- **Data mesh entity**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `.arrow[aria-hidden="true"]:nth-child(2)`, `.arrow[aria-hidden="true"]:nth-child(4)`, `.arrow[aria-hidden="true"]:nth-child(6)`, `.arrow[aria-hidden="true"]:nth-child(8)`, `#entity-toml`. Pass: the step arrows are #8a8a8a, 3.4:1 on white and 3.3:1 on #fafafa, and hidden from assistive tech (the steps' order is in the text). Pass: the entity file's text is #242424 on #fafafa, 14.9:1 (axe sees the box as partly covered).
- **Learn lab**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `.learn-mark`. Pass: an exercise check's mark is #242424 on white, 15.5:1 (passed is #1f7a4a, 5.3:1), and hidden from assistive tech (the check's text says it).
- **Report a problem dialog**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `textarea[rows="3"]`, `.mono`. Pass: What happened is #242424 on white, 15.5:1, and its placeholder #757575, 4.6:1. Pass: the diagnostics are #424242 on #fafafa, 9.6:1.
