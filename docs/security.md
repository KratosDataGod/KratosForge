# Security model

How Fabric Desktop by KratosBI protects your sign-in, your data and your PC, what it trusts, and what is still open. This is the threat model for FD-102; the [enterprise backlog](https://github.com/KratosDataGod/fabric-desktop/blob/main/docs/product/enterprise-backlog.md) and the [hardening backlog](hardening/backlog.md) track the open items. It describes `main` as of 2026-10-18 (a346145); the [hardening audit](hardening/README.md) checked it at 1248ccd and its fixes are reflected here. The [security evidence pack](evidence-pack.md) collects this page with the rest of what a reviewer needs.

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

1. **Webview to shell.** The UI calls a fixed list of Rust commands (`rpc`, `update_app`, `open_link`, ...). Each validates its input: build commits are 40 hex characters, links are https or mailto, docs pages use a character allowlist.
2. **Shell to engine.** One Python process started by the shell, talking JSON lines over its own stdin and stdout. Only methods in `server.METHODS` can be called. Notebook output can't forge a reply, because the engine moves Python's stdout away from the pipe.
3. **External AI tools to engine.** Only when a person turns on **Settings > Integrations**: an MCP server per tool (`fabric-desktop mcp --client <id>`) and, if switched on, a local HTTP API on `127.0.0.1` (port 47615 by default). The HTTP API answers only loopback addresses, requires a `Host` naming the loopback address and port (stopping DNS rebinding), refuses any request carrying an `Origin` (so no web page can call it), caps bodies at 1 MiB and rate-limits each tool. Each tool has a token stored only as its SHA-256 hash. Running code always asks the person first. The full model is in [AI integration security](product/ai-integration-security.md).
4. **Engine to Microsoft.** HTTPS to Fabric, OneLake and Entra ID with your delegated token. The agent uses a service principal or managed identity instead.
5. **Shell to GitHub.** Updates come from this repository's `stable` and `latest` releases.
6. **Notebook code.** Code in a notebook runs as you, with your tokens, by design: it is your code. A notebook from someone else is code from someone else. Treat it like a script you were sent. It is **not sandboxed**: it can read your files, including the token cache. The same goes for Python an external AI tool runs after you approve it.

## Threats and what stops them

| Threat | Mitigation | State |
| --- | --- | --- |
| Script injected into the UI (from a table value, file name or notebook Markdown) calls engine methods | Every value is put in the page as text (`h()`, `esc()` with quotes escaped since PR #51); notebook HTML output runs in `sandbox="allow-scripts"` frames with no same-origin access | In place. A Content Security Policy as a second layer is open (FD-102b) |
| Another program on the PC calls the engine | The pipe to the shell can't be reached from outside. The integrations API (off by default) needs a tool token, answers loopback only and refuses browser requests | In place. A program running as you can still read the token cache (below) |
| A web page calls the local API | `Origin` refused, `Host` checked against loopback names | In place |
| A tampered or mixed-up engine download | The engine zip's SHA-256 is checked against the release's `version.json` before it is unpacked, and the unpacked engine must be the build the release names | In place from FD-102a |
| A tampered or mixed-up app update | The exe's SHA-256 is checked against `version.json` and the exe must carry its build commit | In place from FD-102a; a missing checksum is refused for both (HD-008, #158) |
| A Stable PC getting an untested engine | The engine comes from the Stable release | In place: Stable falls back to another channel only when its release has no engine, never on a network error (HD-008, #158) |
| Someone who can publish releases replaces every file | Checksums in the same release don't stop this. Code signing would (waiting on the Trusted Signing account); build provenance attestations are produced but nothing checks them yet | **Open** (SH-2, HD-021). Until signing exists, releases publish unsigned without failing |
| A `curl.exe` or `tar.exe` saved next to the app runs instead of Windows' own | Windows tools run by full path from `%SystemRoot%\System32` | In place from FD-102a |
| A spoofed Fabric response or a proxy steers the token to another site | A next-page or operation URL from Fabric is followed only over https on the same host or a Fabric, OneLake or Power BI host; anything else is refused before a request is sent | In place (HD-058) |
| Tokens copied from the PC | Token cache is per user; written atomically; with Windows sign-in the broker holds the primary token | **Partly open**: the cache file isn't encrypted on any OS (FD-131b, decision D-C2), so any program or notebook running as you, including Python an AI tool runs, can read it (EN-2, EN-10) |
| Tokens or personal data in logs, error messages or Report a problem | `redact.py` removes tokens, keys and passwords from every error the UI shows, the audit log and agent logs; Report a problem and support bundles also remove emails, user names in paths and ids (FD-131a, FD-138) | In place, including agent log fields (HD-007, #157) |
| Silent overwrites of OneLake data | Writes are version-checked; Save as table and CTAS refuse to replace a table unless asked (PR #51, #54); proofs write to sandboxes | In place. A retry after a post-commit failure no longer writes rows twice (HD-001, #138); `notebookutils.fs` deletes need `recurse=True`, refuse managed `Tables/` and honour the external-run write guard (HD-003, #154) |
| Someone on the PC loosens the organization's policy file | The file lives in `%ProgramData%`, which only administrators can change when IT pushes it ([Enterprise install](enterprise-install.md)). **Settings > Help and about** warns when the person using the PC can change it | Partly in place. The policy file stops mistakes, not someone set on getting round it: the engine is installed in the person's own profile, so they could change its code. To enforce which tenants people sign in to, use Microsoft Entra tenant restrictions or Conditional Access |
| Tests writing to production | The [live test](https://github.com/KratosDataGod/fabric-desktop/blob/main/docs/live-testing.md) refuses unmarked workspaces, production-looking names and Platform workspaces | In place from FD-101 |
| Secrets in project or agent files | Config validators refuse secret-looking fields; secrets come from Key Vault or environment variables | In place |
| Command injection through schedules | Task Scheduler is called with argument lists, never a shell; XML values are escaped | In place |

## Supply chain

- Python packages are pinned exactly in `sidecar/constraints.txt` and CI installs with it; Rust builds use `Cargo.lock` with `--locked`.
- CI publishes SHA-256 checksums in `version.json`, which the app now enforces.
- In place (FD-103): `pip-audit` and `cargo audit` on every pull request, CycloneDX SBOMs for the shell and the engine, build provenance attestations on releases, third-party actions in `ci.yml` pinned by commit SHA, and signing that runs only for pushes to `main`.
- Not yet: `codex-review.yml` and `docs.yml` still pin actions by tag (SH-8); the Windows agent installer and `install-sidecar` don't install with the constraints (SH-9); nothing verifies the attestations on a PC.

## Reporting a vulnerability

Please don't open a public issue. Use GitHub's **Report a vulnerability** on the repository's Security tab, or contact KratosBI directly.
