# Accessibility audit: automated, per page

*Made by `app/tests/browser/a11y-audit.test.cjs` (PB-063 part 1). Do not edit by hand: run*
`A11Y_REPORT=docs/ux/accessibility-audit.md node --test app/tests/browser/a11y-audit.test.cjs` *to refresh it.*

Rules: axe-core 4.10.2 with the WCAG 2.1 A and AA tags (wcag2a, wcag2aa, wcag21a, wcag21aa), color contrast among them. Keyboard: everything with a pointer cursor must be a Tab stop or inside one, and pressing Tab from the top must reach every Tab stop without looping (Escape then Tab leaves a code editor). Focus: each Tab stop on the page must show an outline or ring. Each page is checked in light and dark (the window's color scheme); the app has one theme, so dark is checked to show nothing breaks when Windows is set to dark. At 200% (the 1440x940 window laid out as 720x470) no page may scroll sideways as a whole, and each Tab stop must be on screen and not under something when it has focus. In a Windows contrast theme (forced colors) contrast is the theme's, so it isn't measured, and each Tab stop must still show a ring (the theme removes rings drawn as box shadows). The keyboard walk runs in light, at 200% and in the contrast theme. Dialogs opened from buttons are checked the same way in `a11y-dialogs.test.cjs`.

The pages are opened signed in with test data (two workspaces, one lakehouse, two notebooks), so lists show rows where they can. Screens that need a live tenant to fill are audited in their empty or signed-in state. Part 2 (Narrator and keyboard on Windows) is a separate pass.

| Page | Scheme | Rules passed | Violations (serious + critical) | Needs a look | Tab stops | Unreachable | No focus ring |
|---|---|---|---|---|---|---|---|
| Home | light | 15 | 0 (0) | color-contrast (5) | 23 | 0 | 0 |
| Home | dark | 20 | 0 (0) | color-contrast (5) | 0 | 0 | 0 |
| Home | zoom-200 | 19 | 0 (0) | color-contrast (5) | 23 | 0 | 0 |
| Home | high-contrast | 19 | 0 (0) | none | 23 | 0 | 0 |
| Create | light | 4 | 0 (0) | none | 6 | 0 | 0 |
| Create | dark | 4 | 0 (0) | none | 0 | 0 | 0 |
| Create | zoom-200 | 5 | 0 (0) | none | 6 | 0 | 0 |
| Create | high-contrast | 3 | 0 (0) | none | 6 | 0 | 0 |
| Workspaces | light | 17 | 0 (0) | color-contrast (2) | 17 | 0 | 0 |
| Workspaces | dark | 17 | 0 (0) | color-contrast (2) | 0 | 0 | 0 |
| Workspaces | zoom-200 | 18 | 0 (0) | color-contrast (2) | 17 | 0 | 0 |
| Workspaces | high-contrast | 16 | 0 (0) | none | 17 | 0 | 0 |
| OneLake | light | 17 | 0 (0) | none | 8 | 0 | 0 |
| OneLake | dark | 17 | 0 (0) | none | 0 | 0 | 0 |
| OneLake | zoom-200 | 17 | 0 (0) | none | 8 | 0 | 0 |
| OneLake | high-contrast | 16 | 0 (0) | none | 8 | 0 | 0 |
| Data mesh | light | 14 | 0 (0) | none | 9 | 0 | 0 |
| Data mesh | dark | 14 | 0 (0) | none | 0 | 0 | 0 |
| Data mesh | zoom-200 | 14 | 0 (0) | none | 9 | 0 | 0 |
| Data mesh | high-contrast | 13 | 0 (0) | none | 9 | 0 | 0 |
| Platform | light | 14 | 0 (0) | none | 8 | 0 | 0 |
| Platform | dark | 14 | 0 (0) | none | 0 | 0 | 0 |
| Platform | zoom-200 | 14 | 0 (0) | none | 8 | 0 | 0 |
| Platform | high-contrast | 13 | 0 (0) | none | 8 | 0 | 0 |
| Optimize | light | 14 | 0 (0) | none | 5 | 0 | 0 |
| Optimize | dark | 14 | 0 (0) | none | 0 | 0 | 0 |
| Optimize | zoom-200 | 15 | 0 (0) | none | 5 | 0 | 0 |
| Optimize | high-contrast | 13 | 0 (0) | none | 5 | 0 | 0 |
| Monitor | light | 18 | 0 (0) | color-contrast (3) | 18 | 0 | 0 |
| Monitor | dark | 18 | 0 (0) | color-contrast (3) | 0 | 0 | 0 |
| Monitor | zoom-200 | 18 | 0 (0) | color-contrast (3) | 18 | 0 | 0 |
| Monitor | high-contrast | 17 | 0 (0) | none | 18 | 0 | 0 |
| Academy | light | 14 | 0 (0) | none | 7 | 0 | 0 |
| Academy | dark | 14 | 0 (0) | none | 0 | 0 | 0 |
| Academy | zoom-200 | 14 | 0 (0) | none | 7 | 0 | 0 |
| Academy | high-contrast | 13 | 0 (0) | none | 7 | 0 | 0 |
| Source control | light | 7 | 0 (0) | none | 1 | 0 | 0 |
| Source control | dark | 7 | 0 (0) | none | 0 | 0 | 0 |
| Source control | zoom-200 | 7 | 0 (0) | none | 1 | 0 | 0 |
| Source control | high-contrast | 6 | 0 (0) | none | 1 | 0 | 0 |
| Settings | light | 21 | 0 (0) | none | 45 | 0 | 0 |
| Settings | dark | 21 | 0 (0) | none | 0 | 0 | 0 |
| Settings | zoom-200 | 21 | 0 (0) | none | 45 | 0 | 0 |
| Settings | high-contrast | 20 | 0 (0) | none | 45 | 0 | 0 |
| Notebook | light | 18 | 0 (0) | color-contrast (2) | 38 | 0 | 0 |
| Notebook | dark | 18 | 0 (0) | color-contrast (2) | 0 | 0 | 0 |
| Notebook | zoom-200 | 19 | 0 (0) | color-contrast (4) | 38 | 0 | 0 |
| Notebook | high-contrast | 17 | 0 (0) | none | 38 | 0 | 0 |
| SQL query | light | 22 | 0 (0) | none | 21 | 0 | 0 |
| SQL query | dark | 22 | 0 (0) | none | 0 | 0 | 0 |
| SQL query | zoom-200 | 23 | 0 (0) | none | 21 | 0 | 0 |
| SQL query | high-contrast | 21 | 0 (0) | none | 21 | 0 | 0 |
| Command bar (Ctrl+K) | light | 17 | 0 (0) | none | 108 | 0 | 0 |
| Command bar (Ctrl+K) | dark | 17 | 0 (0) | none | 0 | 0 | 0 |
| Command bar (Ctrl+K) | zoom-200 | 17 | 0 (0) | none | 108 | 0 | 0 |
| Command bar (Ctrl+K) | high-contrast | 16 | 0 (0) | none | 108 | 0 | 0 |
| Data mesh entity | light | 15 | 0 (0) | color-contrast (5) | 14 | 0 | 0 |
| Data mesh entity | dark | 15 | 0 (0) | color-contrast (5) | 0 | 0 | 0 |
| Data mesh entity | zoom-200 | 15 | 0 (0) | color-contrast (4) | 14 | 0 | 0 |
| Data mesh entity | high-contrast | 14 | 0 (0) | none | 14 | 0 | 0 |
| Shell (top bar and rail) | light | 13 | 0 (0) | none | 19 | 0 | 0 |
| Shell (top bar and rail) | dark | 13 | 0 (0) | none | 0 | 0 | 0 |
| Shell (top bar and rail) | zoom-200 | 14 | 0 (0) | none | 19 | 0 | 0 |
| Shell (top bar and rail) | high-contrast | 12 | 0 (0) | none | 19 | 0 | 0 |
| Confirm dialog | light | 13 | 0 (0) | none | 3 | 0 | 0 |
| Confirm dialog | dark | 13 | 0 (0) | none | 0 | 0 | 0 |
| Confirm dialog | zoom-200 | 13 | 0 (0) | none | 3 | 0 | 0 |
| Confirm dialog | high-contrast | 12 | 0 (0) | none | 3 | 0 | 0 |
| Profile | light | 19 | 0 (0) | none | 12 | 0 | 0 |
| Profile | dark | 19 | 0 (0) | none | 0 | 0 | 0 |
| Profile | zoom-200 | 19 | 0 (0) | none | 12 | 0 | 0 |
| Profile | high-contrast | 18 | 0 (0) | none | 12 | 0 | 0 |
| Learn lab | light | 16 | 0 (0) | color-contrast (1) | 8 | 0 | 0 |
| Learn lab | dark | 16 | 0 (0) | color-contrast (1) | 0 | 0 | 0 |
| Learn lab | zoom-200 | 16 | 0 (0) | none | 8 | 0 | 0 |
| Learn lab | high-contrast | 15 | 0 (0) | none | 8 | 0 | 0 |
| Settings > Integrations | light | 19 | 0 (0) | none | 13 | 0 | 0 |
| Settings > Integrations | dark | 19 | 0 (0) | none | 0 | 0 | 0 |
| Settings > Integrations | zoom-200 | 19 | 0 (0) | none | 13 | 0 | 0 |
| Settings > Integrations | high-contrast | 18 | 0 (0) | none | 13 | 0 | 0 |
| AI tool approval dialog | light | 15 | 0 (0) | none | 3 | 0 | 0 |
| AI tool approval dialog | dark | 15 | 0 (0) | none | 0 | 0 | 0 |
| AI tool approval dialog | zoom-200 | 15 | 0 (0) | none | 3 | 0 | 0 |
| AI tool approval dialog | high-contrast | 14 | 0 (0) | none | 3 | 0 | 0 |
| Report a problem dialog | light | 14 | 0 (0) | color-contrast (2) | 6 | 0 | 0 |
| Report a problem dialog | dark | 14 | 0 (0) | color-contrast (2) | 0 | 0 | 0 |
| Report a problem dialog | zoom-200 | 14 | 0 (0) | color-contrast (2) | 6 | 0 | 0 |
| Report a problem dialog | high-contrast | 13 | 0 (0) | none | 6 | 0 | 0 |
| Keyboard shortcuts dialog | light | 17 | 0 (0) | color-contrast (1) | 4 | 0 | 0 |
| Keyboard shortcuts dialog | dark | 17 | 0 (0) | color-contrast (1) | 0 | 0 | 0 |
| Keyboard shortcuts dialog | zoom-200 | 16 | 0 (0) | none | 4 | 0 | 0 |
| Keyboard shortcuts dialog | high-contrast | 16 | 0 (0) | none | 4 | 0 | 0 |
| Engine picker's How to choose dialog | light | 17 | 0 (0) | color-contrast (4) | 5 | 0 | 0 |
| Engine picker's How to choose dialog | dark | 17 | 0 (0) | color-contrast (4) | 0 | 0 | 0 |
| Engine picker's How to choose dialog | zoom-200 | 17 | 0 (0) | color-contrast (2) | 5 | 0 | 0 |
| Engine picker's How to choose dialog | high-contrast | 16 | 0 (0) | none | 5 | 0 | 0 |

## Findings

Filed to be fixed in [#905](https://github.com/KratosDataGod/KratosForge/issues/905); the test fails on any serious or critical rule violation, or keyboard or focus finding, not listed here.

None.

## Needs a look

Checks axe couldn't decide by itself (for example a glyph, a line number, or text over a gradient or an image), each with a person's verdict and the measured contrast.

- **Home**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `.home-hero-kicker`, `.home-hero-title`, `h1`, `.hero-outline`, `.get-started > .fold-head > .fold-toggle[title="Hide"] > .chev`. Pass: the hero's text and buttons sit on the navy sky: the gradient #01203d to #062e57, and over the picture's empty left quarter, faded into that same sky (its sky runs #10223f to #1b3664; the text always ends before the cape, at 27% of the picture). Worst case, on #1b3664: the title #ffffff 11.9:1, the welcome #e6ecf5 10.1:1, the kicker #f8991c 5.4:1. On the plain gradient (axe names the welcome h1 and the outline button by their short selectors when they sit over the picture): the title #ffffff 13.7:1 at worst, the welcome #e6ecf5 11.5:1, the kicker #f8991c 6.2:1, the outline button's label 13.7:1, and Continue is #0b1420 on its own #f8991c fill, 8.4:1 (axe can't read a picture). Pass: the fold arrow is #616161 on white, 6.2:1 (3:1 needed for a control's graphic).
- **Workspaces**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `.click:nth-child(1) > td:nth-child(3) > .star[title="Add to favorites"][aria-label="Add to favorites"]`, `.click:nth-child(2) > td:nth-child(3) > .star[title="Add to favorites"][aria-label="Add to favorites"]`. Pass: the favorite star is #8a8a8a, 3.4:1 on white and 3.3:1 on #fafafa (3:1 needed for a control's graphic); its button is named.
- **Monitor**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `.forge-title`, `.forge-title > span`, `.forge-sub`. Pass: on the Forge band's navy gradient (#000f22 to #0a2a4b, lightest taken), the title is #ffffff, 14.5:1, its state word at least 6.6:1 (#f8991c active, #7fd99b done 8.5:1, #ff9e9e failed 7.4:1, #c9d6ea quiet 9.9:1), the line under it #e6ecf5, 12.2:1, and the link to the table #ffcf8a, 10.1:1 (axe can't measure text on a gradient).
- **Notebook**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `.markdown > .gutter > .cell-grip[draggable="true"][title="Drag to move this cell"]`, `span[title="Fold line"]`. Pass: the drag grip is #616161, 6.2:1, and hidden from assistive tech; the cell's Move up and Move down buttons do the same from the keyboard. Pass: the fold marker is #616161 on white, 6.2:1.
- **Data mesh entity**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `.arrow[aria-hidden="true"]:nth-child(2)`, `.arrow[aria-hidden="true"]:nth-child(4)`, `.arrow[aria-hidden="true"]:nth-child(6)`, `.arrow[aria-hidden="true"]:nth-child(8)`, `#entity-toml`. Pass: the step arrows are #8a8a8a, 3.4:1 on white and 3.3:1 on #fafafa, and hidden from assistive tech (the steps' order is in the text). Pass: the entity file's text is #242424 on #fafafa, 14.9:1 (axe sees the box as partly covered).
- **Learn lab**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `.learn-mark`. Pass: an exercise check's mark is #242424 on white, 15.5:1 (passed is #1f7a4a, 5.3:1), and hidden from assistive tech (the check's text says it).
- **Report a problem dialog**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `textarea[rows="3"]`, `.mono`. Pass: What happened is #242424 on white, 15.5:1, and its placeholder #757575, 4.6:1. Pass: the diagnostics are #424242 on #fafafa, 9.6:1.
- **Keyboard shortcuts dialog**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `.text:nth-child(6) > .learn-more[href="#"]`. Pass: in both runs the notebook guide link is #1b3664 on the dialog's white, 12.0:1 (axe sees the list's last lines, which scroll under the dialog's buttons, as partly covered).
- **Engine picker's How to choose dialog**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `p:nth-child(10)`, `u[title="side-by-side.md"]`, `p:nth-child(10) > u:nth-child(2)`, `.scroll > b`. Pass: in both runs the decision pattern's text, links, headings and Copy example are #242424 on the dialog's white, 15.5:1, and the note beside Copy example is #616161, 6.2:1 (axe sees lines that scroll under the dialog's buttons as partly covered).
