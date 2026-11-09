# Accessibility at 200% and in a Windows contrast theme

Every page and every dialog is now checked at 200% zoom and in a Windows contrast theme, as well as in light and dark:

- Pages: `app/tests/browser/a11y-audit.test.cjs`.
- Dialogs: `app/tests/browser/a11y-dialogs.test.cjs`.

How each check works:

- **200% zoom.** The window's 1440x940 is laid out as 720x470.
  - No page scrolls sideways as a whole.
  - Each Tab stop is on screen, and not under something else, when it has focus.
  - A dialog is never wider than the window. It is taller only where its backdrop scrolls to the rest, and nothing it holds runs past its own edges.
- **Contrast theme (forced colors).** The theme sets the colors, so contrast isn't measured. Instead:
  - Each Tab stop must still show a focus ring. The theme removes rings drawn as box shadows.
  - Each dialog must have an edge.
- **Both.** axe's WCAG 2.1 A and AA rules run as they do in light and dark.

## Fixed in this change

| Where | What was wrong | Fix |
| --- | --- | --- |
| SQL query, 200% | The Compatibility panel kept its 240 px beside the query, so the editor was squeezed to about 90 px. The panel was also drawn over the query's text. | Below 900 px wide, the panel goes under the query. |
| Notebook and SQL editors, 200% | A long line made the editor scroll sideways, but axe didn't count the editor as reachable from the keyboard (`scrollable-region-focusable`). | Each editor's text area declares `tabindex="0"`. It was already a Tab stop. |
| 9 dialogs, 200% | Content ran off the bottom of the window and couldn't be scrolled to. These were: Edit profile, New repository, What's sent (usage data), Group naming, New landing zone, Request a source, Move to one landing workspace, Change a warehouse, and Remove a staging lakehouse. | In a window under 640 px tall, a dialog is as tall as its content and the backdrop scrolls. Wide dialogs keep their own scrolling panes. |
| Scorecard dialog, 200% | Its cards ran past the bottom of a wide dialog, which has a fixed height. | The cards are in a scrolling pane that can be reached with the keyboard. |
| SQL from a template, 200% | The script preview scrolled sideways but couldn't be reached from the keyboard. | It is a Tab stop, as the other script previews already are. |
| Every dialog, the command bar and toasts, contrast theme | With shadows and background colors gone, they had no edge, so a dialog's text sat directly on the page's. | In a contrast theme they get a border in the theme's text color. |

## For UX: works, but would read better with a layout change

The checks pass on all of these. They are not fixed here because each one needs a layout decision.

1. **The left rail at 200%.** Academy, Source control and Settings fall below the window. The rail scrolls and Tab reaches them, but they can't be seen without scrolling. One option is a compact rail (icons only) below a height.
2. **The SQL page's Explorer at 200%.** The Explorer keeps about 40% of the width, which leaves the query and results narrow. A way to collapse it would give the query the room.
3. **The Home hero at 200%.** It fills most of the first screen, so Start something and Getting started begin below the fold.
4. **Pictures in a contrast theme.** The theme keeps pictures as they are. These all keep their own colors, which is expected and readable but doesn't follow the theme:
   - the header wordmark
   - the page header art
   - the hero art

   Each of these already has its words as real text beside it.
