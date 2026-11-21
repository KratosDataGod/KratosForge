# Mobile companion: evaluation and plan

Status: **Proposed** (evaluation only, 2026-11-21). Nothing here is built. Building any of it, adding
cloud infrastructure, or paying for app store accounts needs Chris's typed go after he reviews this.

Companion files:
[decisions (ADRs)](mobile-companion-decisions.md) ·
[UX specification](mobile-companion-ux.md) ·
[security design](mobile-companion-security.md) ·
[stories](mobile-companion-backlog.md)

Core principle: **the PC does the work; the phone watches and steers it.**

## Executive summary

- **Feasible, and most of the hard parts already exist.** KratosForge already has a pairing flow
  with a short confirmation code (VS Code, `editor_pairing.py`), a capability registry with
  permission categories, workspace scope, single-use approvals and a hash-chained audit trail
  (`integrations/`), cancel paths for most run types, and IT policy keys with Group Policy.
  A phone becomes one more kind of connected client behind `Registry.invoke`, not a second
  control surface.
- **Recommended architecture: hybrid, in two steps.** One end-to-end encrypted channel
  (a Noise handshake, keys made at pairing) carried either directly over the local network or
  through a blind relay that only forwards encrypted bytes. Step 1 (MVP) ships direct only, which
  costs nothing to run. Step 2 adds the relay for "from anywhere". Because the channel is
  encrypted end to end, the relay is never trusted with commands or data.
- **Simplest secure MVP:** monitor runs and the PC's status, get notified when runs end, and start
  or cancel only the saved items the person marked "Allow from phone" on the PC. No code, SQL or
  paths are ever sent from the phone. No Prod, no deploys, no credential changes.
- **Recommended app technology:** React Native with Expo. It scored highest on secure key storage,
  QR scanning and push notifications, which are the phone-side hard parts. A PWA was ruled out as
  the main client because a page served over HTTPS can't reach a PC on the local network without a
  publicly trusted certificate on that PC.
- **Ongoing cost:** $0 for the MVP (direct connection; push notifications through Expo's free push
  service). Remote access costs depend on who runs the relay: $0 to KratosForge if each
  organization runs it (an open-source container), or roughly $10 to $60 a month at 100 to 1,000
  users and $1,500 to $4,500 a month at 100,000 users if KratosForge runs it (section 9). App store
  fees are $99 a year (Apple) and $25 once (Google).
- **Effort:** about 2 to 3 weeks for the proof of concept and about 10 to 14 weeks to a remote-capable
  MVP, at the project's current pace with review gates (assumptions in section 12).

**Decision Chris needs to make:** approve the P0 proof of concept (MC-001 to MC-008, direct
connection, Android or iPhone through Expo Go, no cloud resources, no cost), and choose who runs
the relay for P2: nobody (local network and your own VPN only), each organization (recommended),
or KratosForge.

## Contents

1. [Current-state assessment (A)](#1-current-state-assessment-a)
2. [Connectivity options and decision matrix](#2-connectivity-options-and-decision-matrix)
3. [Mobile technology evaluation](#3-mobile-technology-evaluation)
4. [Capability classification](#4-capability-classification)
5. [Desktop companion service](#5-desktop-companion-service)
6. [Central platform integration](#6-central-platform-integration)
7. [Desktop lifecycle and reliability](#7-desktop-lifecycle-and-reliability)
8. [Notifications](#8-notifications)
9. [Cost analysis](#9-cost-analysis)
10. [Proof of concept plan](#10-proof-of-concept-plan)
11. [Delivery roadmap (F)](#11-delivery-roadmap-f)
12. [Implementation recommendation (G)](#12-implementation-recommendation-g)

## 1. Current-state assessment (A)

Reviewed at `main` ea988e9 (2026-11-21), plus ai-commons `AGENTS.md`, active standards, the
KratosForge context, status and mission prompt. Classification: **Implemented** (works today),
**Partial** (exists but needs changes for a phone), **Missing**, **Unknown** (needs a test to know).

### 1.1 What can be reused

| Area | Finding | Status | Evidence |
| --- | --- | --- | --- |
| Pairing with a confirmation code | A client asks to pair, the app shows a 6-character code the client also shows, the person approves or denies; requests expire after 120 s, at most 3 wait; token stored only as SHA-256; lifetime session, 8h or 7d; ended from Settings > Integrations | Implemented (for VS Code, loopback only) | `sidecar/fabric_desktop/editor_pairing.py`, `editor_service.py`, `docs/vscode.md` |
| Local listener hardening | Loopback only, same-Windows-user check, `Host` check, no browser `Origin`, 1 MiB body cap, 16 connections, slow-body timeout, long-poll up to 25 s, API version handshake | Implemented (loopback) | `editor_channel.py`, `integrations/http_server.py`, `integrations/peer.py` |
| Capability registry | Every external action is a `Capability` with JSON Schemas, one permission category, approval rule and handler; `invoke()` is the only way in, used by MCP stdio, MCP over HTTP and REST | Implemented | `integrations/registry.py`, `capabilities.py` (57 capabilities) |
| Permission categories | `READ_METADATA`, `READ_DATA`, `WRITE_LOCAL_FILES`, `EXECUTE_LOCAL_CODE`, `WRITE_ONELAKE`, `MODIFY_FABRIC_ARTIFACTS`, `DEPLOY_TO_FABRIC`, `MANAGE_CONNECTIONS`, granted per client | Implemented | `integrations/registry.py` `PERMISSIONS`, `permissions.py` |
| Single-use approvals bound to arguments | An approval is bound to a hash of the exact arguments and used once | Implemented (approved on the PC only) | `integrations/permissions.py`, `store.py` |
| Workspace scope | Each capability declares how it is confined; a limited client can't leave its workspaces | Implemented | `integrations/scope.py` |
| Audit trail | One line per external call, SHA-256 chained, monthly JSONL, verifiable; app audit end anchored in Windows Event Log | Implemented | `integrations/audit.py`, `audit_chain.py`, `audit_anchor.py` |
| Executions with ids and states | `ex_` ids; queued, running, succeeded, failed, cancelled, timed_out; logs and capped results | Implemented (for external tool runs) | `integrations/executions.py` |
| Organization policy | `integrations`, `integrations_max_permissions`, `integrations_http`, `integrations_editors`; fail closed; Group Policy / ADMX | Implemented | `integrations/policy.py`, `app_settings.py` `INTEGRATION_KEYS`, `LOOSENING` |
| Secret removal | `redact.py` / `secret_redaction.py` remove tokens, keys, webhook addresses from text | Implemented | `redact.py`, `app/tests/redact-cases.json` |
| Run-now of saved schedules | Windows Task Scheduler runs schedules even with the app closed (while signed in); `run_now(schedule_id)` | Implemented | `local_schedules.py` |
| Cancel paths | SQL runs, local queries, notebook cells (interrupt), notebook batch, pipelines, proofs, side-by-side, Git ops, Fabric jobs | Implemented per run type | `service.py sql_cancel`, `localdata_service.py`, `service.py interrupt_cell`, `notebook_batch_service.py`, `pipeline_service.py`, `prove_service.py`, `cloud_service.py cancel_fabric_run` |
| Inventory of what runs where | Workload records built from existing run logs and Fabric items | Partial (records, not a live job feed) | `workloads/inventory.py`, `workload_service.py` |
| Run history | Job history file, metadata run log, schedule runs, Fabric runs | Partial (several stores, no single job model) | `service.py history_file`, `meta_service.meta_runs`, `schedule_runs.jsonl`, `fabric_runs.jsonl` |
| Alert delivery | Teams / flow / mail outbox, quiet hours, digest, at-most-once for unconfirmed posts | Implemented (not phones) | `workloads/notify.py`, PR #3032 |
| Control plane | Installation identity and environment manifest over the existing Platform DB and central lakehouse | Implemented (engine), live run pending | `controlplane/`, `docs/product/central-control-plane-*.md` |
| MCP server | Read-only tools over the same registry | Implemented (Preview until live check) | `integrations/mcp_server.py`, #2363 |
| Device-key crypto | `cryptography>=42` is already a direct dependency (X25519, Ed25519, ChaCha20-Poly1305, HKDF) | Implemented (library) | `sidecar/pyproject.toml` |

### 1.2 What is missing or blocks a phone

| Area | Finding | Status | Evidence |
| --- | --- | --- | --- |
| Any listener off the PC | Every listener today is `127.0.0.1` only, and `docs/security.md` promises loopback. A phone is the first inbound client from another device | Missing (by design) | `docs/security.md` trust boundaries 3 to 5 |
| Unified live job model | No one list of "what is running now" across SQL runs, notebook cells, metadata runs, pipelines, schedules and Fabric jobs. The UI polls each page's own calls | Missing | `app/ui/activity.js` (15 s poll of Fabric jobs), per-service status calls |
| Push events from the engine | The engine answers requests; it has no event bus or subscription. Long-poll exists only in the editor channel | Missing | `server.py` (one JSON reply per request), `editor_channel.py` `wait` |
| Cancel a metadata run | `meta_run` has no cancel or stop call | Missing | `meta_service.py` |
| Pause anything | No run type can be paused and resumed | Missing (and not planned) | no pause method in any service |
| Running in the background | No tray; closing the window ends the engine. Only Task Scheduler schedules run with the app closed | Missing | `app/src-tauri/src/main.rs` (no tray), `local_schedules.py` |
| Approvals away from the PC | Approvals can only be given in the desktop app | Partial | `integrations/permissions.py` |
| Hardware-bound device keys, mutual device authentication | Pairing gives a bearer token; nothing binds it to a device | Missing | `editor_pairing.py` (`kfp_` token) |
| Phone push notifications | No APNs/FCM/Web Push sender | Missing | none |
| A "safe to run from a phone" list | Nothing marks which saved items may run without the person at the PC | Missing | none |
| CPU / RAM metrics | Free memory is read for query limits; there is no metrics call | Partial | `free_memory.py`, `cpu_cap.py` |
| Product scope | `docs/ideas.md` lists "A phone or mobile app" as out of scope. Chris's master prompt (2026-11-21) asks for this evaluation; the line changes only if he approves building | Conflict to resolve | `docs/ideas.md` "What's out of scope" |
| Code signing | Identity validation still in progress; an unsigned exe that listens on the network triggers a Windows Firewall prompt naming an unknown publisher | Blocker for LAN polish | project status, `signature.rs` |
| Windows Firewall | An inbound listener needs an allow rule. A per-user install can't add one silently; an IT MSI can scope one to the Private profile | Unknown (to test on Chris's PC) | `app/installer/kratosforge.wxs` |

### 1.3 Owners to coordinate with

| Topic | Owner |
| --- | --- |
| Registry, permissions, MCP, local API | MCP server thread (#2363) |
| Installation identity, central stores | Control plane thread |
| Mockups for the phone screens and the Settings > Mobile companion page | UX thread |
| Threat model and every inbound-surface PR | Security QC |
| Notices and alert delivery | Governance, Workload mgmt |
| Windows install, firewall rule, tray | Windows hardening thread |
| Merges | Release management, after Quality review |

## 2. Connectivity options and decision matrix

All four options use the same pairing and the same end-to-end channel (ADR MC-ADR-002), so they
differ only in how bytes travel. Details and diagrams are in the [decisions](mobile-companion-decisions.md).

### Option A: local network, direct

- **Discovery:** the QR code carries the PC's current private addresses and port; the phone keeps
  them and also browses mDNS (`_kratosforge._tcp`, advertising only an opaque id, never the PC or
  user name) when an address changes. iOS asks the person for Local Network permission once.
- **Transport:** a WebSocket on a port the engine picks, bound to explicit private-range interface
  addresses (RFC 1918, link-local, ULA) and only while Windows classifies that network as Private or
  Domain, never Public, since cafe and hotel Wi-Fi use private addresses too. Encryption is the Noise channel, so no
  certificate is needed and there is nothing for a CA to vouch for.
- **Firewall:** Windows asks to allow the program the first time it listens. An enterprise PC
  often blocks inbound connections by Group Policy, and guest or office Wi-Fi often isolates
  clients from each other. Direct then fails, and the app says so plainly.
- **IP changes:** handled by mDNS and the stored list; no reconfiguration by the person.
- **Sleep:** a sleeping PC drops off; the phone shows "PC is asleep or offline" (section 7).
- **Offline:** works with no internet at all, as long as both are on the same network.
- **Works over a VPN:** a corporate VPN, Tailscale or similar puts the phone on a private network
  with the PC, so direct also works "from anywhere" for people who already have one, at no cost to
  KratosForge.

### Option B: blind relay

- **Outbound only:** the PC opens one WebSocket over HTTPS (port 443) to the relay, through the
  Windows proxy as every other call does (`win_proxy.rs`). No inbound port, no firewall rule.
- **What the relay knows:** a routing id (a hash of the PC's public key), which paired-phone key
  hashes may reach it, connection times and byte counts. It never sees commands, results, names
  or tokens: it forwards Noise ciphertext.
- **Authorization:** the relay only decides who may *connect* (signed challenge from the PC's key;
  phone key hash on the PC's list). Every command is still authorized by the PC.
- **Cannot become remote execution:** it has no API that does anything except forward bytes
  between two authenticated sockets; it runs no code and stores nothing but rate-limit counters.
- **Latency:** one extra hop, typically 20 to 80 ms in-region; status updates are small.
- **Hosting:** a small open-source container (stateless, horizontally scaled by routing id). Who
  runs it is Chris's decision (section 9).

### Option C: peer-to-peer (WebRTC)

- Needs a signaling service anyway (to exchange offers), plus STUN, plus TURN for the 10 to 20%
  of connections where both ends sit behind symmetric NAT or UDP is blocked, which is common on
  corporate networks. TURN carries all traffic for those connections, so it costs about what a
  relay costs, and both services must be run.
- Adds a large native dependency (libwebrtc) to the phone and an ICE/DTLS stack to the engine.
- Rejected: more moving parts than B for the same reach, and the least predictable on enterprise
  networks.

### Option D: hybrid (recommended)

- Try direct first (stored addresses, then mDNS, 1.5 s budget), then the relay if the person or
  their organization turned it on. The phone shows which path is in use ("On your network" or
  "Through relay") only on the Devices screen; it never changes what is allowed.
- Same Noise channel, same keys, same permissions on both paths, so there is no weaker path to
  downgrade to. The phone never falls back to an unencrypted path, because none exists.
- The extra complexity is a transport selector and reconnect loop on the phone (about 300 lines),
  justified because each path alone fails a large group: direct fails most enterprise and
  away-from-home cases, relay-only fails with no internet and costs money for traffic that never
  needed to leave the room.

### Weighted decision matrix

Scores 1 (poor) to 5 (best). Weighted total out of 100.

| Criterion | Weight | A Direct | B Relay | C P2P | D Hybrid |
| --- | ---: | ---: | ---: | ---: | ---: |
| Security | 25 | 4 | 4 | 4 | 4 |
| User experience | 15 | 2 | 5 | 3 | 5 |
| Implementation effort (5 = least) | 10 | 4 | 3 | 2 | 2 |
| Operating cost (5 = least) | 15 | 5 | 2 | 3 | 3 |
| Performance | 5 | 5 | 4 | 5 | 5 |
| Enterprise compatibility | 15 | 2 | 4 | 1 | 4 |
| Offline support | 5 | 5 | 1 | 3 | 5 |
| Long-term maintainability | 10 | 4 | 3 | 2 | 3 |
| **Weighted total** | 100 | **73** | **70** | **57** | **76** |

Security is equal across options because the end-to-end channel and the PC-side checks are the
same; the transport is never trusted. **Recommendation: D, built as A first.** Fallback if the relay
proves too costly or too much to run: **A plus "bring your own private network"** (corporate VPN,
Tailscale, ZeroTier), documented, at $0.

## 3. Mobile technology evaluation

The desktop UI is plain HTML and JavaScript with no build step and no framework, so no option
reuses desktop UI components; what can be shared is the protocol, the brand tokens
(`app/ui/styles.css`), wording and test cases. Scores 1 to 5.

| Criterion | Weight | Expo (React Native) | Flutter | PWA | Tauri 2 mobile | Native Swift + Kotlin |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Reuse (stack, skills, code) | 20 | 2 | 1 | 4 | 5 | 1 |
| Secure, device-bound key storage | 15 | 5 | 4 | 2 | 3 | 5 |
| QR scanning | 5 | 5 | 4 | 3 | 4 | 5 |
| Push notifications with the app closed | 15 | 5 | 4 | 3 | 2 | 5 |
| Background connectivity | 10 | 3 | 3 | 1 | 2 | 5 |
| Development complexity (5 = simplest) | 10 | 3 | 2 | 5 | 3 | 1 |
| Testing | 10 | 4 | 4 | 4 | 3 | 3 |
| Store deployment | 5 | 5 | 4 | 5 | 4 | 4 |
| Long-term maintenance | 10 | 3 | 3 | 4 | 4 | 1 |
| **Weighted total** | 100 | **74** | **60** | **67** | **67** | **63** |

- **PWA:** cheapest to build, and Web Push lets the PC notify the phone with no server of our own.
  But a service worker needs a secure origin, and a page loaded over HTTPS may not open `ws://` to
  a LAN address (mixed content) or `wss://` to a PC with a self-signed certificate. So a PWA works
  only through a relay, never direct. iOS keeps Web Push to PWAs added to the home screen, and keys
  in WebCrypto are not hardware-bound. **Not the main client; possible later as a relay-only
  viewer.**
- **Tauri 2 mobile:** best reuse of the Rust toolchain. Official plugins cover QR scanning and
  biometrics; push notifications and hardware keystore access need community or custom plugins
  (Unknown maturity). Good second choice if the Expo spike fails.
- **Expo (React Native):** mature camera/QR (`expo-camera`), Keychain/Keystore storage
  (`expo-secure-store`), biometrics (`expo-local-authentication`) and push (`expo-notifications`
  with Expo's free push service or direct APNs/FCM). Expo Go runs the proof of concept on Chris's
  phone with no developer account and no store build. Hardware-bound signing keys (Secure Enclave,
  Android StrongBox) need a small native module in a development build, which is the MVP's
  release-blocking item MC-104.
- **Flutter / native:** strong platform access, but a new language with nothing shared.

**Recommendation: Expo (React Native) for the phone app. PoC in Expo Go; MVP as an Expo development
build.** A PWA is not used for the PoC because the PoC tests the direct path first.

## 4. Capability classification

Risk class: **R** read-only, **O** operational, **S** sensitive (section 9 of the brief). Feasibility
against today's code.

| Capability | Phase | Class | Feasibility | Risk | Value | Notes |
| --- | --- | --- | --- | --- | --- | --- |
| PC online/offline | 1 | R | High | Low | High | Phone-side: last contact; relay presence in P2 |
| Active runs (SQL, cells, notebook batch, pipelines, proofs, schedules, Fabric jobs) | 1 | R | Medium | Low | High | Needs the run feed MC-001 over existing status calls |
| Notebook progress | 1 | R | High | Low | High | Cell n of m from notebook batch / pipeline notebook status |
| Pipeline progress | 1 | R | High | Low | High | `pipeline_service` run status |
| SQL run status | 1 | R | High | Low | Medium | `sql_runs` status, no rows sent |
| Current engine / workload | 1 | R | High | Low | Medium | Engine name per run |
| Elapsed time; estimated finish | 1 | R | High / Low | Low | Medium | Estimate shown only when a previous run of the same item exists; else blank |
| CPU / RAM | 1 | R | Medium | Low | Medium | New metrics call (MC-102) |
| Errors and logs | 1 | R | High | Medium | High | Redacted, capped, never cell outputs or rows |
| Job history | 1 | R | Medium | Low | High | Same run feed, last 7 days |
| Notifications on finish / fail | 1 | R | Medium | Medium | High | MC-106; generic previews |
| Start a saved item | 2 | O | High | Medium | High | Only items marked "Allow from phone" on the PC (MC-103) |
| Cancel | 2 | O | High for SQL, cells, batch, pipelines, proofs, Fabric jobs; Missing for metadata runs | Low | High | Cancel shown only where the run type can cancel |
| Pause | 2 | O | Not possible | n/a | n/a | Never shown |
| Retry a failed run | 2 | O | Medium | Medium | Medium | Same saved item, same parameters, new run id |
| Run saved notebook / SQL / pipeline | 2 | O | High | Medium | High | As "start a saved item"; no code sent |
| Choose predefined parameters | 2 | O | Medium | Medium | Medium | Only from a list saved on the PC; no free text |
| Review results | 2 | R | Medium | Medium | Medium | Row counts and checks, not rows (rows need `READ_DATA`) |
| Optimization recommendations, savings | 2 | R | High | Low | Medium | Existing optimize / ESI reads; Measured vs Estimated kept apart |
| Lightweight SQL editor | 3 | S | Feasible | High | Low | Out of scope: sends code from the phone |
| Notebook code viewing | 3 | R | High | Medium | Medium | Read-only, `READ_METADATA`+ |
| Notebook editing | 3 | S | Feasible | High | Low | Out of scope |
| Job parameter editing | 3 | O | Medium | Medium | Medium | Only within limits saved on the PC |
| Deployment approvals | 3 | S | Low today | High | High | Needs the "Approval before a promote to Prod" feature and a typed-name rule on the phone; Chris decides |
| Git status | 3 | R | High | Low | Low | Read-only |
| Git push / pull | 3 | S | Feasible | Medium | Low | Out of scope |
| Semantic model validation results | 3 | R | High | Low | Medium | BPA / OSSIE results |
| Workspace / capacity monitoring | 3 | R | Medium | Low | Medium | Existing capacity reads |
| Academy progress, ranks, achievements | 3 | R | High | Low | Medium | Profile view; art only on rank/achievement surfaces |
| Several PCs | 3 | R/O | High | Low | High | Pairing is per PC; the phone keeps a list |
| Several users, team monitoring, shared history, approvals | 4 | R/S | Medium | Medium | Medium | Through the central platform; separate from personal control |
| Organization device policy | 4 | S | High | Low | High | Policy keys (MC-ADR-008) |

## 5. Desktop companion service

The companion service is **a transport, not a new service layer**: a third front door onto
`Registry.invoke`, beside MCP and the local HTTP API (MC-ADR-004).

```
phone ──Noise──> companion listener / relay link ─> CompanionChannel ─> Registry.invoke ─> existing services
                                                    (device -> client)   (permissions, scope,
                                                                          approvals, audit)
```

Conceptual operations, each one registry capability with an input schema:

| Operation | Capability (new unless noted) | Permission | Class |
| --- | --- | --- | --- |
| Status | `fabric_desktop_get_status` (exists) + `companion_get_pc_status` | `READ_METADATA` | R |
| List runs | `companion_list_runs` (run feed) | `READ_METADATA` | R |
| Run detail and log tail | `companion_get_run` | `READ_METADATA` | R |
| Follow runs | `companion_watch` (long-poll, `since` cursor, up to 25 s) | `READ_METADATA` | R |
| Metrics | `companion_get_metrics` | `READ_METADATA` | R |
| List runnable items | `companion_list_runnable` | `READ_METADATA` | R |
| Start a saved item | `companion_start_saved` `{item_id, preset_id?, request_id}` | new `RUN_SAVED_ITEMS` | O |
| Cancel a run | `companion_cancel_run` `{run_id, request_id}` | `RUN_SAVED_ITEMS` | O |
| Retry | `companion_retry_run` `{run_id, request_id}` | `RUN_SAVED_ITEMS` | O |
| Devices | managed on the PC only; the phone can only end its own pairing | n/a | n/a |

Rules carried by every call:

- **Allowlisted:** only capabilities tagged `companion=True` are reachable on this channel; the
  rest of the registry is invisible to a phone.
- **Schema-validated:** the registry already validates input; companion schemas take ids only
  (pattern `^[A-Za-z0-9_\-]{1,100}$`), never code, SQL, paths or URLs.
- **Scoped:** the device is a registry client with its own grants and optional workspace list.
- **Rate limited:** per device, 10 operational calls a minute and 120 reads a minute; excess
  answered `rate_limited` with a retry time.
- **Audited, failing closed:** every call through the existing audit trail with `channel = "companion"`,
  device id and network path; an operational command whose audit line can't be written is refused.
- **Presence proven to the PC:** each operational call carries a signature by the phone's
  presence-required operational key over (session hash, `request_id`, `item_id`, `preset_id`).
- **Idempotent:** every operational call carries a `request_id` (UUIDv4). The PC keeps a
  `request_id -> result` table for 24 h; a repeat returns the first answer and starts nothing.
- **Acknowledged:** a start returns `accepted` with the run id at once; progress comes from the
  run feed. A start whose answer the phone never saw is resolved by asking again with the same
  `request_id`.
- **Reliable ids:** the run feed gives each run `kind:native_id` (for example `sql:e_...`,
  `batch:...`, `pipeline:...`, `fabric:<job id>`), stable across reconnects.
- **No shell, no files:** no capability takes a path or a command; the phone can't name a file.
- **Errors:** the registry's `IntegrationError` codes, with one plain sentence and the next step.
- **Existing guards apply:** `_job_guard` still refuses job runs into Test or Prod; Prod writes
  still need the name typed on the PC; nothing from the phone can satisfy a typed-name confirm.

## 6. Central platform integration

| Data | Where it lives | Why |
| --- | --- | --- |
| Paired devices and their public keys | On the PC: the integrations store (`integrations.sqlite`, new columns) | Trust is between one phone and one PC; needs no cloud and no Fabric capacity |
| Pairing secrets, session keys | PC memory only | Never written down |
| Idempotency table, rate counters | PC, integrations store | Low latency, transactional |
| Companion audit lines | PC, the existing audit trail | Already chained and verifiable |
| Notification history | PC, 30 days, titles only | Shown on the phone's Home |
| Phone-side cache | Phone, encrypted store, last 7 days of run summaries | Offline viewing |
| Execution history and savings (optional, P4) | Central lakehouse families already written by optimize / ESI sync | Analytical history only, through the existing sync |
| Organization device policy (P4) | `policy.json` / Group Policy (existing), optionally published through the control plane's policy package | No new store |
| Installations | Control plane `installations` row (exists) | A team view in P4 can list PCs that allow phones, from data already there |

The lakehouse is never a command queue or a device store. The operational Platform database
(`SQL_Platform`) is not needed for personal use and is used in P4 only for organization-level
views that already belong there. **Mandatory for connectivity:** the PC and the phone (plus a
relay for remote use). **Optional:** everything central. **No Fabric capacity is needed to connect
a phone.**

## 7. Desktop lifecycle and reliability

| Scenario | Supported without new background service? | Behaviour |
| --- | --- | --- |
| App open and active | Yes | Full |
| App minimized | Yes | Full; the engine keeps running |
| PC locked | Yes | Full; the app keeps running under the locked session |
| Window closed | No (today the engine stops) | Phone shows "KratosForge is closed on CHRIS-PC". P2 story MC-206 adds opt-in "Keep running in the notification area while phones are paired" (mockup first) |
| User signs out | No | Offline. Signed-out schedules still run through Task Scheduler; the phone sees their results at the next connection |
| PC asleep | No | Offline. The phone never promises a start on a sleeping PC. Optional P3 "Keep this PC awake while a run started from the phone is running" (`SetThreadExecutionState`), off by default. Wake-on-LAN is out of scope |
| PC restarts | No | Offline until KratosForge starts again; pairings persist (keys on disk, DPAPI-protected) |
| Network drops | Yes | Phone reconnects with backoff (1, 2, 4 ... 30 s); runs on the PC are unaffected |
| PC changes network | Yes | New addresses found by mDNS or the relay; no re-pairing |
| Phone Wi-Fi to cellular | Yes with relay, else no | Direct path lost; relay path if on; otherwise "Not on the same network as CHRIS-PC" |
| Several phones | Yes | Each a separate device with its own grants; actions from one show on the others |
| Run ends while phone offline | Yes | Push notification if the PC is online (at most once); the run feed has it next time |

No Windows service is proposed. A service would run as another account, outside the person's
sign-in and token cache, which breaks "runs as you" and the local-first model. The tray option
keeps work in the person's own session.

## 8. Notifications

| Event | Default | Preview text (lock screen) |
| --- | --- | --- |
| Run finished | On for runs started from the phone, off for others | "A run finished on your PC" |
| Run failed | On | "A run failed on your PC" |
| Long-running (over N minutes, set on the phone) | Off | "A run is still going on your PC" |
| Resource threshold (memory or disk low) | Off | "Your PC is low on memory" |
| Deployment approval requested (P3) | Off | "An approval is waiting" |
| Optimization opportunity | Off | "New optimization suggestions" |
| PC disconnected (relay only) | Off | "Your PC went offline" |
| Security event (new pairing, failed pairing, device revoked) | On whenever notifications are on | "Security: a phone was paired with your PC" |

- Previews say "your PC" by default; showing the PC name is an opt-in setting on the phone.
- **Never in a preview:** item names, table, workspace or notebook names, error text, log lines,
  row counts. The full detail is fetched over the encrypted channel when the person opens the app.
- **Off until switched on:** push is a new outbound path to a third party, so it stays off until the
  person turns it on (separately from pairing), and previews carry no PC name by default.
- **Delivery:** the push payload carries an opaque event id and a ciphertext the phone decrypts
  with its device key; Apple and Google see only the generic text.
- **At most once:** a push that got no answer is not re-sent, consistent with the notice rule
  (#3032). The run feed is the record.
- **With the app closed:** Apple (APNs) and Google (FCM) require the sender to hold the app
  publisher's credentials, which can't ship inside every desktop install. Two ways that need no
  KratosForge server: Expo's push service (free; the PC sends to `exp.host` with the phone's Expo
  push token, no publisher secret on the PC), or, for an organization that blocks it, a
  tiny push gateway beside the org's relay. Recommended: Expo push for P1, gateway optional in P2.
- **Policy:** `companion_push` false turns phone notifications off on that PC.

## 9. Cost analysis

### Assumptions

- Each user pairs 1.2 phones and 1 PC. 60% of PCs are online at the busiest hour with remote access
  on. Each PC holds one idle relay WebSocket (heartbeat 30 s, about 100 bytes).
- A phone opens the app 5 times a day for 3 minutes; while open it follows runs at about one
  1 KB update every 2 seconds, plus log views: about 2 MB a user a day, 60 MB a month.
- 10 notifications a user a day. 2 operational commands a user a day.
- Relay instances: a small container holds about 10,000 idle WebSockets; two at minimum for
  availability above 1,000 users; routing by PC id through a shared pub/sub above one instance.
- Prices are approximate public list prices as of 2026 and must be re-checked before any spend.
  Egress about $0.08 a GB after a free allowance.

### Monthly operating cost if KratosForge runs the relay

| Users | Peak connections | Traffic / month | Relay hosting | Egress | Push | Total / month |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 100 | ~60 | ~6 GB | $10–30 (1 small instance) | ~$0 | $0 | **~$10–30** |
| 1,000 | ~600 | ~60 GB | $30–60 (2 small) | ~$0–5 | $0 | **~$30–65** |
| 10,000 | ~6,000 | ~600 GB | $150–400 (2–3 + pub/sub) | ~$40–50 | $0 | **~$200–450** |
| 100,000 | ~60,000 | ~6 TB | $1,500–4,000 (8–10 + pub/sub + LB) | ~$450–500 | $0 | **~$2,000–4,500** |

Not in the table: on-call and security patching for a public 24x7 service, which is the real cost
at every size for a self-funded project; a managed alternative (Azure Web PubSub, about $49 per
1,000 concurrent connections a month) lands in the same range at 10,000+ users with less operating
work. **Push:** APNs, FCM and Expo's push service charge nothing at these volumes.
**TURN (option C only):** for the 10–20% of sessions needing it, TURN bandwidth costs about the same
as relay traffic, on top of a signaling service.

### What runs where

| Component | Runs | Cost to KratosForge |
| --- | --- | --- |
| Pairing, channel, permissions, audit, run feed | On the PC | $0 |
| Phone app | On the phone | $99/year Apple developer account, $25 once Google Play (needed only to publish) |
| Direct connection, VPN connection | Local network / person's own VPN | $0 |
| Relay | Org-hosted (recommended) or KratosForge-hosted | $0 or the table above |
| Push | Expo push service, or org gateway | $0 |
| Central history (P4) | The organization's existing Fabric, Databricks or Snowflake | $0 to KratosForge |

## 10. Proof of concept plan

Goal: prove pairing, the encrypted channel, the run feed, start, cancel and revoke on Chris's PC
and phone, over the local network, with nothing in the cloud. Then prove the same channel through a
relay running **on Chris's own PC or LAN** (a local container), so no cloud resource is created.

Scope: stories MC-001 to MC-008. Built behind a developer setting that is off and hidden in
release builds; Preview; never shipped to Stable.

| Step | Shows | Pass condition |
| --- | --- | --- |
| 1 | Launch KratosForge with the PoC setting on | Settings > Mobile companion (Preview) is listed |
| 2 | Pair a phone shows a QR | QR holds no token or user data (decoded and checked in a test) |
| 3 | Scan on the phone (Expo Go) | Phone shows the PC name and a 6-digit code |
| 4 | Approve on the PC | PC shows the same 6 digits, phone name and platform; Approve with "Monitor only" or "Monitor and run allowed items" |
| 5 | Authenticated session | Reconnect works without the QR; audit shows `companion` pairing line |
| 6 | Status on the phone | PC name, online, engine version, memory, active runs |
| 7 | Start an allowed test item | `PoC: count rows` notebook, marked "Allow from phone", runs on the PC |
| 8 | Progress | Cell n of m and elapsed time update within 2 s |
| 9 | Cancel | Run shows Cancelled on both; metadata runs show no Cancel button |
| 10 | Disconnect and reconnect | Phone airplane mode on/off; feed resumes from its cursor, no duplicate runs |
| 11 | Revoke on the PC | Phone's open connection closes within 1 s |
| 12 | Revoked credentials fail | Reconnect refused with `device_revoked`; audit line written |

Security tests (automated, in CI, no network): unauthorized pairing (wrong PSK), expired challenge
(121 s), reused challenge, replayed handshake message, MITM (wrong desktop key in the QR), command
duplication (same `request_id` twice and concurrently), network interruption mid-start, oversize
and malformed frames (extend the existing framing fuzz test), revoked device mid-call, phone key
swapped after pairing, rate limit. Plus one manual check: the QR is never written to logs, crash
dumps or the support bundle.

Exit: a short report in this file with each step's result, and Security QC's verdict, before any P1
work starts.

## 11. Delivery roadmap (F)

| Phase | Contents | Stories | Gate to start |
| --- | --- | --- | --- |
| **P0 Foundation** | This evaluation, threat model, pairing protocol, PoC on LAN, relay PoC on Chris's LAN | MC-001 to MC-008 | Chris's typed go for the PoC |
| **P1 Mobile MVP** | Settings > Mobile companion, pairing UX, devices, run feed, status, history, allowed-item start, cancel, retry, push (Expo), hardware-bound keys, policy keys, docs | MC-101 to MC-112 | PoC report + Security QC clear + UX mockups approved + Chris's go; Apple/Google accounts if publishing |
| **P2 Remote connectivity** | Relay (org-hosted container), transport selector, presence, "PC went offline", optional push gateway, tray option | MC-201 to MC-207 | Chris's relay hosting choice; no hosted resource without his go |
| **P3 Advanced mobile** | Several PCs polish, parameter presets, optimization and savings, code viewing, Git status, semantic model results, Academy and ranks, keep-awake option | MC-301 to MC-307 | MVP adoption; each new screen mockup first |
| **P4 Enterprise** | Organization device policy package, team view of PCs that allow phones, shared history from the central lakehouse, approvals (after the promote-approval feature), MDM app config | MC-401 to MC-405 | Control plane live run; Chris's go per feature |

Changes from the brief's phases, from the codebase findings: the run feed (MC-001) moves into P0
because nothing today gives one live list of runs; push moves into P1 because Expo push needs no
server; deployment approvals move to P4 because the promote-approval feature they depend on isn't
built and the typed-name rule needs a decision.

## 12. Implementation recommendation (G)

1. **Feasible?** Yes. Every phone-side need is covered by mature components, and the PC side
   reuses the registry, permissions, approvals, audit and policy that external tools already use.
   The two real gaps are the run feed and the first non-loopback listener.
2. **Architecture:** hybrid (D), built direct-first: one Noise end-to-end channel over the local
   network now, and over an optional blind relay later. Expo (React Native) phone app.
3. **Simplest secure MVP:** pair by QR with a 6-digit match approved on the PC; monitor runs, status
   and history; notifications with generic previews; start, cancel and retry only items marked
   "Allow from phone" on the PC; revoke from the PC. Direct connection only.
4. **Reused code:** `editor_pairing.py` (request lifecycle, codes, limits), `editor_channel.py` /
   `http_server.py` guard patterns, `integrations/registry.py`, `permissions.py`, `scope.py`,
   `store.py`, `audit.py`, `policy.py`, cancel paths in each service, `local_schedules.run_now`,
   `workloads/inventory.py`, `redact.py`, `cryptography`, the framing fuzz tests.
5. **New pieces:** the run feed (one list over existing status calls); the companion transport
   (listener, Noise channel, device keys); a `RUN_SAVED_ITEMS` permission and "Allow from phone"
   list; the phone app; later, a blind relay and an optional push gateway.
6. **Largest security risks:** turning the PC into a network-reachable server (mitigated: off by
   default, private addresses only, end-to-end channel, policy kill switch); a stolen unlocked phone
   (hardware-bound keys, biometric for operational commands, revoke from the PC); a QR photographed
   by someone nearby (single-use, 120 s, PC-side approval with code match); scope creep from
   "start a saved item" to "run anything" (no code or paths from the phone, ever). See the
   [security design](mobile-companion-security.md).
7. **Ongoing cost:** $0 for the MVP. Remote: $0 if organizations host the relay; otherwise about
   $10–65 a month to 1,000 users, $200–450 at 10,000, $2,000–4,500 at 100,000, plus the operating
   work of a public service. App store fees $99/year + $25 once when publishing.
8. **Effort:** assumptions: agents build, Chris reviews; each PR passes Quality review and, for
   inbound surfaces, Security QC; about 2 PRs a thread open at once; Expo and Noise libraries
   behave as documented. **P0** 2–3 weeks, **P1** 5–7 weeks, **P2** 3–4 weeks (so 10–14 weeks to
   remote-capable MVP), **P3** 4–6 weeks, **P4** 4–6 weeks. Complexity: medium overall; high in the
   channel and pairing (security-critical, small code), low in the screens.
9. **Build first:** the run feed (MC-001) and the pairing protocol with its test vectors (MC-002),
   because both are needed by every later step and both can be tested without a phone.
10. **Out of scope on purpose:** editing code, SQL or notebooks on the phone; free-text parameters;
    deploys, Prod writes, promote, restore and credential changes from the phone (until Chris
    decides the approval design); pause; running anything while the PC is off or asleep; Wake-on-LAN;
    a Windows service; a KratosForge-hosted relay without Chris's go; storing devices or commands in
    a lakehouse; telemetry (stays opt-in, off).
