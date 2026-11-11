---
site_group: Internal engineering
---

# Accessibility at 200% and in a Windows contrast theme

> **Internal — engineering working material.** This is a dated implementation, design, or planning record. Use the user guides for current instructions and check releases for shipped behavior.

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

## Short and narrow windows

UX chose these layout changes after the checks above. `app/tests/browser/short-window.test.cjs` checks them.

| Where | Before | Now |
| --- | --- | --- |
| The left rail, under 640 px tall | Academy, Source control and Settings fell below the window. The rail scrolled to them. | The rail shows icons only, closer together, so every item fits. Each name is still the button's text, so it is the accessible name, and it shows as a tooltip. The current page keeps its stripe and focus keeps its ring. |
| The SQL page's Explorer | It kept about 40% of the width at 200%, leaving the query narrow. | A button in the Explorer's header collapses it to a thin strip with an expand button. The choice is kept on this PC. With no choice made, a window under 900 px wide starts with it collapsed. |
| The Home hero, under 640 px tall | It filled most of the first screen, so Start something and Getting started began below the fold. | It is one strip with the welcome and its actions. The kicker, the title and the art are left out. |

## Pictures in a contrast theme

A contrast theme keeps pictures in their own colors. The Home hero art (`.home-hero-img`) is hidden in a contrast theme (under `forced-colors: active` in `styles.css`), so no page shows them there. The header wordmark keeps its colors, which is right: it is the product's mark, and its name is real text beside it.
