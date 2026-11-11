# Quality gates

This page lists the automated checks every change passes, what each one enforces, and the release criteria on top of them. The per-PR review process is the [PR quality gate](../quality/pr-review.md), and the merge path is [Release management](../release-management/README.md).

## Checks on every pull request (`.github/workflows/ci.yml`)

| Job | What it enforces | Blocks merge |
| --- | --- | --- |
| `sidecar` | The engine test suite, about 8,700 tests, on the pinned packages. The SQL tests also run at both ends of the sqlglot range, and the engine suite on DuckDB 2.0: on every main push, nightly (`compat-nightly.yml`), and on a pull request that changes package versions or engine code that imports duckdb or sqlglot | Yes |
| `ui` | `node --test app/tests/ui.test.cjs`, including the raw-HTML-sink check | Yes |
| `lint` | `ruff check` (rules E9, F, B, BLE) and the mypy ratchet: new type errors fail, against `sidecar/mypy-baseline/` (one file per module) | Yes |
| `ui-browser` | Real UI in headless Chromium, with the engine for SQL and Optimize, plus the axe accessibility audit | Yes |
| `vscode-extension` | Build and tests of the VS Code extension | Yes |
| `audit` | `pip-audit` on `constraints.txt`, `engine-requirements.txt` and the Databricks Connect locks; `npm audit` on the UI vendor and VS Code lock files (high and critical fail); `cargo audit` on `Cargo.lock`. The CI tools' own advisories are informational only | Yes |
| `secrets` | detect-secrets, through `.github/scripts/secret_scan.py`, on a pull request's changed files (every tracked file on main). A finding that `.secrets.baseline` doesn't list as reviewed fails | Yes |
| `engine`, `shell` | Windows build of the engine zip and the exe; Rust tests (`cargo test --release --locked`) | Yes |
| `sbom-app`, `release` | SBOMs, provenance attestations, and the Preview publish (main only) | Main only |
| `docs.yml` | `zensical build --strict`, which fails on broken links | Yes |

The engine suite also includes these repository rules:

- `test_silent_except.py`: a broad `except` must log, narrow or carry a reviewed `# silent-ok:` reason.
- Version-pin tests for Python, DuckDB and sqlglot.
- The T-SQL contract cases.

## Before pushing (contributors and agents)

Run all of these on the files you changed:

- `python -m pytest -q` (at least the affected tests and `tests/test_silent_except.py`)
- `python -m py_compile`
- `python -m ruff check .`
- `python ../.github/scripts/mypy_ratchet.py`
- `node --test app/tests/ui.test.cjs` when you change UI files.

Install with `-c constraints.txt` so you test what users get.

## What every change must carry

- A regression test for every verified defect it fixes. The test must fail before the fix.
- A data-mutating change needs a test that checks the table or file afterwards, not only which call was made.
- No new skipped tests, quarantines or mocks that hide a real failure. A flaky test goes in the flaky ledger with an owner.
- Docs updated in the same PR when user-visible behaviour changes. Preview stays on anything untested on a tenant.

## Release criteria

| Gate | Criterion | State at `b75dcf5`, R3 updated at `1ca71e3` (2026-11-05) |
| --- | --- | --- |
| R1 Green CI | Every job above passes on the release commit | Met on `aed09ee2` |
| R2 No open P0 | No P0 in the [backlog](REMEDIATION_BACKLOG.md) or the private security list | Met |
| R3 No open P1 data-integrity defect | EA-001, EA-002 and EA-004 closed | Met on 2026-11-05 (#1572, #1576, #1600, #1645, #1646, #1647) |
| R4 Signed builds | The exe and engine are Authenticode-signed, and CI fails an unsigned main build | **Blocked** (Trusted Signing identity check) |
| R5 Live verification | The FD-101 harness passes on the non-production test workspace, with the result recorded | **Not run** |
| R6 Stable promotion | A build tried on Preview, then promoted with the maintainer's approval in the `stable` environment | In place (`promote.yml`) |

## Progressive tightening (proposed, one PR each)

These are proposals only. Each would be its own PR, and none is in this one.

1. Add Ruff `B` rules, except reviewed false positives such as B023 on closures called inside the loop, and `BLE`. Fix or annotate the existing hits in small batches (EA-027).
2. Lock in mypy decreases as they land. The ratchet already supports `--update`; the Codebase audit thread owns it.
3. Measure coverage in CI, for information only. Coverage will not be made a gate.
4. Add a start-up and notebook-start timing benchmark, recorded per build, warning-only at first (EA-029).
