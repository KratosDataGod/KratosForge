# UI guidelines: adding a feature without moving things people know

KratosForge is used by people who learned where things are. New features go where those people already look. These rules keep it that way; `app/ui/features.js` enforces the first two.

## Where a new page goes

- The left rail holds at most 8 pages (`RAIL_LIMIT`). Today: Home, Create, Workspaces, OneLake, Data mesh, Platform, Fabric, Monitor, with Settings at the bottom. The rail is full: a new page now goes inside one of these.
- A new feature becomes a **tab or group inside an existing page**. The Data mesh page's tabs are in groups (Design, Runs, Quality, Domains, Deploy; `META_TAB_GROUPS` in `meta.js`); a new metadata feature joins a group, not a fifth row.
- The rail grows only for a new kind of work, and only after a mockup is agreed.
- Settings for a feature go on the Settings page (`settings.js`), in an existing section where one fits.

## Registering a feature

Every page registers itself once:

```js
registerFeature({
  id: "monitor",
  rail: { label: "Monitor", icon: "monitor", order: 70 },   // leave out for pages opened from elsewhere
  views: { monitor: renderMonitor },                          // view id -> render function
  commands: [                                                 // found by the command bar (Ctrl+K)
    { label: "Cost", icon: "gauge", keywords: "capacity cu", preview: true, run: () => { ... } },
  ],
});
```

Register in the file that defines the page's render function, at the end of it.

## Keyboard shortcuts

A feature with keys of its own registers them once, and the **Keyboard shortcuts** dialog (**?**, the command bar, Settings) lists them with the app-wide ones. There is no second list to keep up to date:

```js
registerShortcuts("Notebook", [
  { keys: ["Shift+Enter"], label: "Run the cell and select the next one", when: () => state.view === "notebook" },
  { keys: ["Ctrl+Enter", "Ctrl+Shift+Enter"], label: "Run the selected cells" },
]);
```

- `keys` is what to press, each combination written with `+`; several mean any of them. `label` says what it does, in plain words. `when` (optional) lists it only while it returns true.
- A key the app handles everywhere also gets an `id`, `match` (its `KeyboardEvent.key` values) and `mod: true` when Ctrl or Cmd is held. Its handler asks `isShortcut(e, id)`, so the key and its line in the list can't drift apart. Add it to the table in [Keyboard shortcuts](user-guide.md#keyboard-shortcuts) too.
- A plain key (no Ctrl) must do nothing while someone is typing: check `typingIn(e.target)`.
- Keys that work only on one page are registered too, description only (no `id`), with a `when` for that page, next to the page's code. The handler stays where it is.

## Preview features

A feature that hasn't been tried on a real Fabric tenant is in preview:

1. Add it to `PREVIEW_FEATURES` in `features.js`.
2. Wrap its tab or button: `preview("cost", button)`. It gets a Preview tag, or disappears when someone turns preview features off in Settings.
3. Mark its commands `preview: true`.

A core step that can't disappear (promote, say) uses `previewMarked("promote", button)` instead: it always shows, always with the Preview tag, even when preview features are turned off. Add its id to `PREVIEW_ALWAYS_SHOWN` too, so Settings says it stays.

When it has been tried on a tenant, remove it from `PREVIEW_FEATURES`. The tag goes away everywhere at once.

## How this works

Every page and tab says, beside its content, what it does with people's data. Use the shared panel:

```js
body.replaceChildren(withHowItWorks(content, howItWorks("platform.overview", {
  reads: "What it reads, from where, and when.",
  changes: "What it changes, or Nothing.",
  results: "Where results go and who sees them.",
  anchor: "platform",  // its section of user-guide.md
})));
```

- Three short answers in plain words, then the guide link. Add more parts with `more: [["Undo", "..."]]` when a page needs them.
- "What it changes" names anything that writes to Fabric or OneLake and says that it asks first.
- The panel folds to its title and remembers that on the PC, so people who know the page can put it away.
- It sits on the right, and moves under the content in a narrow window.
- **On request** is the default for **Settings > Appearance > Layout > Explanations**: the panel leaves the page and opens as a drawer from the title bar's info button. That works for any panel made with `howItWorks`, so don't build a page's own toggle for it. A panel inside a Home card, a notebook side pane or query results starts folded to its title where it is, and a dialog's panel opens unfolded every time (showDialog), because it says what the dialog's button changes.
- Write a page's one-line explanation of what it is for with `pageIntro(...)`, not a plain `p.sub`. On request it folds into the same drawer above the panel; a page with no panel gets an "About this page" drawer, and its Learn more link moves there. Keep facts and warnings (counts, a missing setup step) as plain `.sub` so they stay on the page.
- Mark Preview features with `previewTag()` (or `preview`/`previewMarked`), never a tag of your own, so **Preview marks: Quiet dots** turns them into dots. A tag that's a direct child of an `h1` stays a full tag.

## Pop-out windows

A section people watch while they work elsewhere (a run list, a monitoring hub) can open in a window of its own, to keep on another screen. Monitor's two sections are the first ([user guide](user-guide.md#pop-out-windows-preview)); a new one uses the same pattern, in the same words:

- **Pop out** (`popOutButton(section)`, icon `open`, Preview) goes on the section's own band, last before **Hide** when there is one. It shows only in the main window.
- While the section is out, the main window keeps a band in its place (`poppedOutBand`): the section's title, "In a window of its own", **Show window** and **Dock back**. The rest of the page takes the room.
- The window shows only that section: no rail, no search, no bell. Its top bar has **Keep on top** and **Dock back**, plus Back and Forward when the section opens other pages. Closing the window docks it back.
- A pop-out window starts nothing: no component check, no update, no splash, and it never changes the page the main window reopens on. A list in it refreshes itself in place, keeping the scroll position, and never while a dialog is open or the window is minimized.
- Links that belong to the main window (the Fabric portal) open there, not in the pop-out.
- The window's title is "KratosForge · *section title*", so it can be told apart on the taskbar.

Adding a section today means listing it in three places: `POP_OUTS` in `main.rs` (its window title), `POP_OUT` in `app.js` and the section check in `listenForPopOuts` (`popout.js`). `startPopOut` opens Monitor; a section on another page moves that into a small registry (section, page, render) first, in its own pull request.

## Action-first standard

Each screen leads with its most valuable action, recommends a next step only from evidence, keeps every other permitted action reachable, and gives feedback for every action. The standard, review checklist, baseline and backlog are in [Action-first experience](ux/action-first.md). The shared code is `app/ui/actions.js`:

```js
defineAction({
  id: "notebook.run_all", label: "Run all", icon: "play", contexts: ["notebook"],
  risk: "moderate", where: "local", primary: true,
  available: (ctx) => (ctx.busy ? "A run is already going." : true),   // a reason the person can read
  run: (ctx) => runAll(),
  done: (result) => ({ text: "Ran every cell", next: ["notebook.publish"] }),
});

defineRecommendation({
  id: "notebook.warnings", contexts: ["notebook"], reason: "validation", basis: "verified",
  action: "notebook.validate", when: (ctx) => (ctx.warnings ? { n: ctx.warnings } : null),
  why: (e) => `${e.n} validation warnings to review before you publish`,
});
```

- `risk` is `low`, `moderate` or `high`; `where` is `local`, `fabric` or `either`. A `high` action needs `confirm` (shown as a danger dialog) or `ownConfirm` naming the dialog that already asks; one with `spendsCapacity` needs `riskCheck` or `ownRiskCheck`. Without them `defineAction` throws when the app loads.
- `available` is a hint for the screen. The engine still checks: an action that changes Fabric or data asks the engine first.
- A page tells the framework what it knows with `provideActionContext(view, () => ({ ... }))`.
- Every action is found by the command bar. `actionBar(ctx)` draws the primary button and a More menu; `nextSteps(ctx)` draws up to three recommendations; `runAction(id)` runs one with the checks above; `doneToast(text, nextIds)` ends with the next step.
- Recommendations are ranked by `reason` alone (failure, safety, validation, unfinished, local, continue). Never add a score.

## Words and help

- Plain words people use, not internal names. "Run in Fabric", not "submit job".
- Ask before deleting or overwriting data with `confirmDialog(title, text, verb, { danger: true })`: the verb button is red and Cancel has focus. Title the question "Delete *name*?" or "Overwrite *name*?", say in the text what is lost, and use the action's verb on the button. Add `typeToConfirm: name` for overwriting a table or anything in Prod.
- Every page header ends with `learnMore("<anchor>")`, linking to its section of [user-guide.md](user-guide.md). Add the section in the same pull request.
- Errors go through `friendlyError` (`activity.js`), which turns known causes into a sentence and says what to do.

## UX review

A pull request that changes a screen gets a UX review against the [usability release gates](ux/release-review.md). The [navigation architecture](ux/navigation.md) says where planned features go; a new rail item, Monitor segment or Platform tab needs a mockup first.

## Releases

Merges to `main` go to the **Preview** channel (the `latest` release) straight away. People on **Stable**, the default, only get a build when someone runs the **Promote to stable** workflow, whose notes become What's new. Promote after a build has been tried on Preview.
