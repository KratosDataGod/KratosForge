# Narrator checklist (EA-030)

The second half of the accessibility pass (PB-063 part 2), done on a Windows PC with Narrator. It takes about 20 minutes. The automated half already runs on every pull request: axe on every page and dialog ([accessibility-audit.md](accessibility-audit.md), `a11y-dialogs.test.cjs`), a keyboard-only walk of every rail page (`keyboard-pass.test.cjs`) and the dialog keyboard checks (`dialog-keyboard.test.cjs`). Those tests run in Chromium, so they can't hear what Narrator says. This list covers only what a person has to listen for.

Each "Expect" line comes from what the app tells assistive technology on `main`, read in Chromium's accessibility tree. Narrator words things in its own way ("button" or "Button", "current page" or "current"), and that's fine. Mark **Fail** when a name is missing, wrong or not read at all, or when focus lands somewhere you can't tell where it is.

## Before you start (2 minutes)

- Use the Preview build, signed in to the test tenant `kf_tenant_test`. Nothing in this list writes to Fabric.
- Turn Narrator on with **Ctrl+Win+Enter**. The Narrator key is **Caps Lock** (or Insert).
- Keys you'll use:
  - **Tab** and **Shift+Tab** move between controls.
  - **Caps Lock+Space** turns scan mode on and off.
  - In scan mode, **H** jumps to the next heading.
  - **Caps Lock+0** reads the current item again.
  - **Esc** closes a dialog.
- Write P (pass) or F (fail) in the Result column. Add a note for each F, quoting what Narrator said where you can.

## 1. The window (2 minutes)

| # | Do | Expect | Result |
|---|---|---|---|
| 1.1 | Click the window title, then press Tab once | "Skip to content, link" | |
| 1.2 | Keep pressing Tab through the top bar | "Search or run a command Ctrl+K", then "Activity", with the number of new problems if there are any, then the account button by its name | |
| 1.3 | Keep pressing Tab into the left rail | "Main" navigation. Each page is read as a button with its name, and the page you're on adds "current page" | |
| 1.4 | Press Shift+Tab back to "Skip to content" and press Enter | Focus moves to the page itself, not the top of the window | |

## 2. Each rail page (8 minutes)

On each row: Tab to the page in the rail, press Enter, then turn on scan mode and press H once.

**Listen for:** after you press Enter, Narrator should say the page changed. The app doesn't move focus or announce the new page; focus stays on the rail button. So if Narrator says only the button's name again, write F on that row and note "no page change heard". That gap is in the code, not a Narrator setting.

| # | Rail button | Expect from H (first heading) | Also check | Result |
|---|---|---|---|---|
| 2.1 | Home | "Welcome back, *your name*, heading level 1" | Tab reaches "New notebook" in "Start something" | |
| 2.2 | Create | "Create, heading level 1" | Each tile reads its name and its one-line description, for example "Notebook Explore, transform and write Delta tables with Python" | |
| 2.3 | Workspaces | "Workspaces, heading level 1" | The tabs read as "Your workspaces, tab, selected, 1 of 4". In the table, a row's buttons read "Show items", "Pin to the top" and "Add to favorites" | |
| 2.4 | OneLake | "OneLake catalog, heading level 2". This page has no level 1 heading, so note whether that confuses you | The tabs "Catalog", "Shortcuts" and "Access", and the search box "Search the catalog". While loading, "Loading items from all workspaces" is read once without moving focus | |
| 2.5 | Data mesh | "Data mesh, heading level 1" | The View buttons say which one is pressed: "My team, toggle button, pressed" | |
| 2.6 | Platform | "Platform Preview, heading level 1" | The Environment group reads "Dev", "Test" and "Prod" as buttons in a group called Environment | |
| 2.7 | Optimize | "Optimize Preview, heading level 1" | The tabs "Overview", "Opportunities", "Cost and savings" and "Settings history" | |
| 2.8 | Monitor | "Monitor, heading level 1" | The groups "KratosForge pages", "Environment" and "Where it ran" each say which button is pressed. "Problems first" reads as a checkbox, checked | |
| 2.9 | Academy | "Academy Preview, heading level 1" | The six tabs, from "Home" to "Portfolio" | |
| 2.10 | Source control | "Source control, heading level 1" | "Loading…" is read once, then the page's content | |
| 2.11 | Settings (bottom of the rail) | "Settings, heading level 1" | Tab moves through the settings. Each switch or box reads its own label | |

## 3. Dialogs (6 minutes)

Every dialog should read its title first ("…, dialog"), then the control focus landed on. While it's open, Tab should stay inside it. Esc should close it and Narrator should then read the button you started from.

**Listen for:** none of these dialogs links its text to its title. If Narrator reads only the title and the focused button, but not the sentence that explains what will happen (for example in 3.4), write F and note it.

| # | How to open | Expect when it opens | Then | Result |
|---|---|---|---|---|
| 3.1 | Press **?** while focus isn't in a text box | "Keyboard shortcuts, dialog", then "Close, button" | In scan mode, H reads "Anywhere", "Moving around" and so on, and the table rows read the key and what it does | |
| 3.2 | Press Ctrl+K, type `report`, press Enter | "Report a problem, dialog", then "Close, button" | Tab reaches "What happened, edit" and then "Sent with it…", which reads as a text box. Press Esc | |
| 3.3 | Home › All notebooks. On any notebook, open "More actions for *name*" and choose Rename… | Opening the menu reads "menu" and its first item. The dialog reads "Rename notebook, dialog", then "Name, edit", with the current name selected | Press Esc. Nothing is renamed | |
| 3.4 | On the same notebook, choose Delete… | "Delete *name*?, dialog", then the focused button. The sentence under the title should also be read, once on open or after Caps Lock+0 | **Press Cancel.** Nothing is deleted | |
| 3.5 | Press Ctrl+K and type `mon` | "Command bar, dialog", then "Command bar, edit" | Press Down arrow twice. **Listen for:** each result you move to should be read, for example "Monitor". If nothing is read, write F | |

## 4. Messages that appear on their own (2 minutes)

| # | Do | Expect | Result |
|---|---|---|---|
| 4.1 | Create › Notebook. Type `1+1` in the first cell and run it with Shift+Enter | "Cell 1 succeeded" is read without focus moving | |
| 4.2 | In that notebook, type `1/0` in a cell and run it | "Cell 2 failed" is read without focus moving | |
| 4.3 | Note any message you saw at the bottom of the window during the pass (a toast) | It was read when it appeared. An error is read straight away. Other messages are read at the next pause | |

Delete the test notebook afterwards from All notebooks (More actions › Delete…). It exists only on this PC.

## When you're done

Send the table back, or paste the F rows with your notes. Each F becomes an issue, labelled for EA-030 in [REMEDIATION_BACKLOG.md](../audits/REMEDIATION_BACKLOG.md). Three rows are expected to fail from reading the code: the page change in section 2, the dialog sentence in 3.4, and the command bar results in 3.5. Hearing them confirms them before they're fixed.
