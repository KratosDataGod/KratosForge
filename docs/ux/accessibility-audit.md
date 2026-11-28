# Accessibility audit: automated, per page

*Made by `app/tests/browser/a11y-audit.test.cjs` (PB-063 part 1). Do not edit by hand: run*
`A11Y_REPORT=docs/ux/accessibility-audit.md node --test app/tests/browser/a11y-audit.test.cjs` *to refresh it.*

Rules: axe-core 4.10.2 with the WCAG 2.1 A and AA and WCAG 2.2 AA tags (wcag2a, wcag2aa, wcag21a, wcag21aa, wcag22aa), color contrast and target size (24 by 24 px, or spaced apart) among them. Keyboard: everything with a pointer cursor must be a Tab stop or inside one, and pressing Tab from the top must reach every Tab stop without looping (Escape then Tab leaves a code editor). Focus: each Tab stop on the page must show an outline or ring. Each page is checked in light and dark (the window's color scheme); the app has one theme, so dark is checked to show nothing breaks when Windows is set to dark. At 200% (the 1440x940 window laid out as 720x470) no page may scroll sideways as a whole, and each Tab stop must be on screen and not under something when it has focus. In a Windows contrast theme (forced colors) the theme picks the colors, so axe's contrast rule is off there; where the app keeps its own colors (forced-color-adjust: none, as on a selected tab or choice), the text must still reach 4.5:1 against what it sits on (3:1 for large text), measured with the theme's colors. Each Tab stop must still show a ring there too (the theme removes rings drawn as box shadows). The keyboard walk runs in light, at 200% and in the contrast theme. Every page is opened with reduced motion asked for, as Windows asks when Show animations is off: nothing may still be moving once it has drawn (a loop, or an animation longer than the stylesheet's cap), and no script may start an animation, unless the person chose motion anyway in Settings. A button or link that shows only an icon or a symbol must be named in words (aria-label, aria-labelledby or title). With text spacing raised as WCAG 1.4.12 asks (line height 1.5, paragraphs 2 em apart, letters 0.12 em and words 0.16 em apart), no box may cut off its words, unless it shortens them on purpose with an ellipsis or a line clamp. Dialogs opened from buttons are checked the same way in `a11y-dialogs.test.cjs`.

The pages are opened signed in with test data (two workspaces, one lakehouse, two notebooks), so lists show rows where they can. Screens that need a live tenant to fill are audited in their empty or signed-in state. Part 2 (Narrator and keyboard on Windows) is a separate pass.

| Page | Scheme | Rules passed | Violations (serious + critical) | Needs a look | Tab stops | Unreachable | No focus ring |
|---|---|---|---|---|---|---|---|
| Home | light | 16 | 0 (0) | color-contrast (5) | 21 | 0 | 0 |
| Home | dark | 16 | 0 (0) | color-contrast (5) | 0 | 0 | 0 |
| Home | zoom-200 | 16 | 0 (0) | color-contrast (3) | 21 | 0 | 0 |
| Home | high-contrast | 15 | 0 (0) | none | 21 | 0 | 0 |
| Create | light | 10 | 0 (0) | color-contrast (2) | 8 | 0 | 0 |
| Create | dark | 10 | 0 (0) | color-contrast (2) | 0 | 0 | 0 |
| Create | zoom-200 | 11 | 0 (0) | color-contrast (1) | 8 | 0 | 0 |
| Create | high-contrast | 9 | 0 (0) | none | 8 | 0 | 0 |
| Workspaces | light | 18 | 0 (0) | color-contrast (3) | 17 | 0 | 0 |
| Workspaces | dark | 18 | 0 (0) | color-contrast (3) | 0 | 0 | 0 |
| Workspaces | zoom-200 | 19 | 0 (0) | color-contrast (2) | 17 | 0 | 0 |
| Workspaces | high-contrast | 17 | 0 (0) | none | 17 | 0 | 0 |
| OneLake | light | 18 | 0 (0) | none | 8 | 0 | 0 |
| OneLake | dark | 18 | 0 (0) | none | 0 | 0 | 0 |
| OneLake | zoom-200 | 18 | 0 (0) | none | 8 | 0 | 0 |
| OneLake | high-contrast | 17 | 0 (0) | none | 8 | 0 | 0 |
| Data mesh | light | 15 | 0 (0) | color-contrast (1) | 9 | 0 | 0 |
| Data mesh | dark | 15 | 0 (0) | color-contrast (1) | 0 | 0 | 0 |
| Data mesh | zoom-200 | 15 | 0 (0) | none | 9 | 0 | 0 |
| Data mesh | high-contrast | 14 | 0 (0) | none | 9 | 0 | 0 |
| Platform | light | 15 | 0 (0) | color-contrast (1) | 8 | 0 | 0 |
| Platform | dark | 15 | 0 (0) | color-contrast (1) | 0 | 0 | 0 |
| Platform | zoom-200 | 15 | 0 (0) | none | 8 | 0 | 0 |
| Platform | high-contrast | 14 | 0 (0) | none | 8 | 0 | 0 |
| Optimize | light | 15 | 0 (0) | color-contrast (1) | 6 | 0 | 0 |
| Optimize | dark | 15 | 0 (0) | color-contrast (1) | 0 | 0 | 0 |
| Optimize | zoom-200 | 16 | 0 (0) | none | 6 | 0 | 0 |
| Optimize | high-contrast | 14 | 0 (0) | none | 6 | 0 | 0 |
| Monitor | light | 19 | 0 (0) | color-contrast (3) | 18 | 0 | 0 |
| Monitor | dark | 19 | 0 (0) | color-contrast (3) | 0 | 0 | 0 |
| Monitor | zoom-200 | 19 | 0 (0) | color-contrast (3) | 18 | 0 | 0 |
| Monitor | high-contrast | 18 | 0 (0) | none | 18 | 0 | 0 |
| Academy | light | 15 | 0 (0) | color-contrast (1) | 7 | 0 | 0 |
| Academy | dark | 15 | 0 (0) | color-contrast (1) | 0 | 0 | 0 |
| Academy | zoom-200 | 15 | 0 (0) | none | 7 | 0 | 0 |
| Academy | high-contrast | 14 | 0 (0) | none | 7 | 0 | 0 |
| Source control | light | 13 | 0 (0) | color-contrast (1) | 1 | 0 | 0 |
| Source control | dark | 13 | 0 (0) | color-contrast (1) | 0 | 0 | 0 |
| Source control | zoom-200 | 13 | 0 (0) | none | 1 | 0 | 0 |
| Source control | high-contrast | 12 | 0 (0) | none | 1 | 0 | 0 |
| Settings | light | 23 | 0 (0) | color-contrast (1) | 55 | 0 | 0 |
| Settings | dark | 23 | 0 (0) | color-contrast (1) | 0 | 0 | 0 |
| Settings | zoom-200 | 23 | 0 (0) | none | 55 | 0 | 0 |
| Settings | high-contrast | 22 | 0 (0) | none | 55 | 0 | 0 |
| Workspaces > Fabric portal | light | 15 | 0 (0) | none | 12 | 0 | 0 |
| Workspaces > Fabric portal | dark | 15 | 0 (0) | none | 0 | 0 | 0 |
| Workspaces > Fabric portal | zoom-200 | 15 | 0 (0) | none | 12 | 0 | 0 |
| Workspaces > Fabric portal | high-contrast | 14 | 0 (0) | none | 12 | 0 | 0 |
| Workspaces > Power BI | light | 20 | 0 (0) | color-contrast (2) | 18 | 0 | 0 |
| Workspaces > Power BI | dark | 20 | 0 (0) | color-contrast (2) | 0 | 0 | 0 |
| Workspaces > Power BI | zoom-200 | 21 | 0 (0) | color-contrast (2) | 18 | 0 | 0 |
| Workspaces > Power BI | high-contrast | 19 | 0 (0) | none | 18 | 0 | 0 |
| Workspaces > Databricks | light | 14 | 0 (0) | none | 7 | 0 | 0 |
| Workspaces > Databricks | dark | 14 | 0 (0) | none | 0 | 0 | 0 |
| Workspaces > Databricks | zoom-200 | 15 | 0 (0) | none | 7 | 0 | 0 |
| Workspaces > Databricks | high-contrast | 13 | 0 (0) | none | 7 | 0 | 0 |
| OneLake > Shortcuts | light | 17 | 0 (0) | none | 6 | 0 | 0 |
| OneLake > Shortcuts | dark | 17 | 0 (0) | none | 0 | 0 | 0 |
| OneLake > Shortcuts | zoom-200 | 17 | 0 (0) | none | 6 | 0 | 0 |
| OneLake > Shortcuts | high-contrast | 16 | 0 (0) | none | 6 | 0 | 0 |
| OneLake > Access | light | 16 | 0 (0) | none | 8 | 0 | 0 |
| OneLake > Access | dark | 16 | 0 (0) | none | 0 | 0 | 0 |
| OneLake > Access | zoom-200 | 16 | 0 (0) | none | 8 | 0 | 0 |
| OneLake > Access | high-contrast | 15 | 0 (0) | none | 8 | 0 | 0 |
| Data mesh > Products | light | 15 | 0 (0) | color-contrast (1) | 6 | 0 | 0 |
| Data mesh > Products | dark | 15 | 0 (0) | color-contrast (1) | 0 | 0 | 0 |
| Data mesh > Products | zoom-200 | 15 | 0 (0) | none | 6 | 0 | 0 |
| Data mesh > Products | high-contrast | 14 | 0 (0) | none | 6 | 0 | 0 |
| Data mesh > Requests | light | 15 | 0 (0) | color-contrast (1) | 6 | 0 | 0 |
| Data mesh > Requests | dark | 15 | 0 (0) | color-contrast (1) | 0 | 0 | 0 |
| Data mesh > Requests | zoom-200 | 15 | 0 (0) | none | 6 | 0 | 0 |
| Data mesh > Requests | high-contrast | 14 | 0 (0) | none | 6 | 0 | 0 |
| Data mesh > Domains | light | 15 | 0 (0) | color-contrast (1) | 7 | 0 | 0 |
| Data mesh > Domains | dark | 15 | 0 (0) | color-contrast (1) | 0 | 0 | 0 |
| Data mesh > Domains | zoom-200 | 15 | 0 (0) | none | 7 | 0 | 0 |
| Data mesh > Domains | high-contrast | 14 | 0 (0) | none | 7 | 0 | 0 |
| Platform > Set up | light | 15 | 0 (0) | color-contrast (1) | 11 | 0 | 0 |
| Platform > Set up | dark | 15 | 0 (0) | color-contrast (1) | 0 | 0 | 0 |
| Platform > Set up | zoom-200 | 15 | 0 (0) | none | 11 | 0 | 0 |
| Platform > Set up | high-contrast | 14 | 0 (0) | none | 11 | 0 | 0 |
| Platform > Govern | light | 15 | 0 (0) | color-contrast (1) | 7 | 0 | 0 |
| Platform > Govern | dark | 15 | 0 (0) | color-contrast (1) | 0 | 0 | 0 |
| Platform > Govern | zoom-200 | 15 | 0 (0) | none | 7 | 0 | 0 |
| Platform > Govern | high-contrast | 14 | 0 (0) | none | 7 | 0 | 0 |
| Platform > Settings | light | 15 | 0 (0) | color-contrast (1) | 5 | 0 | 0 |
| Platform > Settings | dark | 15 | 0 (0) | color-contrast (1) | 0 | 0 | 0 |
| Platform > Settings | zoom-200 | 15 | 0 (0) | none | 5 | 0 | 0 |
| Platform > Settings | high-contrast | 14 | 0 (0) | none | 5 | 0 | 0 |
| Optimize > Opportunities | light | 15 | 0 (0) | color-contrast (1) | 6 | 0 | 0 |
| Optimize > Opportunities | dark | 15 | 0 (0) | color-contrast (1) | 0 | 0 | 0 |
| Optimize > Opportunities | zoom-200 | 15 | 0 (0) | none | 6 | 0 | 0 |
| Optimize > Opportunities | high-contrast | 14 | 0 (0) | none | 6 | 0 | 0 |
| Optimize > Assessment | light | 20 | 0 (0) | color-contrast (1) | 14 | 0 | 0 |
| Optimize > Assessment | dark | 20 | 0 (0) | color-contrast (1) | 0 | 0 | 0 |
| Optimize > Assessment | zoom-200 | 20 | 0 (0) | none | 14 | 0 | 0 |
| Optimize > Assessment | high-contrast | 18 | 0 (0) | none | 14 | 0 | 0 |
| Optimize > Cost and savings | light | 15 | 0 (0) | color-contrast (1) | 6 | 0 | 0 |
| Optimize > Cost and savings | dark | 15 | 0 (0) | color-contrast (1) | 0 | 0 | 0 |
| Optimize > Cost and savings | zoom-200 | 16 | 0 (0) | none | 6 | 0 | 0 |
| Optimize > Cost and savings | high-contrast | 14 | 0 (0) | none | 6 | 0 | 0 |
| Optimize > Settings history | light | 15 | 0 (0) | color-contrast (1) | 6 | 0 | 0 |
| Optimize > Settings history | dark | 15 | 0 (0) | color-contrast (1) | 0 | 0 | 0 |
| Optimize > Settings history | zoom-200 | 15 | 0 (0) | none | 6 | 0 | 0 |
| Optimize > Settings history | high-contrast | 14 | 0 (0) | none | 6 | 0 | 0 |
| Monitor > Fabric | light | 17 | 0 (0) | none | 11 | 0 | 0 |
| Monitor > Fabric | dark | 17 | 0 (0) | none | 0 | 0 | 0 |
| Monitor > Fabric | zoom-200 | 17 | 0 (0) | none | 11 | 0 | 0 |
| Monitor > Fabric | high-contrast | 16 | 0 (0) | none | 11 | 0 | 0 |
| Monitor > Databricks | light | 17 | 0 (0) | color-contrast (1) | 6 | 0 | 0 |
| Monitor > Databricks | dark | 17 | 0 (0) | color-contrast (1) | 0 | 0 | 0 |
| Monitor > Databricks | zoom-200 | 17 | 0 (0) | none | 6 | 0 | 0 |
| Monitor > Databricks | high-contrast | 16 | 0 (0) | none | 6 | 0 | 0 |
| Academy > Path | light | 15 | 0 (0) | color-contrast (1) | 7 | 0 | 0 |
| Academy > Path | dark | 15 | 0 (0) | color-contrast (1) | 0 | 0 | 0 |
| Academy > Path | zoom-200 | 15 | 0 (0) | none | 7 | 0 | 0 |
| Academy > Path | high-contrast | 14 | 0 (0) | none | 7 | 0 | 0 |
| Academy > Practice | light | 15 | 0 (0) | color-contrast (1) | 7 | 0 | 0 |
| Academy > Practice | dark | 15 | 0 (0) | color-contrast (1) | 0 | 0 | 0 |
| Academy > Practice | zoom-200 | 15 | 0 (0) | none | 7 | 0 | 0 |
| Academy > Practice | high-contrast | 14 | 0 (0) | none | 7 | 0 | 0 |
| Academy > Assessments | light | 15 | 0 (0) | color-contrast (1) | 6 | 0 | 0 |
| Academy > Assessments | dark | 15 | 0 (0) | color-contrast (1) | 0 | 0 | 0 |
| Academy > Assessments | zoom-200 | 15 | 0 (0) | none | 6 | 0 | 0 |
| Academy > Assessments | high-contrast | 14 | 0 (0) | none | 6 | 0 | 0 |
| Academy > Readiness | light | 15 | 0 (0) | color-contrast (1) | 7 | 0 | 0 |
| Academy > Readiness | dark | 15 | 0 (0) | color-contrast (1) | 0 | 0 | 0 |
| Academy > Readiness | zoom-200 | 15 | 0 (0) | none | 7 | 0 | 0 |
| Academy > Readiness | high-contrast | 14 | 0 (0) | none | 7 | 0 | 0 |
| Academy > Portfolio | light | 15 | 0 (0) | color-contrast (1) | 7 | 0 | 0 |
| Academy > Portfolio | dark | 15 | 0 (0) | color-contrast (1) | 0 | 0 | 0 |
| Academy > Portfolio | zoom-200 | 15 | 0 (0) | none | 7 | 0 | 0 |
| Academy > Portfolio | high-contrast | 14 | 0 (0) | none | 7 | 0 | 0 |
| Settings > Connections | light | 15 | 0 (0) | color-contrast (1) | 5 | 0 | 0 |
| Settings > Connections | dark | 15 | 0 (0) | color-contrast (1) | 0 | 0 | 0 |
| Settings > Connections | zoom-200 | 15 | 0 (0) | none | 5 | 0 | 0 |
| Settings > Connections | high-contrast | 14 | 0 (0) | none | 5 | 0 | 0 |
| Settings > Environment | light | 15 | 0 (0) | color-contrast (1) | 5 | 0 | 0 |
| Settings > Environment | dark | 15 | 0 (0) | color-contrast (1) | 0 | 0 | 0 |
| Settings > Environment | zoom-200 | 15 | 0 (0) | none | 5 | 0 | 0 |
| Settings > Environment | high-contrast | 14 | 0 (0) | none | 5 | 0 | 0 |
| Settings > Integrations | light | 22 | 0 (0) | color-contrast (1) | 8 | 0 | 0 |
| Settings > Integrations | dark | 22 | 0 (0) | color-contrast (1) | 0 | 0 | 0 |
| Settings > Integrations | zoom-200 | 22 | 0 (0) | none | 8 | 0 | 0 |
| Settings > Integrations | high-contrast | 21 | 0 (0) | none | 8 | 0 | 0 |
| Notebook | light | 19 | 0 (0) | color-contrast (2) | 38 | 0 | 0 |
| Notebook | dark | 19 | 0 (0) | color-contrast (2) | 0 | 0 | 0 |
| Notebook | zoom-200 | 20 | 0 (0) | color-contrast (4) | 38 | 0 | 0 |
| Notebook | high-contrast | 18 | 0 (0) | none | 38 | 0 | 0 |
| SQL query | light | 23 | 0 (0) | none | 31 | 0 | 0 |
| SQL query | dark | 23 | 0 (0) | none | 0 | 0 | 0 |
| SQL query | zoom-200 | 23 | 0 (0) | none | 26 | 0 | 0 |
| SQL query | high-contrast | 22 | 0 (0) | none | 31 | 0 | 0 |
| Command bar (Ctrl+K) | light | 18 | 0 (0) | none | 110 | 0 | 0 |
| Command bar (Ctrl+K) | dark | 18 | 0 (0) | none | 0 | 0 | 0 |
| Command bar (Ctrl+K) | zoom-200 | 18 | 0 (0) | none | 110 | 0 | 0 |
| Command bar (Ctrl+K) | high-contrast | 17 | 0 (0) | none | 110 | 0 | 0 |
| Data mesh entity | light | 16 | 0 (0) | color-contrast (5) | 15 | 0 | 0 |
| Data mesh entity | dark | 16 | 0 (0) | color-contrast (5) | 0 | 0 | 0 |
| Data mesh entity | zoom-200 | 16 | 0 (0) | color-contrast (4) | 15 | 0 | 0 |
| Data mesh entity | high-contrast | 15 | 0 (0) | none | 15 | 0 | 0 |
| Shell (top bar and rail) | light | 14 | 0 (0) | none | 19 | 0 | 0 |
| Shell (top bar and rail) | dark | 14 | 0 (0) | none | 0 | 0 | 0 |
| Shell (top bar and rail) | zoom-200 | 14 | 0 (0) | none | 19 | 0 | 0 |
| Shell (top bar and rail) | high-contrast | 13 | 0 (0) | none | 19 | 0 | 0 |
| Confirm dialog | light | 14 | 0 (0) | none | 3 | 0 | 0 |
| Confirm dialog | dark | 14 | 0 (0) | none | 0 | 0 | 0 |
| Confirm dialog | zoom-200 | 14 | 0 (0) | none | 3 | 0 | 0 |
| Confirm dialog | high-contrast | 13 | 0 (0) | none | 3 | 0 | 0 |
| Profile | light | 20 | 0 (0) | color-contrast (2) | 13 | 0 | 0 |
| Profile | dark | 20 | 0 (0) | color-contrast (2) | 0 | 0 | 0 |
| Profile | zoom-200 | 20 | 0 (0) | color-contrast (1) | 13 | 0 | 0 |
| Profile | high-contrast | 19 | 0 (0) | none | 13 | 0 | 0 |
| Learn lab | light | 17 | 0 (0) | color-contrast (1) | 8 | 0 | 0 |
| Learn lab | dark | 17 | 0 (0) | color-contrast (1) | 0 | 0 | 0 |
| Learn lab | zoom-200 | 17 | 0 (0) | none | 8 | 0 | 0 |
| Learn lab | high-contrast | 16 | 0 (0) | none | 8 | 0 | 0 |
| Settings > Integrations | light | 20 | 0 (0) | color-contrast (1) | 14 | 0 | 0 |
| Settings > Integrations | dark | 20 | 0 (0) | color-contrast (1) | 0 | 0 | 0 |
| Settings > Integrations | zoom-200 | 20 | 0 (0) | none | 14 | 0 | 0 |
| Settings > Integrations | high-contrast | 19 | 0 (0) | none | 14 | 0 | 0 |
| AI tool approval dialog | light | 16 | 0 (0) | none | 3 | 0 | 0 |
| AI tool approval dialog | dark | 16 | 0 (0) | none | 0 | 0 | 0 |
| AI tool approval dialog | zoom-200 | 16 | 0 (0) | none | 3 | 0 | 0 |
| AI tool approval dialog | high-contrast | 15 | 0 (0) | none | 3 | 0 | 0 |
| Report a problem dialog | light | 15 | 0 (0) | color-contrast (2) | 6 | 0 | 0 |
| Report a problem dialog | dark | 15 | 0 (0) | color-contrast (2) | 0 | 0 | 0 |
| Report a problem dialog | zoom-200 | 15 | 0 (0) | color-contrast (2) | 6 | 0 | 0 |
| Report a problem dialog | high-contrast | 14 | 0 (0) | none | 6 | 0 | 0 |
| Keyboard shortcuts dialog | light | 18 | 0 (0) | color-contrast (1) | 4 | 0 | 0 |
| Keyboard shortcuts dialog | dark | 18 | 0 (0) | color-contrast (1) | 0 | 0 | 0 |
| Keyboard shortcuts dialog | zoom-200 | 17 | 0 (0) | none | 4 | 0 | 0 |
| Keyboard shortcuts dialog | high-contrast | 17 | 0 (0) | none | 4 | 0 | 0 |
| Engine picker's How to choose dialog | light | 18 | 0 (0) | color-contrast (4) | 5 | 0 | 0 |
| Engine picker's How to choose dialog | dark | 18 | 0 (0) | color-contrast (4) | 0 | 0 | 0 |
| Engine picker's How to choose dialog | zoom-200 | 18 | 0 (0) | color-contrast (2) | 5 | 0 | 0 |
| Engine picker's How to choose dialog | high-contrast | 17 | 0 (0) | none | 5 | 0 | 0 |
| A workspace | light | 8 | 0 (0) | none | 4 | 0 | 0 |
| A workspace | dark | 8 | 0 (0) | none | 0 | 0 | 0 |
| A workspace | zoom-200 | 8 | 0 (0) | none | 4 | 0 | 0 |
| A workspace | high-contrast | 7 | 0 (0) | none | 4 | 0 | 0 |
| A lakehouse | light | 8 | 0 (0) | none | 5 | 0 | 0 |
| A lakehouse | dark | 8 | 0 (0) | none | 0 | 0 | 0 |
| A lakehouse | zoom-200 | 9 | 0 (0) | none | 5 | 0 | 0 |
| A lakehouse | high-contrast | 7 | 0 (0) | none | 5 | 0 | 0 |
| Workspaces > Databricks | light | 14 | 0 (0) | none | 7 | 0 | 0 |
| Workspaces > Databricks | dark | 14 | 0 (0) | none | 0 | 0 | 0 |
| Workspaces > Databricks | zoom-200 | 15 | 0 (0) | none | 7 | 0 | 0 |
| Workspaces > Databricks | high-contrast | 13 | 0 (0) | none | 7 | 0 | 0 |
| Workspaces > Databricks > Volumes on this PC | light | 25 | 0 (0) | color-contrast (1) | 17 | 0 | 0 |
| Workspaces > Databricks > Volumes on this PC | dark | 25 | 0 (0) | color-contrast (1) | 0 | 0 | 0 |
| Workspaces > Databricks > Volumes on this PC | zoom-200 | 26 | 0 (0) | color-contrast (1) | 17 | 0 | 0 |
| Workspaces > Databricks > Volumes on this PC | high-contrast | 24 | 0 (0) | none | 17 | 0 | 0 |
| Workspaces > Power BI | light | 20 | 0 (0) | color-contrast (2) | 18 | 0 | 0 |
| Workspaces > Power BI | dark | 20 | 0 (0) | color-contrast (2) | 0 | 0 | 0 |
| Workspaces > Power BI | zoom-200 | 21 | 0 (0) | color-contrast (2) | 18 | 0 | 0 |
| Workspaces > Power BI | high-contrast | 19 | 0 (0) | none | 18 | 0 | 0 |
| Embed Studio | light | 17 | 0 (0) | none | 8 | 0 | 0 |
| Embed Studio | dark | 17 | 0 (0) | none | 0 | 0 | 0 |
| Embed Studio | zoom-200 | 18 | 0 (0) | none | 8 | 0 | 0 |
| Embed Studio | high-contrast | 16 | 0 (0) | none | 8 | 0 | 0 |
| Fabric portal (the app's bar) | light | 15 | 0 (0) | none | 12 | 0 | 0 |
| Fabric portal (the app's bar) | dark | 15 | 0 (0) | none | 0 | 0 | 0 |
| Fabric portal (the app's bar) | zoom-200 | 15 | 0 (0) | none | 12 | 0 | 0 |
| Fabric portal (the app's bar) | high-contrast | 14 | 0 (0) | none | 12 | 0 | 0 |
| All notebooks | light | 19 | 0 (0) | color-contrast (2) | 7 | 0 | 0 |
| All notebooks | dark | 19 | 0 (0) | color-contrast (2) | 0 | 0 | 0 |
| All notebooks | zoom-200 | 20 | 0 (0) | color-contrast (2) | 7 | 0 | 0 |
| All notebooks | high-contrast | 18 | 0 (0) | none | 7 | 0 | 0 |
| A pipeline | light | 17 | 0 (0) | none | 7 | 0 | 0 |
| A pipeline | dark | 17 | 0 (0) | none | 0 | 0 | 0 |
| A pipeline | zoom-200 | 17 | 0 (0) | none | 7 | 0 | 0 |
| A pipeline | high-contrast | 16 | 0 (0) | none | 7 | 0 | 0 |
| A pipeline run | light | 21 | 0 (0) | none | 20 | 0 | 0 |
| A pipeline run | dark | 21 | 0 (0) | none | 0 | 0 | 0 |
| A pipeline run | zoom-200 | 20 | 0 (0) | none | 20 | 0 | 0 |
| A pipeline run | high-contrast | 20 | 0 (0) | none | 20 | 0 | 0 |
| A workload | light | 10 | 0 (0) | none | 5 | 0 | 0 |
| A workload | dark | 10 | 0 (0) | none | 0 | 0 | 0 |
| A workload | zoom-200 | 10 | 0 (0) | none | 5 | 0 | 0 |
| A workload | high-contrast | 9 | 0 (0) | none | 5 | 0 | 0 |
| An incident | light | 16 | 0 (0) | none | 5 | 0 | 0 |
| An incident | dark | 16 | 0 (0) | none | 0 | 0 | 0 |
| An incident | zoom-200 | 17 | 0 (0) | none | 5 | 0 | 0 |
| An incident | high-contrast | 15 | 0 (0) | none | 5 | 0 | 0 |
| An agent | light | 11 | 0 (0) | none | 1 | 0 | 0 |
| An agent | dark | 11 | 0 (0) | none | 0 | 0 | 0 |
| An agent | zoom-200 | 12 | 0 (0) | none | 1 | 0 | 0 |
| An agent | high-contrast | 10 | 0 (0) | none | 1 | 0 | 0 |
| App Studio | light | 18 | 0 (0) | none | 9 | 0 | 0 |
| App Studio | dark | 18 | 0 (0) | none | 0 | 0 | 0 |
| App Studio | zoom-200 | 19 | 0 (0) | none | 9 | 0 | 0 |
| App Studio | high-contrast | 17 | 0 (0) | none | 9 | 0 | 0 |
| Learn | light | 20 | 0 (0) | none | 22 | 0 | 0 |
| Learn | dark | 20 | 0 (0) | none | 0 | 0 | 0 |
| Learn | zoom-200 | 20 | 0 (0) | none | 22 | 0 | 0 |
| Learn | high-contrast | 19 | 0 (0) | none | 22 | 0 | 0 |

## Findings

Filed to be fixed in [#905](https://github.com/KratosDataGod/KratosForge/issues/905); the test fails on any serious or critical rule violation, or keyboard or focus finding, not listed here.

None.

## Needs a look

Checks axe couldn't decide by itself (for example a glyph, a line number, or text over a gradient or an image), each with a person's verdict and the measured contrast.

- **Home**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `.home-hero-kicker`, `.home-hero-title`, `h1`, `.hero-outline`, `.get-started > .fold-head > .fold-toggle[title="Hide"] > .chev`. Pass: the hero's text and buttons sit on the navy sky: the gradient #01203d to #062e57, and over the picture's empty left quarter, faded into that same sky (its sky runs #10223f to #1b3664; the text always ends before the cape, at 27% of the picture). Worst case, on #1b3664: the title #ffffff 11.9:1, the welcome #e6ecf5 10.1:1, the kicker #f8991c 5.4:1. On the plain gradient (axe names the welcome h1 and the outline button by their short selectors when they sit over the picture): the title #ffffff 13.7:1 at worst, the welcome #e6ecf5 11.5:1, the kicker #f8991c 6.2:1, the outline button's label 13.7:1, and Continue is #0b1420 on its own #f8991c fill, 8.4:1 (axe can't read a picture). Pass: the fold arrow is #616161 on white, 6.2:1 (3:1 needed for a control's graphic), and hidden from assistive tech (its button is named).
- **Create**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `h1`, `.hero-outline`. Pass: a page's header (pageHero, art/experience/heroes.js), the same on each of its tabs, puts its h1 and buttons on the navy sky: the gradient #01203d to #062e57, and over each picture's empty left part, faded into that sky; the text always ends before the picture's sky line, which the page-heroes test checks for every picture at 12 widths. Measured in the browser under every glyph and button of all ten pages at 1920, 1440, 1251, 1101 and 961 px with the text hidden, the lightest pixel is #00396e: the title #ffffff is 11.6:1 at worst and the outline buttons' #ffffff labels 13.9:1. New notebook is #0b1420 on its own #f8991c fill, 8.4:1, and so is the Preview tag on its own #f8991c fill (axe can't read a picture).
- **Workspaces**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `h1`, `.click:nth-child(1) > td:nth-child(3) > .star[title="Add to favorites"][aria-label="Add to favorites"]`, `.click:nth-child(2) > td:nth-child(3) > .star[title="Add to favorites"][aria-label="Add to favorites"]`. Pass: a page's header (pageHero, art/experience/heroes.js), the same on each of its tabs, puts its h1 and buttons on the navy sky: the gradient #01203d to #062e57, and over each picture's empty left part, faded into that sky; the text always ends before the picture's sky line, which the page-heroes test checks for every picture at 12 widths. Measured in the browser under every glyph and button of all ten pages at 1920, 1440, 1251, 1101 and 961 px with the text hidden, the lightest pixel is #00396e: the title #ffffff is 11.6:1 at worst and the outline buttons' #ffffff labels 13.9:1. New notebook is #0b1420 on its own #f8991c fill, 8.4:1, and so is the Preview tag on its own #f8991c fill (axe can't read a picture). Pass: the favorite star is #8a8a8a, 3.4:1 on white and 3.3:1 on #fafafa (3:1 needed for a control's graphic); its button is named.
- **Data mesh**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `h1`. Pass: a page's header (pageHero, art/experience/heroes.js), the same on each of its tabs, puts its h1 and buttons on the navy sky: the gradient #01203d to #062e57, and over each picture's empty left part, faded into that sky; the text always ends before the picture's sky line, which the page-heroes test checks for every picture at 12 widths. Measured in the browser under every glyph and button of all ten pages at 1920, 1440, 1251, 1101 and 961 px with the text hidden, the lightest pixel is #00396e: the title #ffffff is 11.6:1 at worst and the outline buttons' #ffffff labels 13.9:1. New notebook is #0b1420 on its own #f8991c fill, 8.4:1, and so is the Preview tag on its own #f8991c fill (axe can't read a picture).
- **Platform**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `h1`. Pass: a page's header (pageHero, art/experience/heroes.js), the same on each of its tabs, puts its h1 and buttons on the navy sky: the gradient #01203d to #062e57, and over each picture's empty left part, faded into that sky; the text always ends before the picture's sky line, which the page-heroes test checks for every picture at 12 widths. Measured in the browser under every glyph and button of all ten pages at 1920, 1440, 1251, 1101 and 961 px with the text hidden, the lightest pixel is #00396e: the title #ffffff is 11.6:1 at worst and the outline buttons' #ffffff labels 13.9:1. New notebook is #0b1420 on its own #f8991c fill, 8.4:1, and so is the Preview tag on its own #f8991c fill (axe can't read a picture).
- **Optimize**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `h1`. Pass: a page's header (pageHero, art/experience/heroes.js), the same on each of its tabs, puts its h1 and buttons on the navy sky: the gradient #01203d to #062e57, and over each picture's empty left part, faded into that sky; the text always ends before the picture's sky line, which the page-heroes test checks for every picture at 12 widths. Measured in the browser under every glyph and button of all ten pages at 1920, 1440, 1251, 1101 and 961 px with the text hidden, the lightest pixel is #00396e: the title #ffffff is 11.6:1 at worst and the outline buttons' #ffffff labels 13.9:1. New notebook is #0b1420 on its own #f8991c fill, 8.4:1, and so is the Preview tag on its own #f8991c fill (axe can't read a picture).
- **Monitor**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `.forge-title`, `.forge-title > span`, `.forge-sub`. Pass: on the Forge band's navy gradient (#000f22 to #0a2a4b, lightest taken), the title is #ffffff, 14.5:1, its state word at least 6.6:1 (#f8991c active, #7fd99b done 8.5:1, #ff9e9e failed 7.4:1, #c9d6ea quiet 9.9:1), the line under it #e6ecf5, 12.2:1, and the link to the table #ffcf8a, 10.1:1 (axe can't measure text on a gradient).
- **Academy**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `h1`. Pass: a page's header (pageHero, art/experience/heroes.js), the same on each of its tabs, puts its h1 and buttons on the navy sky: the gradient #01203d to #062e57, and over each picture's empty left part, faded into that sky; the text always ends before the picture's sky line, which the page-heroes test checks for every picture at 12 widths. Measured in the browser under every glyph and button of all ten pages at 1920, 1440, 1251, 1101 and 961 px with the text hidden, the lightest pixel is #00396e: the title #ffffff is 11.6:1 at worst and the outline buttons' #ffffff labels 13.9:1. New notebook is #0b1420 on its own #f8991c fill, 8.4:1, and so is the Preview tag on its own #f8991c fill (axe can't read a picture).
- **Source control**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `h1`. Pass: a page's header (pageHero, art/experience/heroes.js), the same on each of its tabs, puts its h1 and buttons on the navy sky: the gradient #01203d to #062e57, and over each picture's empty left part, faded into that sky; the text always ends before the picture's sky line, which the page-heroes test checks for every picture at 12 widths. Measured in the browser under every glyph and button of all ten pages at 1920, 1440, 1251, 1101 and 961 px with the text hidden, the lightest pixel is #00396e: the title #ffffff is 11.6:1 at worst and the outline buttons' #ffffff labels 13.9:1. New notebook is #0b1420 on its own #f8991c fill, 8.4:1, and so is the Preview tag on its own #f8991c fill (axe can't read a picture).
- **Settings**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `h1`. Pass: a page's header (pageHero, art/experience/heroes.js), the same on each of its tabs, puts its h1 and buttons on the navy sky: the gradient #01203d to #062e57, and over each picture's empty left part, faded into that sky; the text always ends before the picture's sky line, which the page-heroes test checks for every picture at 12 widths. Measured in the browser under every glyph and button of all ten pages at 1920, 1440, 1251, 1101 and 961 px with the text hidden, the lightest pixel is #00396e: the title #ffffff is 11.6:1 at worst and the outline buttons' #ffffff labels 13.9:1. New notebook is #0b1420 on its own #f8991c fill, 8.4:1, and so is the Preview tag on its own #f8991c fill (axe can't read a picture).
- **Workspaces > Power BI**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `tr:nth-child(1) > td:nth-child(1) > .star[aria-label="Favorite Sales performance"][title="Add to favorites"]`, `tr:nth-child(2) > td:nth-child(1) > .star[aria-label="Favorite Sales performance"][title="Add to favorites"]`. Pass: the favorite star is #8a8a8a, 3.4:1 on white and 3.3:1 on #fafafa (3:1 needed for a control's graphic); its button is named.
- **Data mesh > Products**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `h1`. Pass: a page's header (pageHero, art/experience/heroes.js), the same on each of its tabs, puts its h1 and buttons on the navy sky: the gradient #01203d to #062e57, and over each picture's empty left part, faded into that sky; the text always ends before the picture's sky line, which the page-heroes test checks for every picture at 12 widths. Measured in the browser under every glyph and button of all ten pages at 1920, 1440, 1251, 1101 and 961 px with the text hidden, the lightest pixel is #00396e: the title #ffffff is 11.6:1 at worst and the outline buttons' #ffffff labels 13.9:1. New notebook is #0b1420 on its own #f8991c fill, 8.4:1, and so is the Preview tag on its own #f8991c fill (axe can't read a picture).
- **Data mesh > Requests**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `h1`. Pass: a page's header (pageHero, art/experience/heroes.js), the same on each of its tabs, puts its h1 and buttons on the navy sky: the gradient #01203d to #062e57, and over each picture's empty left part, faded into that sky; the text always ends before the picture's sky line, which the page-heroes test checks for every picture at 12 widths. Measured in the browser under every glyph and button of all ten pages at 1920, 1440, 1251, 1101 and 961 px with the text hidden, the lightest pixel is #00396e: the title #ffffff is 11.6:1 at worst and the outline buttons' #ffffff labels 13.9:1. New notebook is #0b1420 on its own #f8991c fill, 8.4:1, and so is the Preview tag on its own #f8991c fill (axe can't read a picture).
- **Data mesh > Domains**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `h1`. Pass: a page's header (pageHero, art/experience/heroes.js), the same on each of its tabs, puts its h1 and buttons on the navy sky: the gradient #01203d to #062e57, and over each picture's empty left part, faded into that sky; the text always ends before the picture's sky line, which the page-heroes test checks for every picture at 12 widths. Measured in the browser under every glyph and button of all ten pages at 1920, 1440, 1251, 1101 and 961 px with the text hidden, the lightest pixel is #00396e: the title #ffffff is 11.6:1 at worst and the outline buttons' #ffffff labels 13.9:1. New notebook is #0b1420 on its own #f8991c fill, 8.4:1, and so is the Preview tag on its own #f8991c fill (axe can't read a picture).
- **Platform > Set up**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `h1`. Pass: a page's header (pageHero, art/experience/heroes.js), the same on each of its tabs, puts its h1 and buttons on the navy sky: the gradient #01203d to #062e57, and over each picture's empty left part, faded into that sky; the text always ends before the picture's sky line, which the page-heroes test checks for every picture at 12 widths. Measured in the browser under every glyph and button of all ten pages at 1920, 1440, 1251, 1101 and 961 px with the text hidden, the lightest pixel is #00396e: the title #ffffff is 11.6:1 at worst and the outline buttons' #ffffff labels 13.9:1. New notebook is #0b1420 on its own #f8991c fill, 8.4:1, and so is the Preview tag on its own #f8991c fill (axe can't read a picture).
- **Platform > Govern**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `h1`. Pass: a page's header (pageHero, art/experience/heroes.js), the same on each of its tabs, puts its h1 and buttons on the navy sky: the gradient #01203d to #062e57, and over each picture's empty left part, faded into that sky; the text always ends before the picture's sky line, which the page-heroes test checks for every picture at 12 widths. Measured in the browser under every glyph and button of all ten pages at 1920, 1440, 1251, 1101 and 961 px with the text hidden, the lightest pixel is #00396e: the title #ffffff is 11.6:1 at worst and the outline buttons' #ffffff labels 13.9:1. New notebook is #0b1420 on its own #f8991c fill, 8.4:1, and so is the Preview tag on its own #f8991c fill (axe can't read a picture).
- **Platform > Settings**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `h1`. Pass: a page's header (pageHero, art/experience/heroes.js), the same on each of its tabs, puts its h1 and buttons on the navy sky: the gradient #01203d to #062e57, and over each picture's empty left part, faded into that sky; the text always ends before the picture's sky line, which the page-heroes test checks for every picture at 12 widths. Measured in the browser under every glyph and button of all ten pages at 1920, 1440, 1251, 1101 and 961 px with the text hidden, the lightest pixel is #00396e: the title #ffffff is 11.6:1 at worst and the outline buttons' #ffffff labels 13.9:1. New notebook is #0b1420 on its own #f8991c fill, 8.4:1, and so is the Preview tag on its own #f8991c fill (axe can't read a picture).
- **Optimize > Opportunities**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `h1`. Pass: a page's header (pageHero, art/experience/heroes.js), the same on each of its tabs, puts its h1 and buttons on the navy sky: the gradient #01203d to #062e57, and over each picture's empty left part, faded into that sky; the text always ends before the picture's sky line, which the page-heroes test checks for every picture at 12 widths. Measured in the browser under every glyph and button of all ten pages at 1920, 1440, 1251, 1101 and 961 px with the text hidden, the lightest pixel is #00396e: the title #ffffff is 11.6:1 at worst and the outline buttons' #ffffff labels 13.9:1. New notebook is #0b1420 on its own #f8991c fill, 8.4:1, and so is the Preview tag on its own #f8991c fill (axe can't read a picture).
- **Optimize > Assessment**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `h1`. Pass: a page's header (pageHero, art/experience/heroes.js), the same on each of its tabs, puts its h1 and buttons on the navy sky: the gradient #01203d to #062e57, and over each picture's empty left part, faded into that sky; the text always ends before the picture's sky line, which the page-heroes test checks for every picture at 12 widths. Measured in the browser under every glyph and button of all ten pages at 1920, 1440, 1251, 1101 and 961 px with the text hidden, the lightest pixel is #00396e: the title #ffffff is 11.6:1 at worst and the outline buttons' #ffffff labels 13.9:1. New notebook is #0b1420 on its own #f8991c fill, 8.4:1, and so is the Preview tag on its own #f8991c fill (axe can't read a picture).
- **Optimize > Cost and savings**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `h1`. Pass: a page's header (pageHero, art/experience/heroes.js), the same on each of its tabs, puts its h1 and buttons on the navy sky: the gradient #01203d to #062e57, and over each picture's empty left part, faded into that sky; the text always ends before the picture's sky line, which the page-heroes test checks for every picture at 12 widths. Measured in the browser under every glyph and button of all ten pages at 1920, 1440, 1251, 1101 and 961 px with the text hidden, the lightest pixel is #00396e: the title #ffffff is 11.6:1 at worst and the outline buttons' #ffffff labels 13.9:1. New notebook is #0b1420 on its own #f8991c fill, 8.4:1, and so is the Preview tag on its own #f8991c fill (axe can't read a picture).
- **Optimize > Settings history**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `h1`. Pass: a page's header (pageHero, art/experience/heroes.js), the same on each of its tabs, puts its h1 and buttons on the navy sky: the gradient #01203d to #062e57, and over each picture's empty left part, faded into that sky; the text always ends before the picture's sky line, which the page-heroes test checks for every picture at 12 widths. Measured in the browser under every glyph and button of all ten pages at 1920, 1440, 1251, 1101 and 961 px with the text hidden, the lightest pixel is #00396e: the title #ffffff is 11.6:1 at worst and the outline buttons' #ffffff labels 13.9:1. New notebook is #0b1420 on its own #f8991c fill, 8.4:1, and so is the Preview tag on its own #f8991c fill (axe can't read a picture).
- **Monitor > Databricks**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `h1`. Pass: a page's header (pageHero, art/experience/heroes.js), the same on each of its tabs, puts its h1 and buttons on the navy sky: the gradient #01203d to #062e57, and over each picture's empty left part, faded into that sky; the text always ends before the picture's sky line, which the page-heroes test checks for every picture at 12 widths. Measured in the browser under every glyph and button of all ten pages at 1920, 1440, 1251, 1101 and 961 px with the text hidden, the lightest pixel is #00396e: the title #ffffff is 11.6:1 at worst and the outline buttons' #ffffff labels 13.9:1. New notebook is #0b1420 on its own #f8991c fill, 8.4:1, and so is the Preview tag on its own #f8991c fill (axe can't read a picture).
- **Academy > Path**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `h1`. Pass: a page's header (pageHero, art/experience/heroes.js), the same on each of its tabs, puts its h1 and buttons on the navy sky: the gradient #01203d to #062e57, and over each picture's empty left part, faded into that sky; the text always ends before the picture's sky line, which the page-heroes test checks for every picture at 12 widths. Measured in the browser under every glyph and button of all ten pages at 1920, 1440, 1251, 1101 and 961 px with the text hidden, the lightest pixel is #00396e: the title #ffffff is 11.6:1 at worst and the outline buttons' #ffffff labels 13.9:1. New notebook is #0b1420 on its own #f8991c fill, 8.4:1, and so is the Preview tag on its own #f8991c fill (axe can't read a picture).
- **Academy > Practice**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `h1`. Pass: a page's header (pageHero, art/experience/heroes.js), the same on each of its tabs, puts its h1 and buttons on the navy sky: the gradient #01203d to #062e57, and over each picture's empty left part, faded into that sky; the text always ends before the picture's sky line, which the page-heroes test checks for every picture at 12 widths. Measured in the browser under every glyph and button of all ten pages at 1920, 1440, 1251, 1101 and 961 px with the text hidden, the lightest pixel is #00396e: the title #ffffff is 11.6:1 at worst and the outline buttons' #ffffff labels 13.9:1. New notebook is #0b1420 on its own #f8991c fill, 8.4:1, and so is the Preview tag on its own #f8991c fill (axe can't read a picture).
- **Academy > Assessments**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `h1`. Pass: a page's header (pageHero, art/experience/heroes.js), the same on each of its tabs, puts its h1 and buttons on the navy sky: the gradient #01203d to #062e57, and over each picture's empty left part, faded into that sky; the text always ends before the picture's sky line, which the page-heroes test checks for every picture at 12 widths. Measured in the browser under every glyph and button of all ten pages at 1920, 1440, 1251, 1101 and 961 px with the text hidden, the lightest pixel is #00396e: the title #ffffff is 11.6:1 at worst and the outline buttons' #ffffff labels 13.9:1. New notebook is #0b1420 on its own #f8991c fill, 8.4:1, and so is the Preview tag on its own #f8991c fill (axe can't read a picture).
- **Academy > Readiness**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `h1`. Pass: a page's header (pageHero, art/experience/heroes.js), the same on each of its tabs, puts its h1 and buttons on the navy sky: the gradient #01203d to #062e57, and over each picture's empty left part, faded into that sky; the text always ends before the picture's sky line, which the page-heroes test checks for every picture at 12 widths. Measured in the browser under every glyph and button of all ten pages at 1920, 1440, 1251, 1101 and 961 px with the text hidden, the lightest pixel is #00396e: the title #ffffff is 11.6:1 at worst and the outline buttons' #ffffff labels 13.9:1. New notebook is #0b1420 on its own #f8991c fill, 8.4:1, and so is the Preview tag on its own #f8991c fill (axe can't read a picture).
- **Academy > Portfolio**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `h1`. Pass: a page's header (pageHero, art/experience/heroes.js), the same on each of its tabs, puts its h1 and buttons on the navy sky: the gradient #01203d to #062e57, and over each picture's empty left part, faded into that sky; the text always ends before the picture's sky line, which the page-heroes test checks for every picture at 12 widths. Measured in the browser under every glyph and button of all ten pages at 1920, 1440, 1251, 1101 and 961 px with the text hidden, the lightest pixel is #00396e: the title #ffffff is 11.6:1 at worst and the outline buttons' #ffffff labels 13.9:1. New notebook is #0b1420 on its own #f8991c fill, 8.4:1, and so is the Preview tag on its own #f8991c fill (axe can't read a picture).
- **Settings > Connections**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `h1`. Pass: a page's header (pageHero, art/experience/heroes.js), the same on each of its tabs, puts its h1 and buttons on the navy sky: the gradient #01203d to #062e57, and over each picture's empty left part, faded into that sky; the text always ends before the picture's sky line, which the page-heroes test checks for every picture at 12 widths. Measured in the browser under every glyph and button of all ten pages at 1920, 1440, 1251, 1101 and 961 px with the text hidden, the lightest pixel is #00396e: the title #ffffff is 11.6:1 at worst and the outline buttons' #ffffff labels 13.9:1. New notebook is #0b1420 on its own #f8991c fill, 8.4:1, and so is the Preview tag on its own #f8991c fill (axe can't read a picture).
- **Settings > Environment**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `h1`. Pass: a page's header (pageHero, art/experience/heroes.js), the same on each of its tabs, puts its h1 and buttons on the navy sky: the gradient #01203d to #062e57, and over each picture's empty left part, faded into that sky; the text always ends before the picture's sky line, which the page-heroes test checks for every picture at 12 widths. Measured in the browser under every glyph and button of all ten pages at 1920, 1440, 1251, 1101 and 961 px with the text hidden, the lightest pixel is #00396e: the title #ffffff is 11.6:1 at worst and the outline buttons' #ffffff labels 13.9:1. New notebook is #0b1420 on its own #f8991c fill, 8.4:1, and so is the Preview tag on its own #f8991c fill (axe can't read a picture).
- **Settings > Integrations**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `h1`. Pass: a page's header (pageHero, art/experience/heroes.js), the same on each of its tabs, puts its h1 and buttons on the navy sky: the gradient #01203d to #062e57, and over each picture's empty left part, faded into that sky; the text always ends before the picture's sky line, which the page-heroes test checks for every picture at 12 widths. Measured in the browser under every glyph and button of all ten pages at 1920, 1440, 1251, 1101 and 961 px with the text hidden, the lightest pixel is #00396e: the title #ffffff is 11.6:1 at worst and the outline buttons' #ffffff labels 13.9:1. New notebook is #0b1420 on its own #f8991c fill, 8.4:1, and so is the Preview tag on its own #f8991c fill (axe can't read a picture).
- **Notebook**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `.markdown > .gutter > .cell-grip[draggable="true"][title="Drag to move this cell"]`, `span[title="Fold line"]`. Pass: the drag grip is #616161, 6.2:1, and hidden from assistive tech; the cell's Move up and Move down buttons do the same from the keyboard. Pass: the fold marker is #616161 on white, 6.2:1.
- **Data mesh entity**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `.arrow[aria-hidden="true"]:nth-child(2)`, `.arrow[aria-hidden="true"]:nth-child(4)`, `.arrow[aria-hidden="true"]:nth-child(6)`, `.arrow[aria-hidden="true"]:nth-child(8)`, `#entity-toml`. Pass: the step arrows are #8a8a8a, 3.4:1 on white and 3.3:1 on #fafafa, and hidden from assistive tech (the steps' order is in the text). Pass: the entity file's text is #242424 on #fafafa, 14.9:1 (axe sees the box as partly covered).
- **Profile**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `h1`, `.outline`. Pass: a page's header (pageHero, art/experience/heroes.js), the same on each of its tabs, puts its h1 and buttons on the navy sky: the gradient #01203d to #062e57, and over each picture's empty left part, faded into that sky; the text always ends before the picture's sky line, which the page-heroes test checks for every picture at 12 widths. Measured in the browser under every glyph and button of all ten pages at 1920, 1440, 1251, 1101 and 961 px with the text hidden, the lightest pixel is #00396e: the title #ffffff is 11.6:1 at worst and the outline buttons' #ffffff labels 13.9:1. New notebook is #0b1420 on its own #f8991c fill, 8.4:1, and so is the Preview tag on its own #f8991c fill (axe can't read a picture).
- **Learn lab**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `.learn-mark`. Pass: an exercise check's mark is #242424 on white, 15.5:1 (passed is #1f7a4a, 5.3:1), and hidden from assistive tech (the check's text says it).
- **Settings > Integrations**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `h1`. Pass: a page's header (pageHero, art/experience/heroes.js), the same on each of its tabs, puts its h1 and buttons on the navy sky: the gradient #01203d to #062e57, and over each picture's empty left part, faded into that sky; the text always ends before the picture's sky line, which the page-heroes test checks for every picture at 12 widths. Measured in the browser under every glyph and button of all ten pages at 1920, 1440, 1251, 1101 and 961 px with the text hidden, the lightest pixel is #00396e: the title #ffffff is 11.6:1 at worst and the outline buttons' #ffffff labels 13.9:1. New notebook is #0b1420 on its own #f8991c fill, 8.4:1, and so is the Preview tag on its own #f8991c fill (axe can't read a picture).
- **Report a problem dialog**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `textarea[rows="3"]`, `.mono`. Pass: What happened is #242424 on white, 15.5:1, and its placeholder #757575, 4.6:1. Pass: the diagnostics are #424242 on #fafafa, 9.6:1.
- **Keyboard shortcuts dialog**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `.text:nth-child(6) > .learn-more[href="#"]`. Pass: in both runs the notebook guide link is #1b3664 on the dialog's white, 12.0:1, and the line it sits in is #616161, 6.2:1 (axe sees the list's last lines, which scroll under the dialog's buttons, as partly covered).
- **Engine picker's How to choose dialog**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `p:nth-child(10)`, `p:nth-child(10) > .md-link-off:nth-child(1)`, `p:nth-child(10) > .md-link-off:nth-child(2)`, `.scroll > b`. Pass: in both runs the decision pattern's text, links (a link that won't open is plain text with a dotted underline), headings and Copy example are #242424 on the dialog's white, 15.5:1, and the note beside Copy example is #616161, 6.2:1 (axe sees lines that scroll under the dialog's buttons as partly covered).
- **Workspaces > Databricks > Volumes on this PC**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `.small`. Pass: the volume's More actions button is the glyph ⋯ in #242424 on the card's white, 15.5:1 (axe can't measure a glyph it takes for an icon); its aria-label names it in words.
- **Workspaces > Power BI**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `tr:nth-child(1) > td:nth-child(1) > .star[aria-label="Favorite Sales performance"][title="Add to favorites"]`, `tr:nth-child(2) > td:nth-child(1) > .star[aria-label="Favorite Sales performance"][title="Add to favorites"]`. Pass: the favorite star is #8a8a8a, 3.4:1 on white and 3.3:1 on #fafafa (3:1 needed for a control's graphic); its button is named.
- **All notebooks**: color-contrast: Elements must meet minimum color contrast ratio thresholds. At `.foldable:nth-child(6) > .fold-head > .fold-toggle[title="Hide"] > .chev`, `.foldable:nth-child(7) > .fold-head > .fold-toggle[title="Hide"] > .chev`. Pass: the fold arrow is #616161 on white, 6.2:1 (3:1 needed for a control's graphic), and hidden from assistive tech (its button is named).
