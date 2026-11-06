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
`--ok-ink`, `--danger-ink`, `--code-ref` and others) holds shades that more than one rule shared. They
are not in the table above, so a skin can't set them yet; dark mode will need them added once the
near-duplicates among them are merged.

74 colors are still written straight into rules (shades used once, the splash screen and the
celebration effects). A test in `app/tests/ui.test.cjs` lets that number only go down: a new rule uses a
token, or adds one to `:root`.

## Skin files

A skin is a small JSON file, checked by `fabric_desktop/skins.py` whether it ships with the app or someone
imports it. It holds two things:

- **appearance**: the color mode it starts in, its density, and colors for the tokens above, for light and dark.
- **workflow**: the order of the left rail's pages, by page id, and short labels for them.

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

Two settings choose the skin: `ui_skin` (its id, `kratosforge` by default) and `ui_color_mode`
(`skin` follows the skin's own mode, or `light` or `dark`). See
[the settings file](azure-setup.md).

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
| Databricks-familiar | A blue accent, and Workspace, Recents, New, Catalog and Jobs and pipelines at the top of the rail, with those names, as Databricks developers expect. A dark version follows the mockup and shows once dark mode lands. It follows Databricks' documented layout, not its branding: no Databricks logos or assets. |
| Snowflake-familiar | A blue accent and light surfaces, and Worksheets, Data, Load and create and Monitoring at the top of the rail, in that order, with those names, as Snowflake developers expect. It follows Snowflake's documented layout, not its branding: no Snowflake logos or assets. |

Every skin keeps all of KratosForge's pages (local runs, compare local vs cloud, Optimize, shared
services, Learn), at most 8 main pages in the rail, and Settings at the bottom. In a platform skin, KratosForge's own
pages (Platform, Optimize and Data mesh) sit together under a "KratosForge" label in the rail, so you can
see what the app adds to the platform you know. Every skin also keeps the same "where it runs" chip
beside the notebook's engine, such as "This PC · no Fabric job" (reading or writing OneLake from this PC still uses Fabric
capacity), and a skin can't hide or change it.
Dark versions of the
skins come once the rest of the app's colors are on tokens. A skin renames rail entries only: the pages
themselves, and where code runs, stay KratosForge's.
