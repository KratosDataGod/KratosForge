# Mobile companion: stories

Stories for the [mobile companion plan](mobile-companion-plan.md), in build order. None is started.
P0 needs Chris's typed go; each later phase needs its gate in the plan, section 11. A story is done
only when its tests pass in CI and, where it says so, the manual check on Chris's PC and phone
passed. No story here duplicates an existing one: the closest are the registry and permissions
(PR-AI-00x, built), VS Code pairing (built) and "Approval before a promote to Prod" (ai-commons
status, not started), which MC-404 depends on rather than repeats.

Complexity: S (under 2 days), M (2 to 5 days), L (1 to 2 weeks). Owner is the thread expected to do
the work; "this thread" is the mobile companion thread.

## P0 Foundation

### MC-001 Run feed (P0, M)
- **User value:** one list of what is running and what ran, for the phone and later the desktop Monitor.
- **Scope:** read-only `RunFeed` over sql_runs, notebook batch, pipelines, proofs, side-by-side, local schedule results, metadata run log, Fabric runs started from the app. Ids `kind:native_id`; state; started, ended, elapsed; progress where reported; `can_cancel` per run type; `since` cursor.
- **Dependencies:** none.
- **Technical:** `sidecar/fabric_desktop/companion/feed.py`; adapters call existing status methods only; no new stored data.
- **Acceptance:** each run type appears with the right state; metadata runs report `can_cancel: false`; a cursor returns only changes; 1,000 runs listed in under 200 ms.
- **Security:** names pass `redact.secrets`; no outputs or rows.
- **Tests:** unit tests with fakes per adapter; one integration test with a real SQL run and a notebook batch.
- **Owner:** this thread.

### MC-002 Noise channel and pairing protocol (P0, M)
- **User value:** a phone and PC can trust each other with no certificates and no accounts.
- **Scope:** `Noise_XXpsk3` and `Noise_KK` over `cryptography`; frame format; rekey; QR payload `kfpair:1`; 6-digit code from the handshake hash.
- **Dependencies:** none.
- **Technical:** `companion/noise.py`, `companion/pairing.py` (reusing `editor_pairing.Pairings` request lifecycle: 120 s, 3 waiting, single use); published Noise test vectors in `sidecar/tests/data/`.
- **Acceptance:** passes the published Noise vectors for both patterns and an interop test against a reference implementation (release-blocking; a vetted library is preferred if it passes both); QR decodes to no reusable credential; secret burned on the first attempt, success or failure.
- **Security:** Security QC review (new protocol). See security design T2 to T4.
- **Tests:** vectors, wrong PSK, expired, reused, wrong fingerprint, reordered counter, replay.
- **Owner:** this thread; Security QC reviews.

### MC-003 Companion listener, PoC (P0, M)
- **User value:** the phone can reach the PC on the same network.
- **Scope:** WebSocket listener on explicit private interface addresses whose Windows network category is Private or Domain, re-checked and closed on any network change; started only while the PoC developer flag is on; no thread per connection before authentication; 5 s deadlines for the upgrade and first Noise message; header and global unauthenticated caps; handshake before any message.
- **Dependencies:** MC-002.
- **Technical:** standard library + the existing HTTP server pattern, or `websockets` only if Security QC and the dependency review accept it; port written to `companion-port` in the data folder.
- **Acceptance:** refuses public-address and wildcard binds; not listening on a Public network; slow clients cut at 5 s; 33rd unauthenticated socket refused; 17th authenticated connection refused.
- **Security:** inbound surface: Security QC. T1, T14, T15, T22.
- **Tests:** bind refusal, network-profile change, caps and deadlines, pre-auth parser fuzz (release-blocking), Origin refusal.
- **Owner:** this thread; Windows hardening checks the firewall prompt on Chris's PC.

### MC-004 Companion transport onto the registry (P0, M)
- **User value:** every check that protects AI tools protects phones too.
- **Scope:** `CompanionChannel` maps a device to a `clients` row (`kind = 'companion'`, public keys); `companion=True` tag on capabilities; channel = `companion` in audit; `RUN_SAVED_ITEMS` permission.
- **Dependencies:** MC-002.
- **Technical:** additive columns in `integrations/store.py` (older builds ignore them); `registry.PERMISSIONS` gains `RUN_SAVED_ITEMS`; `docs/permissions.md` updated.
- **Acceptance:** a non-companion capability called on the channel is refused; audit lines carry device id and path.
- **Security:** approval path and permissions: Security QC; MCP server thread signs off on registry changes.
- **Tests:** registry tests per refusal; store migration test both directions.
- **Owner:** this thread with the MCP server thread.

### MC-005 Companion capabilities, read and run (P0, M)
- **User value:** status, runs, start, cancel and retry from the phone.
- **Scope:** `companion_get_pc_status`, `companion_list_runs`, `companion_get_run`, `companion_watch`, `companion_get_metrics`, `companion_list_runnable`, `companion_start_saved`, `companion_cancel_run`, `companion_retry_run`; `request_id` idempotency table.
- **Dependencies:** MC-001, MC-004.
- **Technical:** handlers call existing services (`local_schedules.run_now`, `notebook_batch`, `pipeline_service`, `sql_cancel`, `cancel_fabric_run`); allowlist with content hash.
- **Acceptance:** duplicate `request_id` starts one run; operational call without a valid operational-key signature refused; audit write failure refuses the command; changed item or changed preset values refused; `_job_guard` refusal passes through; cancel absent where unsupported.
- **Security:** T8 to T12.
- **Tests:** service tests with fakes; concurrency test for duplicate requests.
- **Owner:** this thread.

### MC-006 PoC desktop page (P0, S)
- **User value:** pair, approve and revoke on the PC.
- **Scope:** a developer-only Settings > Mobile companion (Preview) page: switch, QR, approval dialog, devices, remove, disconnect all. Hidden in release builds.
- **Dependencies:** MC-002, MC-004; UX thread sketch (full mockup is MC-107).
- **Technical:** `app/ui/companion.js`, QR drawn in the page (vendored QR encoder, listed in `deps.js`), never written to disk.
- **Acceptance:** PoC steps 1, 2, 4, 11.
- **Security:** QR not in logs or support bundle.
- **Tests:** browser test with a fake engine.
- **Owner:** this thread; UX thread sketch.

### MC-007 PoC phone app in Expo Go (P0, M)
- **User value:** proves the experience on a real phone at no cost.
- **Scope:** scan, pair, Home, Jobs, Job details, Run, cancel, reconnect; software keys only (PoC exception, ADR MC-ADR-003); behind a developer flag that is off by default and never merged enabled; runs against `kf_tenant_test` only.
- **Dependencies:** MC-002, MC-005.
- **Technical:** new folder `mobile/` (Expo, TypeScript), `@noble/*` crypto, shared Noise vectors as a test fixture.
- **Acceptance:** PoC steps 3 to 12 on Chris's phone.
- **Security:** no network calls besides the PC; no analytics SDKs.
- **Tests:** Jest tests for the channel against the same vectors as the PC.
- **Owner:** this thread; run on Chris's phone and PC with him (Remote Control for the PC side).

### MC-008 Relay PoC on the LAN (P0, S)
- **User value:** proves the relay path without any cloud resource.
- **Scope:** minimal blind relay in `relay/`, run locally (Docker or Python) on Chris's PC; PC relay link; phone transport selector.
- **Dependencies:** MC-002, MC-007.
- **Acceptance:** same PoC steps through the relay; relay log holds no plaintext (checked).
- **Security:** T5.
- **Tests:** relay unit tests; relay given an unknown key hash refuses.
- **Owner:** this thread.

## P1 Mobile MVP

### MC-101 Run feed in the desktop Monitor (P1, M)
- **User value:** the desktop and phone show the same list. **Scope:** Monitor reads MC-001 where it helps; mockup first. **Dependencies:** MC-001, UX mockup. **Acceptance:** per mockup. **Security:** none new. **Tests:** browser test. **Owner:** UX thread + this thread.

### MC-102 PC metrics call (P1, S)
- **User value:** see if the PC is struggling. **Scope:** memory, CPU, disk free of the data drive, sampled at most every 2 s. **Technical:** Windows APIs via `ctypes` (as `free_memory.py`), no new dependency. **Acceptance:** values within 5% of Task Manager. **Security:** none. **Tests:** unit with fakes. **Owner:** this thread.

### MC-103 Allow from phone (P1, M)
- **User value:** the person decides at the PC what a phone may start. **Scope:** item menu entry on notebooks, SQL scripts, pipelines, schedules, metadata environments; presets; content hash; list on the settings page. **Dependencies:** MC-005, UX mockup. **Acceptance:** changed items disabled on the phone. **Security:** T9. **Tests:** service and browser tests. **Owner:** this thread.

### MC-104 Hardware-bound device keys (P1, M, release-blocking)
- **User value:** a copied phone backup can't control the PC. **Scope:** Expo development build with a native module for two Secure Enclave / Android Keystore P-256 keys: a monitor key (no presence) and an operational key (iOS `.userPresence`, Android validity duration 0) that signs each operational command for the PC to verify. **Dependencies:** MC-007. **Acceptance:** key marked non-exportable on both platforms; operational command without biometric refused. **Security:** T6, T7. **Tests:** device tests on one iPhone and one Android. **Owner:** this thread.

### MC-105 Policy keys and ADMX (P1, S)
- **User value:** IT can turn phones off or limit them. **Scope:** keys in MC-ADR-008; `companion_relay_url` in `LOOSENING`; ADMX template; `docs/enterprise-install.md`. **Acceptance:** each key fails closed. **Security:** T19. **Tests:** policy tests per key. **Owner:** this thread; Governance reviews.

### MC-106 Notifications through Expo push (P1, M)
- **User value:** know when a run ends without watching. **Scope:** off until the person turns it on, separately from pairing; no PC name in previews by default; push token registration inside the channel; event rules; generic text + ciphertext; at-most-once. **Dependencies:** MC-005, MC-104. **Acceptance:** previews contain no item names (test inspects the payload). **Security:** T13, T21. **Tests:** payload tests; send failure not retried. **Owner:** this thread; Governance (notices) reviews.

### MC-107 Desktop Settings > Mobile companion page (P1, M)
- **User value:** pairing and devices for everyone. **Scope:** per approved mockup (UX spec). **Dependencies:** UX mockup approved by Chris. **Acceptance:** per mockup; a11y check. **Security:** destructive actions via `confirmDialog` danger. **Tests:** browser + a11y. **Owner:** UX thread (mockup), this thread (build).

### MC-108 Phone app MVP screens (P1, L)
- **User value:** the full MVP experience. **Scope:** Home, Jobs, Job details, Run, Optimize (read-only), Devices, Profile, error states, cache, app lock. **Dependencies:** MC-104, phone mockups approved. **Acceptance:** UX spec journeys 1 to 6. **Security:** cache cleared on unpair. **Tests:** Jest + Maestro (or Detox) flows. **Owner:** this thread.

### MC-109 Security hardening pass (P1, M, release-blocking)
- **User value:** confidence before anyone else installs it. **Scope:** every test in security design section 9; rate limits; idle timeouts; emergency disconnect in the tray menu. **Acceptance:** Security QC CLEAR. **Owner:** this thread; Security QC.

### MC-110 Docs (P1, S)
- **User value:** people and IT know what it does. **Scope:** `docs/mobile-companion.md`, `docs/security.md` boundary 6, `docs/permissions.md`, `docs/architecture.md`, `docs/ideas.md` scope line. **Owner:** this thread.

### MC-111 Store release setup (P1, S, needs Chris)
- **User value:** install from the App Store and Google Play. **Scope:** accounts ($99/year Apple, $25 Google, Chris's go), EAS build profiles, privacy labels (no data collected), TestFlight / internal testing. **Owner:** Chris (accounts), this thread (setup).

### MC-112 Live check on Chris's PC and phone (P1, S, gate for Stable)
- **User value:** proven on real hardware. **Scope:** journeys 1 to 6 on his PC and phone; result recorded. **Owner:** this thread with Chris.

## P2 Remote connectivity

### MC-201 Production relay (P2, M)
Blind relay container, horizontal scale by routing id, health endpoint, no storage. **Security:** T5; Security QC. **Tests:** load test 10,000 idle sockets on one instance. **Gate:** Chris's hosting choice. **Owner:** this thread.

### MC-202 Relay link in the engine (P2, M)
Outbound wss through the Windows proxy; reconnect with backoff; registers phone key hashes; policy URL only. **Owner:** this thread.

### MC-203 Transport selector on the phone (P2, S)
Direct then relay, 1.5 s budget; path shown on Devices. **Owner:** this thread.

### MC-204 Presence and "PC went offline" (P2, S)
Relay notices the PC socket drop; push after 5 minutes offline if the person turned it on. **Owner:** this thread.

### MC-205 Optional push gateway for organizations (P2, S)
For organizations that block Expo's push service; runs beside their relay. **Owner:** this thread.

### MC-206 Keep running in the notification area (P2, M, mockup first)
Opt-in: closing the window keeps the engine running while phones are paired; tray menu has Open, Disconnect all phones, Quit. **Owner:** Windows hardening thread with this thread.

### MC-207 Relay deployment guide (P2, S)
Azure Container Apps and plain Docker; how to set `companion_relay_url` by Group Policy. **Owner:** this thread.

## P3 Advanced mobile

- **MC-301** Several PCs: switcher polish, per-PC notifications (S).
- **MC-302** Parameter presets edited on the PC, picked on the phone (M).
- **MC-303** Savings and recommendations detail (S; ESI thread reviews figures).
- **MC-304** Read-only notebook and SQL viewer (M; `READ_METADATA`, code shown, never editable).
- **MC-305** Git status and semantic model validation results, read-only (S).
- **MC-306** Profile: Academy progress, rank and achievements art per the visual style guide (S; Ranks and Achievements threads).
- **MC-307** Keep this PC awake while a phone-started run runs (S, off by default).

## P4 Enterprise

- **MC-401** Organization device policy published through the control plane's policy package (M; control plane thread).
- **MC-402** Team view: installations that allow phones, from the existing `installations` rows (M; read-only).
- **MC-403** Companion audit lines in the central lakehouse through the existing sync (M; Lakehouse optimize co-sign).
- **MC-404** Approvals from the phone, after "Approval before a promote to Prod" exists and Chris decides the typed-name rule on a phone (L).
- **MC-405** MDM managed app configuration (relay URL, notifications off) for Intune (S).
