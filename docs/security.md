# Security model

How Fabric Desktop by KratosBI protects your sign-in, your data and your PC, what it trusts, and what is still open. This is the threat model for FD-102; the [enterprise backlog](https://github.com/KratosDataGod/fabric-desktop/blob/main/docs/product/enterprise-backlog.md) and the [hardening backlog](hardening/backlog.md) track the open items. It describes `main` as of 2026-10-17 (checked against commit 1248ccd by the [hardening audit](hardening/README.md)).

## What it protects

| Asset | Where it lives | Why it matters |
| --- | --- | --- |
| Your Microsoft sign-in (refresh and access tokens for Fabric, OneLake, Power BI, Graph) | `%LOCALAPPDATA%\FabricDesktop\msal_cache.bin`; with Windows sign-in, the Windows broker holds the primary token | Anyone holding them can act as you in Fabric |
| The Fabric view's sign-in cookies | `%LOCALAPPDATA%\FabricDesktop`, encrypted with Windows DPAPI | Same as above, for Fabric's web pages |
| Data in OneLake | Your tenant | Writes, overwrites and deletes are hard to undo for other people |
| Data on the PC | Previews, proof sandboxes, notebook outputs, local tables under `%LOCALAPPDATA%\FabricDesktop` and project folders | May hold personal or confidential data |
| The app and its engine | `fabric-desktop.exe` wherever you saved it; engine under `%LOCALAPPDATA%\FabricDesktop\engine\<build>` | Code that runs as you |
| The headless agent's identity | A certificate path or managed identity named in `agent.toml`; secrets only in environment variables | Acts as a service principal in Fabric |

## Pieces and trust boundaries

```
 Webview (app/ui, HTML + JS)  --Tauri invoke-->  Shell (Rust, main.rs)  --stdin/stdout JSON lines-->  Engine (Python sidecar)
                                                    |                                                     |
                                       GitHub releases (updates)                           Fabric REST, OneLake, Entra ID
```

1. **Webview to shell.** The UI calls a fixed list of Rust commands (`rpc`, `update_app`, `open_link`, ...) under a [Content Security Policy](#content-security-policy). Each validates its input: build commits are 40 hex characters, links are https or mailto, docs pages use a character allowlist.
2. **Shell to engine.** One Python process started by the shell, talking JSON lines over its own stdin and stdout. Only methods in `server.METHODS` can be called. Notebook output can't forge a reply, because the engine moves Python's stdout away from the pipe.
3. **External AI tools to engine.** Only when a person turns on **Settings > Integrations**: an MCP server per tool (`fabric-desktop mcp --client <id>`) and, if switched on, a local HTTP API on `127.0.0.1` (port 47615 by default). The HTTP API answers only loopback addresses, requires a `Host` naming the loopback address and port (stopping DNS rebinding), refuses any request carrying an `Origin` (so no web page can call it), caps bodies at 1 MiB and rate-limits each tool. Each tool has a token stored only as its SHA-256 hash. Running code always asks the person first. The full model is in [AI integration security](product/ai-integration-security.md).
4. **Engine to Microsoft.** HTTPS to Fabric, OneLake and Entra ID with your delegated token. The agent uses a service principal or managed identity instead.
5. **Shell to GitHub.** Updates come from this repository's `stable` and `latest` releases.
6. **Notebook code.** Code in a notebook runs as you, with your tokens, by design: it is your code. A notebook from someone else is code from someone else. Treat it like a script you were sent. It is **not sandboxed**: it can read your files, including the token cache. The same goes for Python an external AI tool runs after you approve it.

## Threats and what stops them

| Threat | Mitigation | State |
| --- | --- | --- |
| Script injected into the UI (from a table value, file name or notebook Markdown) calls engine methods | Every value is put in the page as text (`h()`, `esc()` with quotes escaped since PR #51). A Content Security Policy is the second layer: only the app's own script files run, so injected markup can't run script (HD-020, [below](#content-security-policy)) | In place. The Tauri API is still a page global (`withGlobalTauri`), so a script that did run could call it |
| Notebook HTML output (pandas, Plotly, `%%html`) reaches the app or sends data out | It runs in a sandboxed frame on its own origin (`nbout:`) with its own policy that allows no fetches, no remote images, scripts or fonts, and it can't navigate itself to another site (HD-020). A content security policy doesn't cover WebRTC, so a notebook written to leak its own output could still send it out that way | Mostly in place |
| Another program on the PC calls the engine | The pipe to the shell can't be reached from outside. The integrations API (off by default) needs a tool token, answers loopback only and refuses browser requests | In place. A program running as you can still read the token cache (below) |
| A web page calls the local API | `Origin` refused, `Host` checked against loopback names | In place |
| A tampered or mixed-up engine download | The engine zip's SHA-256 is checked against the release's `version.json` before it is unpacked, and the unpacked engine must be the build the release names | In place from FD-102a |
| A tampered or mixed-up app update | The exe's SHA-256 is checked against `version.json` and the exe must carry its build commit | In place from FD-102a. A `version.json` with no checksum is still accepted for the exe (SH-3, HD-008); the engine refuses one after HD-008 |
| A Stable PC getting an untested engine | The engine comes from the Stable release | **Open** until HD-008: any network error on Stable falls back to the Preview engine |
| Someone who can publish releases replaces every file | Checksums in the same release don't stop this. Code signing would (waiting on the Trusted Signing account); build provenance attestations are produced but nothing checks them yet | **Open** (SH-2, HD-021). Until signing exists, releases publish unsigned without failing |
| A `curl.exe` or `tar.exe` saved next to the app runs instead of Windows' own | Windows tools run by full path from `%SystemRoot%\System32` | In place from FD-102a |
| Tokens copied from the PC | Token cache is per user; written atomically; with Windows sign-in the broker holds the primary token | **Partly open**: the cache file isn't encrypted on any OS (FD-131b, decision D-C2), so any program or notebook running as you, including Python an AI tool runs, can read it (EN-2, EN-10) |
| Tokens or personal data in logs, error messages or Report a problem | `redact.py` removes tokens, keys and passwords from every error the UI shows, the audit log and agent logs; Report a problem and support bundles also remove emails, user names in paths and ids (FD-131a, FD-138) | In place. The agent's JSON log missed secrets in quoted text until HD-007 (EN-5) |
| Silent overwrites of OneLake data | Writes are version-checked; Save as table and CTAS refuse to replace a table unless asked (PR #51, #54); proofs write to sandboxes | In place. Open until fixed: a failure after the commit can write rows twice (DI-1, HD-001); `notebookutils.fs.rm` deletes OneLake folders recursively (DI-3, HD-003) |
| Tests writing to production | The [live test](https://github.com/KratosDataGod/fabric-desktop/blob/main/docs/live-testing.md) refuses unmarked workspaces, production-looking names and Platform workspaces | In place from FD-101 |
| Secrets in project or agent files | Config validators refuse secret-looking fields; secrets come from Key Vault or environment variables | In place |
| Command injection through schedules | Task Scheduler is called with argument lists, never a shell; XML values are escaped | In place |

## Content Security Policy

The main window's policy is set in `app/src-tauri/tauri.conf.json` (`app.security.csp`):

```
default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self' data:;
connect-src ipc: http://ipc.localhost; frame-src nbout: http://nbout.localhost https://nbout.localhost;
object-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors 'none'
```

- **Scripts:** only the files in `app/ui/`. No inline scripts or handlers, no `eval`. A UI test fails if the policy is missing, allows `'unsafe-eval'`, or allows inline or remote scripts.
- **Styles:** `styles.css`, plus the code editor's `<style>` element, which carries the nonce Tauri adds to `style-src` on each load. Inline style attributes aren't allowed; the UI sets styles through the DOM (`element.style`), which the policy doesn't restrict. No `'unsafe-inline'`.
- **Images:** the app's own files and `data:` (notebook charts). Nothing is loaded from the internet.
- **Connections:** only the shell (Tauri's IPC). The UI never calls the engine's local HTTP API or any web address; the engine makes every network call.
- **Frames:** only notebook HTML output. The Fabric view (Monitoring hub, OneLake catalog, Fabric's sign-in) is a separate webview, not a frame, so this policy doesn't apply to it; app sign-in runs in the engine and the system browser.

Notebook HTML output comes from whoever wrote the notebook, so it never runs in the app's origin. The shell serves a small frame page from its `nbout:` scheme (`http://nbout.localhost` on Windows), for the main window only. The page loads it in an `<iframe sandbox="allow-scripts">` without `allow-same-origin`, posts it the HTML, and the frame writes the HTML into itself. The frame keeps its own policy: inline scripts and styles (and `eval`, which some charting libraries use) are allowed, because the output needs them and can reach nothing; images, fonts and media only as `data:` or `blob:`; `default-src 'none'`, so no fetches, beacons or remote resources. A content security policy doesn't cover WebRTC, so script in a notebook written to leak data could still send it out through a WebRTC connection; treat a notebook from someone you don't trust like a script you were sent. Its origin is opaque and it never receives the shell's invoke key, so it can't call the Tauri API, and the main window's `frame-src` stops it from navigating itself to another site. HTML output that loads its scripts from the internet (Plotly with `include_plotlyjs="cdn"`, for example) shows nothing; the engine's own Plotly output inlines plotly.js.

## Supply chain

- Python packages are pinned exactly in `sidecar/constraints.txt` and CI installs with it; Rust builds use `Cargo.lock` with `--locked`.
- CI publishes SHA-256 checksums in `version.json`, which the app now enforces.
- In place (FD-103): `pip-audit` and `cargo audit` on every pull request, CycloneDX SBOMs for the shell and the engine, build provenance attestations on releases, third-party actions in `ci.yml` pinned by commit SHA, and signing that runs only for pushes to `main`.
- Not yet: `codex-review.yml` and `docs.yml` still pin actions by tag (SH-8); the Windows agent installer and `install-sidecar` don't install with the constraints (SH-9); nothing verifies the attestations on a PC.

## Reporting a vulnerability

Please don't open a public issue. Use GitHub's **Report a vulnerability** on the repository's Security tab, or contact KratosBI directly.
