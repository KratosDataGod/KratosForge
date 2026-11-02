# Security and data integrity

This page covers the threat model, the verified controls and the open findings for security and for data integrity. Exploit-sensitive detail is never written here. A security finding is tracked privately with the security owner until a fixed build is on Stable (see [SECURITY.md](https://github.com/KratosDataGod/KratosForge/blob/main/SECURITY.md)). After that, only a neutral summary is added to this page.

Related pages:
- [Security model](../security.md), the user-facing description.
- [Permissions](../permissions.md).
- [AI integration security](../product/ai-integration-security.md).
- The [hardening register](../hardening/findings.md).

Baseline: `main` at `b75dcf5`, 2026-11-02.

## Threat model

| Asset | Threat | Attacker position | Main controls |
| --- | --- | --- | --- |
| Fabric sign-in (tokens) | Theft from disk, logs or errors | Malware or another account on the PC; anyone who reads a support bundle | DPAPI-encrypted MSAL cache (no plain-file fallback on Windows); redaction on logs, errors and support bundles; the Power BI embed token kept only in memory |
| Engine (runs code as the user) | A local process or web page drives it | Another local program, a browser tab, DNS rebinding | Engine calls come only over the shell's private pipe. The local API: loopback only, same Windows account, Host check, no `Origin`, hashed bearer tokens, rate limits, approvals tied to argument hashes. MCP over HTTP uses the SDK's DNS-rebinding protection |
| OneLake data | Unintended overwrite, delete or corruption | The user's own mistake, a retry, a concurrent writer, a crash | Previews and confirmations; writer options refused when they can't be honoured; leases; the Delta version check; handling of failures after the commit; quarantine only after the commit. Open: EA-001, EA-005, EA-006, EA-007 |
| Files on this PC | Path traversal through deep links, notebook names or imports | A crafted link or file | Text containment checks, refused reparse points, refused reserved names |
| Updates | A malicious or downgraded build | Anyone who can publish a GitHub release; a network attacker | Checksums required; downgrades refused per channel; a signed build only takes updates from the same signer. **Open: builds are unsigned until Trusted Signing is set (EA-009)** |
| CI and releases | A malicious PR or a compromised action | A fork contributor, or an upstream action | Actions pinned by SHA; read-only default token; `pull_request_target` never checks out PR code; pip-audit and cargo audit; SBOM and provenance attestations |
| Agent instructions | Text in a PR, issue or file steering an AI reviewer | Anyone who can comment | The routing workflow only recommends and never approves or merges (AGENTS.md). PR text is treated as untrusted |

## Verified controls

Each control below was checked on current code. Most have a regression test, named next to them:

- **Local API guard:** `test_integrations_http.py`, `test_integrations_peer.py`, `test_integrations_permissions.py`.
- **Engine method allowlist:** `server.py` runs only names in `METHODS`, never arbitrary attributes. Checked by code reading; no dedicated test was identified.
- **Execute-SQL sandbox:** one read-only statement, external access off and settings locked. The audit tried `read_csv`, `read_text`, `glob`, multiple statements and `PRAGMA`, and all were refused.
- **SQL built from strings:** the audit reviewed every flagged site reachable from the local API, MCP or proofs. Identifiers are quoted, and numbers are coerced. No injection was found.
- **Deep links and notebook paths:** `test_notebook_files.py`, `deep_link.rs`.
- **Token cache:** `test_token_cache_dpapi.py`.
- **Downgrade guard and signer pinning:** `update_guard.rs`, `signature.rs`.
- **Silent failures:** every broad `except` must log or carry a reviewed reason (`test_silent_except.py`).

## Security findings

- No P0 or P1 security defect was found on the inbound surfaces at this baseline.
- **Four P3 hardening items** are tracked privately and routed to their owners.
- **Supply-chain gaps** (public, not exploit-sensitive) are EA-009 and EA-020 in the [backlog](REMEDIATION_BACKLOG.md).

## Data integrity findings

| Id | Pri | Summary | State |
| --- | --- | --- | --- |
| EA-001 | P1 | Appends quietly convert values to the table's column types | Open, routed |
| EA-002 | P1 | Notebook Parquet and CSV file writes ignored the save mode, so append replaced the file | Fix in review |
| EA-004 | P1 | `sum` and decimal arithmetic write different Delta types than Spark | Open, routed |
| EA-005 | P2 | An overwrite keeps rows a concurrent external writer appended | Open, routed |
| EA-006 | P2 | A lease lost after the commit is reported as a failure, and a rerun duplicates the append | Open, routed |
| EA-007 | P2 | A failed file upload empties the file being replaced | Open, routed |
| EA-025 | P3 | Lease expiry depends on clocks agreeing across PCs | Open |

### Verification still missing

- **Live OneLake behaviour (UNVERIFIED).** Nothing here has run against a tenant: create-truncate semantics, lease files on OneLake, and SQL endpoint and Direct Lake reading tables written on this PC. This needs the harness run (EA-009) on the non-production test workspace.
- **Failure injection.** It exists for failures after the commit, for leases and for upload retries. Still to add, each with its fix:
  - an external writer during an overwrite (EA-005);
  - a lease lost after the commit (EA-006);
  - a failed upload over an existing file (EA-007);
  - a process killed mid-schedule (EA-008).

## Release-critical requirements

None of these may be waived for an enterprise-ready rating:

1. Builds are signed, and a signed build accepts updates only from its own signer.
2. The live harness passes on a non-production workspace, with the result recorded.
3. No open P0 or P1 data-integrity defect. Today EA-001 and EA-004 are open, and EA-002 is in review.
4. A crash or failure never reports success, and never leaves data replaced without the user asking for it.
