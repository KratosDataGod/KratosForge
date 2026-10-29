# Embed Studio (Preview)

See how a Power BI report, paginated report or dashboard behaves when it's embedded in an app, before you build the app. Embed Studio opens it through Microsoft's own embedding SDK, signed in as you, and shows its pages, the events it raises and what the SDK supports for it.

It isn't offline and it isn't free of capacity: Power BI draws the content and runs its queries on the workspace's capacity, exactly as when you view it in Fabric.

| You want to | Where |
| --- | --- |
| Find a report, paginated report or dashboard | **Workspaces › Power BI** (shown when preview features are on) |
| Open it embedded | **Open in Embed Studio** on its row |
| Change page | The page buttons above the report. Power BI's own page list is hidden, as most apps hide it |
| See what it raised (loaded, rendered, page changed, data selected, errors) | The **Events** panel. **Copy events** copies them as JSON, with tokens taken out |
| See what the SDK supports for it | The **Supports** tab beside it |
| Open it in Fabric instead | **Open in Fabric** |

## What you need

- Access to the workspace: at least Viewer.
- To view it, a Power BI Pro or Premium Per User licence, unless the workspace is on F64 or larger capacity.
- The workspace's capacity running. Embed Studio never resumes a capacity; if it's paused, it says so and offers **Open in Fabric**.

Embed Studio asks for Power BI's `Report.Read.All`, `Dashboard.Read.All` and `Dataset.Read.All` the first time you open something ([Permissions](permissions.md)).

## Your token

Embedding with your own sign-in ("user owns data") hands Power BI's frame your Power BI access token. Embed Studio only reads, but the token can do anything you can do in Power BI with KratosForge's permissions, so it is handled like this:

- It stays in memory: never on disk, in browser storage, in a log, in the events or in an error.
- It goes only to `app.powerbi.com`, through the embed host frame, which can frame nothing else ([Security](security.md#content-security-policy)).
- It is renewed by itself a few minutes before it expires, about every hour.

No app registration or service principal is used, and nothing is kept in KratosForge's settings.

## Links in a report

A link in a table or matrix opens in your default browser, and only an `https` link. Other links (in a text box or on a button) don't open yet. The embed frame itself can't open windows or take over the app's window.

## When it can't open

The report area says what happened and the one thing to do next:

| It says | What to do |
| --- | --- |
| Your account can't open this | Ask the workspace's admin for at least Viewer access |
| This content needs a Power BI licence | Ask your admin for a Pro or Premium Per User licence, or for the workspace on F64 or larger capacity |
| Your sign-in to Power BI has expired | **Sign in**: the content reopens |
| The workspace's capacity isn't running | Resume it in Fabric, or ask your capacity admin |

Power BI's own error, with its request id, stays in the Events panel for **Report a problem**.

## Not yet

- Q&A isn't offered: Microsoft retires it, embedded included, in December 2026.
- Power BI in government and sovereign clouds (such as `app.powerbigov.us` or `app.powerbi.cn`) doesn't open.
- Filters, slicers, bookmarks, visuals, tiles and code generation come in later phases ([Power BI embedding](architecture/power-bi-embedding.md)).
- Not yet tried on a live tenant, so it's Preview.
