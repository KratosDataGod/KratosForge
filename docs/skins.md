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
| Brand and accent | `--accent`, `--accent-dark`, `--accent-soft`, `--accent-soft-2`, `--brand-orange`, `--brand-orange-soft`, `--on-accent` | Primary buttons, selection, the active rail item, text on accent fills |
| Text | `--text`, `--text-2`, `--muted`, `--muted-2` | Body text, secondary text, hints |
| Surfaces | `--surface`, `--surface-2`, `--surface-3`, `--surface-3b`, `--canvas`, `--hover`, `--neutral-mark` | Cards, panels, table stripes, the page background, hover |
| Lines | `--border`, `--border-soft`, `--border-input` | Borders and separators |
| Status | `--ok`, `--ok-2`, `--ok-3`, `--ok-soft`, `--warn`, `--warn-strong`, `--warn-2`, `--warn-ink`, `--warn-soft`, `--warn-soft-2`, `--warn-soft-3`, `--info`, `--info-soft`, `--danger`, `--danger-soft`, `--danger-soft-2` | Success, warning, information and error text and fills |

Today's tokens hold exactly the colors the rules used before, so moving a rule onto a token changes
nothing on screen. Some near-duplicates (`--surface-3` and `--surface-3b`, three warning text colors)
are kept apart for that reason; they can be merged when a design review agrees the difference doesn't
matter.

About 155 colors are still written straight into rules (rarely used shades). A test in
`app/tests/ui.test.cjs` lets that number only go down: a new rule uses a token, or adds one to `:root`.
