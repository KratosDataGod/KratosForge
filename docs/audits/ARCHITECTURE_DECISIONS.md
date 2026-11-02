# Architecture decisions

These are the decisions that shape long-term maintenance, with the reasons for each. Earlier decision logs stay where they are: [implementation decisions](../product/implementation-decisions.md), [AI integration decisions](../product/ai-integration-decisions.md) and the [architecture overview](../architecture.md). This page records decisions the audit relies on or proposes. Proposed ones need the maintainer's agreement before any code changes.

## Accepted (already in the code)

### AD-1: The engine is a separate Python process behind a private pipe

The shell starts `fabric_desktop.server` and talks JSON lines over stdin and stdout. Engine calls never come in over the network. The local API for external tools is a separate, opt-in listener with its own guard.

**Why:** the data libraries (DuckDB, Polars, delta-rs, pyarrow) are Python-first, and a crash in them must not take down the window. **Consequence:** the shell needs a watchdog and a restart path; see EA-010 for the crash log.

### AD-2: Refuse rather than approximate when local semantics would differ from Fabric

Writer options the engine can't honour, dynamic partition overwrite, `TIMESTAMP_NTZ` writes and `DROP TABLE` on lakehouse tables all stop with a message. They are not run in an approximate form.

**Why:** a silent difference writes wrong data to a shared lakehouse. **Consequence:** the audit's interoperability findings (EA-001, EA-003, EA-004, EA-012 to EA-017) are fixed by extending this rule, not by adding a compatibility layer.

### AD-3: One pinned dependency set, bumped on purpose

`sidecar/constraints.txt` pins every engine package. CI tests the ranges at both ends where needed (sqlglot, DuckDB 2.0).

**Why:** an upstream release must not change the app overnight. **Open:** the pins aren't hash-locked (EA-020).

### AD-4: Delta's version check plus KratosForge leases for concurrency

Leases keep KratosForge's own writers apart. The Delta commit protocol is the final arbiter against everyone else.

**Consequence:** every overwrite must be pinned to the version it read (EA-005), and a lease must never override a commit that succeeded (EA-006).

## Proposed

### AD-5: Shared plumbing, one helper each (proposed)

The proposal is that atomic file writes, SQLite connections, retry and redaction each have exactly one implementation:

- `jsonfile` for atomic writes, which already calls `fsync`;
- a small SQLite helper that sets WAL, a busy timeout, and corruption set-aside;
- `retrying` for retry;
- `redact` for redaction.

Today there are 9, 7, about 9 and 5 copies (EA-028). Callers move over one PR at a time, each with a characterization test first.

**Why:** a fix to one copy (`fsync`, corruption handling, a new secret pattern) doesn't reach the others. **Not proposed:** a new framework or service layer.

### AD-6: Split the `Service` class by surface, not by rewrite (proposed)

`Service` combines 62 mixins and exposes 637 engine methods. The proposal leaves the class alone and keeps each mixin in its own module, as most are today. The shell's quick-call list is already checked against `server.QUICK` by `test_server_lanes.py`.

**Why:** this lowers coupling without a big-bang refactor.

### AD-7: Run state is written at the start (proposed)

Every long-running unit (a scheduled run, an agent job) writes a "running" record before it does any work. Leftover records are turned into "interrupted" on the next start, as `orchestration/runs.py` does for pipeline runs.

**Why:** a killed process must leave evidence, and must never block the next run silently (EA-008, EA-011).
