# Power BI embedding architecture (proposed)

**Status: proposed, not built.** Nothing on this page is in the app yet. It records the design for
**Embed Studio**, a Power BI embedded analytics development view, and what Microsoft's SDK supports
for each kind of content, so the stories (PBE-01 to PBE-24) are built against checked facts. The
proposal and its decisions are with the product owner.

Today a report, dashboard or paginated report opens in Fabric's own portal page in the Fabric view
(see [security](../security.md), SH-12). That shows the content but can't drive it: no filters,
bookmarks, events, timings or code to take away. Embed Studio adds that with Microsoft's
[`powerbi-client`](https://learn.microsoft.com/javascript/api/overview/powerbi/) JavaScript SDK.

## Principles

- **Native first.** Every capability goes through `powerbi-client`, `powerbi-models`,
  `powerbi-report-authoring` or the Power BI REST API. Nothing is scripted inside Power BI's frame
  and no undocumented call is used.
- **The signed-in person's identity.** Tokens come from the sign-in KratosForge already has
  (Microsoft's Fabric CLI public client, Windows sign-in), so there is no app registration.
- **No token is stored.** A token lives in memory for the session. It is never written to a file,
  a log, a saved configuration, an export or generated code.
- **Never offline, never CU-free.** Power BI renders the content in Microsoft's service and its
  queries run on the content's capacity, exactly as when it is opened in Fabric.

## Pieces

| Piece | Where | What it does |
| --- | --- | --- |
| Token and discovery calls | Engine (`sidecar`) | A Power BI token from the existing sign-in, with its expiry; embed URLs, pages and tiles from the REST API. |
| Embed Studio screen | Main window (`app/ui`) | Holds the token in memory, sends it to the host frame, fetches a fresh one before it expires (at least 10 minutes are left on each one the engine hands out) rather than keeping one. |
| Embed host frame | New local scheme `pbiembed` (`app/src-tauri`) | One page plus the vendored SDK, with its own Content Security Policy. Takes a fixed list of commands from the main window and returns events with tokens removed. |
| Power BI frame | `app.powerbi.com` | Microsoft's renderer. KratosForge only talks to it through the SDK. |

### Why a separate host frame

The main window's policy allows frames only from the app's own notebook-output scheme
(`frame-src nbout:`). The SDK needs a page that frames `app.powerbi.com`. Adding Microsoft's host to
the main window's policy would let any frame in the app load it. Instead the host page is served by a
second local scheme, like `nbout`, and only to the main window. The main window's policy gains only
that local scheme. The host page's own policy is:

```
default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:;
frame-src https://app.powerbi.com; connect-src 'none'; base-uri 'none'; form-action 'none';
frame-ancestors <the app's own origins>
```

The host policy also matters because of how the SDK talks to its frame. `powerbi-client` 2.25.0 sends
its messages to the Power BI frame, including the load request that carries the access token, with a
target origin of `*`, and it does not check the origin of messages it receives. With `frame-src`
limited to `https://app.powerbi.com`, the frame can't be navigated anywhere else, so the token can
only reach Power BI. The host page also refuses an embed URL whose host isn't `app.powerbi.com`
(the SDK's own check accepts any `*.powerbi.com` or `*.fabric.microsoft.com` host).

Checked so far: the SDK embeds under that policy in Chromium with no violations. It sizes its frame
through `element.style`, which `style-src 'self'` allows; only `preload()` sets a `style` attribute,
and Embed Studio doesn't call it. What still needs the WebView2 check (PBE-01): framing from a local
scheme, whether `frame-ancestors` with the app's own origins is honoured on a custom-scheme page,
Power BI's storage inside that frame, pop-ups and downloads.

### Pop-ups and downloads

The main window has no new-window handler today. Power BI opens report hyperlinks and some actions as
pop-ups, so the main window gets the Fabric view's rule: `https` links open in the default browser,
everything else is refused, and WebView2's own unfiltered window is never used. Exports KratosForge
starts go through the existing save dialog.

### Tokens

- **Organization embedding (user owns data)**: an Entra token for
  `https://analysis.windows.net/powerbi/api`, `tokenType: Aad`. The person needs the same access and
  licence as in Power BI: a Pro or Premium Per User licence, or content on F64 or larger capacity for
  viewers with a free licence.
- **Customer embedding (app owns data)**: embed tokens come from the developer's own trusted backend
  (`GenerateToken`). KratosForge never stores a service principal secret. It generates the broker code
  and lets a developer paste an embed token for one session.

### Saved configurations

Saved embedding configurations, presets and test scenarios are JSON files under
`%LOCALAPPDATA%\FabricDesktop\embed\`, written with `jsonfile.py`. The schema refuses keys that hold
tokens or secrets (`accessToken`, `token`, `clientSecret` and similar), on save and on import.
A captured bookmark state can hold filter values, so it stays on this PC like any other local data.

## Capability profiles

What Microsoft's SDK supports for each content type. "Embed Studio" is the planned phase.

| Content | Embed | Interact | Not supported or limited | Embed Studio |
| --- | --- | --- | --- | --- |
| Report | `type: "report"` | Pages, filters (report, page, visual), slicers, bookmarks, visuals, events, settings, themes, export data, edit and save | Export data follows the tenant setting and the person's permission | Phase 1 view, Phase 3 interaction |
| Single visual | `type: "visual"` with `pageName`, `visualName` | Visual-level filters, events | Report-level actions such as page changes | Phase 2 |
| Dashboard | `type: "dashboard"` | `tileClicked` event | No filters or bookmarks | Phase 2 |
| Dashboard tile | `type: "tile"` with `dashboardId` | `tileClicked`, `tileLoaded` | Tile only, no dashboard context | Phase 2 |
| Paginated report | `type: "report"` with the paginated report's embed URL | `parameterValues`, parameter panel settings | No bootstrap, no multi-value parameters, no `rdl:` parameters, no real-time (push) semantic models | Phase 2 |
| Q&A | Retired | – | Microsoft retires Q&A, embedded included, by the end of December 2026, as announced in the Microsoft 365 message center (MC1218421, visible to tenant admins) | Not built |
| New report | `type: "create"` with a semantic model | Authoring with `powerbi-report-authoring` | Saves to Fabric | Phase 6, development copy first |
| Semantic model | Not embedded | – | – | Links to the existing model tools (data dictionary, Direct Lake check, BPA) |

## Measurements

Embed Studio measures what the client sees: time to `loaded`, time to `rendered`, per-visual
`visualRendered` timings and action durations. These are not Fabric capacity units and are never
labelled as such. Query cost stays in Fabric's capacity metrics.

## Tests

Browser tests use a mock Power BI frame that speaks the SDK's message protocol, so they need no
tenant and no capacity. Tests that load real content are marked live, need a confirm, and don't run
in CI.

## Sources

- [Power BI embedded analytics](https://learn.microsoft.com/power-bi/developer/embedded/embedded-analytics-power-bi)
- [Power BI JavaScript API](https://learn.microsoft.com/javascript/api/overview/powerbi/)
- [Embed a paginated report](https://learn.microsoft.com/javascript/api/overview/powerbi/embed-paginated-report)
- Microsoft 365 message center MC1218421, Retirement of Power BI Q&A
- `powerbi-client` 2.25.0 and `powerbi-report-authoring` 3.0.0 (MIT) on npm
