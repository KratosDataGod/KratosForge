# Platform skins: design tokens

KratosForge is getting platform skins: a KratosForge look, and looks familiar to Microsoft Fabric,
Azure Databricks and Snowflake developers. A skin changes how the app looks and how its pages are
arranged. It never changes your projects, connections, credentials, or which engine runs your code.
This page covers the first piece, the design tokens. It grows as the skin work lands.

## Design tokens

Every color in `app/ui/styles.css` is meant to come from a token on `:root`, named for what it means
(`--warn`, `--ok-soft`, `--surface-2`, `--on-accent`), not for its value. A skin, or a dark mode, sets
new values for the tokens and the whole window follows. Measured in the
[app window benchmark](performance.md#the-app-window), restyling the window for new token values takes
a few milliseconds, so a skin switches at once, without reloading, and open work stays as it is.

| Group | Tokens | Used for |
| --- | --- | --- |
| Brand and accent | `--accent`, `--accent-dark`, `--accent-soft`, `--accent-soft-2`, `--brand-orange`, `--brand-orange-soft`, `--rail-active`, `--on-accent` | Primary buttons, selection, the active rail item's stripe (`--rail-active`), text on accent fills |
| Text | `--text`, `--text-2`, `--muted`, `--muted-2` | Body text, secondary text, hints |
| Surfaces | `--surface`, `--surface-2`, `--surface-3`, `--surface-3b`, `--canvas`, `--hover`, `--neutral-mark` | Cards, panels, table stripes, the page background, hover |
| Lines | `--border`, `--border-soft`, `--border-input` | Borders and separators |
| Status | `--ok`, `--ok-2`, `--ok-3`, `--ok-soft`, `--warn`, `--warn-strong`, `--warn-2`, `--warn-ink`, `--warn-soft`, `--warn-soft-2`, `--warn-soft-3`, `--info`, `--info-soft`, `--danger`, `--danger-soft`, `--danger-soft-2` | Success, warning, information and error text and fills |

Today's tokens hold exactly the colors the rules used before, so moving a rule onto a token changes
nothing on screen. Some near-duplicates (`--surface-3` and `--surface-3b`, three warning text colors)
are kept apart for that reason; they can be merged when a design review agrees the difference doesn't
matter.

A second set of tokens on `:root` (`--border-warm`, `--warn-border`, `--warn-soft-4`, `--warn-soft-5`, `--warn-soft-7` to `--warn-soft-9`, `--warn-ink-2`,
`--ok-ink`, `--danger-ink`, `--code-ref`, the syntax colors (`--code-keyword`, `--code-string`, `--code-function`, `--code-comment`, `--code-number`, `--code-builtin`), `--text-disabled`, `--bar-neutral` and others) holds shades that more than one rule shared. They
are not in the table above, so a skin can't set them yet; dark mode will need them added once the
near-duplicates among them are merged.

50 colors are still written straight into rules (shades used once, the splash screen and the
celebration effects). A test in `app/tests/ui.test.cjs` lets that number only go down: a new rule uses a
token, or adds one to `:root`.

## Skin files

A skin is a small JSON file, checked by `fabric_desktop/skins.py` whether it ships with the app or someone
imports it. It holds two things:

- **appearance**: the color mode it starts in, its density, colors for the tokens above, for light and
  dark, and its look (see [Look](#look-preview)).
- **workflow**: the order of the left rail's pages, by page id, and short labels for them, and which
  notebook commands go on the Home ribbon (see [Notebook commands](#notebook-commands-preview)).

```json
{
  "schema": "fabric-desktop.skin/1",
  "id": "my-team",
  "name": "My team",
  "version": "1.0.0",
  "targets": [{"platform": "fabric", "reviewed": "2026-11-03", "basis": "Fabric documentation, November 2026"}],
  "appearance": {"color_mode": "light", "density": "compact", "tokens": {"light": {"--accent": "#0f6c5c"}}},
  "workflow": {"rail": ["workspaces", "home", "create"], "labels": {"metadata": "Catalog"}}
}
```

`targets` says which platform's experience the skin follows and when it was last compared with that
platform's documentation, so a skin that has drifted can be found and reviewed.

What a skin file can't do, because the check refuses it:

- Hold anything but those keys. An unknown key is refused, not ignored.
- Hold code, CSS text or images, or load anything. A token value is a color like `#1b3664` and nothing
  else, and text in a skin (its name, description and labels) is only ever shown as text: no link in it
  is followed or loaded.
- Set an engine, a connection, a credential, a permission or anything about a project. Those live
  elsewhere (Settings > Connections, the engine picker) and a skin can't reach them.
- Make text unreadable. Text colors must reach 4.5:1 contrast on the surfaces they sit on.
- Be larger than 64 KB.

## Look (Preview)

`appearance.style` sets how the app is drawn, part by part, with fixed choices only. The app draws each
choice with its own styles and the fonts it ships with, so a skin never carries CSS, fonts or images. A
part the skin leaves out keeps KratosForge's own look (the first choice listed).

| Part | Choices |
| --- | --- |
| `font` | `segoe-variable`, `segoe`, `dm-sans`, `inter` |
| `header` | `light`, `brand`, `dark`, `none`, `hero` (KratosForge skin only) |
| `rail` | `stacked` (icon above label), `inline` (icon beside label) |
| `corners` | `soft`, `square`, `round` |
| `tabs` | `underline`, `boxed`, `pill` |
| `cells` | `bordered`, `card`, `flat`, `rounded` |
| `buttons` | `accent`, `forge` |
| `tiles` | `plain`, `duotone` |
| `icons` | `regular`, `light`, `bold` |

The app marks the page with one attribute per part, such as `data-skin-rail="inline"`. The built-in
platform skins set these, as drawn in the "skin look and feel" mockup:

| Skin | Font | Header | Rail | Corners | Tabs | Cells | Icons |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Fabric-familiar | Segoe UI | brand | stacked | soft | underline | card | light |
| Databricks-familiar | DM Sans | light | inline | square | boxed | flat | bold |
| Snowflake-familiar | Inter | none | inline | round | pill | rounded | regular |

What each choice draws so far:

- **font:** `segoe-variable` and `segoe` use Windows' own Segoe UI. `dm-sans` and `inter` use open-licence
  fonts the app ships in `app/ui/vendor/fonts` (SIL Open Font License 1.1). They load from the app itself,
  never from the internet: the app's content security policy allows fonts from its own files only.
- **corners:** `soft` keeps KratosForge's 4, 6 and 8 px corners (`--r-sm`, `--r-md`, `--r-lg`). `square`
  makes them 2, 3 and 4 px, and `round` makes them 6, 8 and 12 px. Pills stay pills.
- **icons:** the app's icons are filled shapes. `bold` traces their edge so they read heavier. `regular`
  and `light` draw them as they are.

- **rail:** `stacked` is KratosForge's narrow rail. `inline` is a wide rail, 208 px, with each label beside
  its icon. In a window narrower than 1000 px, such as when zoomed in, it draws as stacked so the page keeps
  its room. It uses the rail's own colors, so it works on a light or a dark rail.
- **header:** `light` is KratosForge's white bar. `brand` fills the bar with the skin's accent. The bar's
  text, buttons and lettering turn to `--on-accent`, and the search box sits on `--accent-dark`. `none`
  blends the bar into the canvas, without its line. The bar keeps search, the breadcrumb, Activity and
  the account in every skin. `dark` and `hero` belong to the KratosForge skin's redraw and draw as
  `light` until it lands.

- **tabs:** `underline` is KratosForge's. `boxed` raises the chosen tab like a file tab on the line below
  it. `pill` fills the chosen tab as a pill, with no line under the row.
- **cells:** `bordered` is KratosForge's, with an accent edge on the chosen cell. `card` gives cells a
  soft border, larger corners and an accent ring when chosen. `flat` keeps square cells with their header
  bar always shown. `rounded` uses large corners and a soft ring. In every style, a cell being edited
  keeps KratosForge's orange edge, so edit and command mode still look different.

`buttons` and `tiles` belong to the KratosForge skin's redraw (`forge` buttons, `duotone` tiles). Until
it lands they draw as `accent` and `plain`.

Two settings choose the skin: `ui_skin` (its id, `kratosforge` by default) and `ui_color_mode`
(`skin` follows the skin's own mode, or `light` or `dark`). See
[the settings file](azure-setup.md).

## Notebook commands (Preview)

A skin can place the notebook's commands on the left of the Home ribbon, the way its platform's
notebooks put them. It sets `workflow.commands.notebook`:

```json
"commands": {
  "notebook": {
    "primary": "notebook.run_all",
    "bar": ["notebook.run_cell", "notebook.restart_run_all", "notebook.publish"],
    "labels": {"notebook.run_cell": "Run cell", "notebook.restart_run_all": "Clear state and run all"}
  }
}
```

- **primary** is the solid button, and **bar** holds up to 3 more, in the skin's order.
- **More** lists every notebook command the skin didn't place, in the order the app defines them. A skin
  can't hide a command.
- **labels** rename a command, in 40 characters or fewer. Only a low or moderate command that stays on
  this PC can be renamed, so Publish to OneLake and Run in Fabric always keep their names. A label can't
  be another command's own name, or a name the skin gives another command (case doesn't matter), so a
  renamed command never reads as a different one.
- The ids are the notebook's 9 commands: `notebook.run_all`, `notebook.run_cell`,
  `notebook.restart_run_all`, `notebook.validate`, `notebook.save`, `notebook.publish`,
  `notebook.run_in_fabric`, `notebook.go_failed` and `notebook.pin`. A test keeps the engine's list equal
  to the app's.

What stays the same in every skin:

- A command asks what it asked before, runs the same risk check and is unavailable in the same cases.
  One that can't run now stays where the skin put it, marked, and says why when chosen.
- After a failed run, Go to the failed cell takes the primary slot. The skin's primary moves beside it
  and its last bar command drops into More, so the group never grows.
- Everything else on the Home ribbon stays where it is: Cancel, the engine, Check, Add data items,
  Share and Schedule.
- Publish keeps its options. On the bar it has its chevron, as on KratosForge's ribbon; under More,
  Change publish workspace follows it.
- The command bar (Ctrl+K) lists the skin's primary and bar commands first, under the skin's names. A
  command's own name still finds it.

The KratosForge skin places no commands, so its ribbon keeps Run all, Save and Publish to OneLake where
they've always been. The three platform skins place them like this:

| Skin | Primary | Beside it |
| --- | --- | --- |
| Fabric-familiar | Run all | Run cell, Save |
| Databricks-familiar | Run all | Run cell, Clear state and run all, Publish to OneLake |
| Snowflake-familiar | Run all | Run, Save |

A skin that breaks these rules is refused with every reason listed, and the skin in use stays on.

## Choosing a skin (Preview)

**Settings > Appearance > Skin** shows one card per built-in skin. Choosing one changes the colors and
the order of the left rail at once. Nothing else changes: the page you're on, open notebooks and their
unsaved edits, editors, selections, connections and engines stay exactly as they were, and the switch
calls the engine only to save the setting. The choice is kept for the next start. The card shows while
Preview features are shown, and an administrator's policy can lock it.

| Skin | What it changes |
|---|---|
| KratosForge | Nothing: the app's own look. Every engine and platform side by side, local runs up front. |
| Fabric-familiar | A teal accent and lighter surfaces, and Create, Workspaces, OneLake and Monitor at the top of the rail, as Fabric developers expect. It follows Fabric's documented workspace-first layout, not its branding: no Microsoft logos or assets. |
| Databricks-familiar | A blue accent, with the active rail item's stripe in the same blue, and Workspace, Recents, New, Catalog and Jobs and pipelines at the top of the rail, with those names, as Databricks developers expect. A dark version follows the mockup and shows once dark mode lands. It follows Databricks' documented layout, not its branding: no Databricks logos or assets. |
| Snowflake-familiar | A blue accent and light surfaces, with the active rail item's stripe in the same blue, and Worksheets, Data, Load and create and Monitoring at the top of the rail, in that order, with those names, as Snowflake developers expect. It follows Snowflake's documented layout, not its branding: no Snowflake logos or assets. |

Every skin keeps all of KratosForge's pages (local runs, compare local vs cloud, Optimize, shared
services, Learn), at most 8 main pages in the rail, and Settings at the bottom. In a platform skin, KratosForge's own
pages (Platform, Optimize and Data mesh) sit together under a "KratosForge" label in the rail, so you can
see what the app adds to the platform you know. Every skin also keeps the same "where it runs" chip
beside the notebook's engine, such as "This PC · no Fabric job" (reading or writing OneLake from this PC still uses Fabric
capacity), and a skin can't hide or change it.
Dark versions of the
skins come once the rest of the app's colors are on tokens. A skin renames rail entries only: the pages
themselves, and where code runs, stay KratosForge's.
