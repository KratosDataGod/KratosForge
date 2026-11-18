# Implementation review: PB-061 Missing policy keys (FD-105)

| Field | Value |
| --- | --- |
| Backlog item | PB-061 Missing policy keys (FD-105) |
| Pull request | #1150 (allow_ switches fail closed), #2569 (policy docs match what the app reads), #2585 (ui_color_mode), #2305 (audit and receipt time limits), #2577 (a choice set by policy stays readable), #2258 (Group Policy ADMX templates, read from HKLM), all merged; main at 7be2906d |
| Package | none |
| Reviewer | Product management thread, which didn't write these PRs |
| Date | 2026-11-18 |
| Environment | Linux container, engine from source in a Python 3.12 venv. No Windows Group Policy run and no tenant |

## Outcome

**Accepted with Documented Limitations** (recommended). Each key FD-105 named is now implemented or was
removed by the product owner's decision, and the docs match what the app reads, with a test that keeps them
in step. The limit is on proof: the ADMX templates and the HKLM read are tested against the policy schema and
a simulated registry, but no domain or Intune push of the templates has been tried on a Windows PC.

Recommended by: Product management thread. Decided by (product owner): pending.

## Acceptance criteria

The item's acceptance says "As defined in FD-105". FD-105 (docs/product/enterprise-gap-analysis.md)
names the gap as "no policy for approved endpoints, execution location (this PC, Fabric), automatic
updates, local data retention or telemetry. Unknown keys and a malformed file are silently ignored."

| # | Criterion | Result | Evidence |
| ---: | --- | --- | --- |
| 1 | Approved endpoints | Met by decision | The product owner chose no general `allowed_hosts` filter on 2026-11-13; IT keeps the per-feature host keys and the proxy host list. Hosts that widen trust are in `app_settings.LOOSENING` and are taken only from a source the person couldn't change (`test_policy_loosening.py`) |
| 2 | Execution location | Met by decision | The product owner chose no "Fabric only" compute key on 2026-11-13; `allow_fabric_runs` and the other `allow_` switches fail closed (#1150, `test_policy_fail_closed.py`) |
| 3 | Automatic updates | Met | `auto_update` and `channel` are policy keys the shell enforces; `engine_download_url` is taken only from a key the account couldn't change (#2258) |
| 4 | Local data retention | Met | `local_retention_days`, `audit_retention_days` and `receipt_retention_days` (#2305), the last two IT only and in `LOOSENING` |
| 5 | Telemetry | Met | `telemetry` is off unless the person turns it on; `telemetry_connection_string` is IT only and in `LOOSENING` |
| 6 | Unknown keys and a malformed file are reported, not silently ignored | Met | `policy_problems()` says what was ignored or restricted and why; a restriction that can't be read fails closed (`RESTRICTED`) |
| 7 | The docs describe only keys the app reads | Met | #2569; `test_policy_schema.py::test_every_policy_key_is_described_once`, `::test_keys_read_outside_app_settings_are_policy_keys` |
| 8 | Group Policy can set them | Met, not tried on a domain | ADMX and ADML generated from the policy schema (`deploy/policy`), and the app reads HKLM, where values win over policy.json (#2258). `test_policy_schema.py::test_the_committed_templates_are_what_the_generator_writes`, `::test_every_setting_a_template_can_hold_is_in_the_admx_with_the_right_box`, `test_registry_policy.py` |

## Checks

| Check | Result | Evidence or note |
| --- | --- | --- |
| Scope matches the item | Yes | Every FD-105 gap is implemented or closed by the product owner's decision of 2026-11-13 |
| Full suites pass | Yes, for this area | app_settings, policy schema, registry policy, loosening, fail closed, watch and trust tests: 167 passed, 1 skipped here. The full suites run in main CI |
| Reachable where docs say; Preview behavior right | Yes | docs/enterprise-install.md (policy.json, Group Policy through `deploy/policy`, loosening keys); ui_color_mode is Preview (#2585) |
| OneLake data safety | Yes | Nothing here writes to OneLake |
| Authentication | Yes | `client_id` and sign-in hosts loosen trust, so they're taken only from an admin-only source |
| No unproven local-equals-platform claim | Yes | No platform claim |
| Docs match the code | Yes | Guarded by #2569's test |
| Accessibility | Not applicable | No new controls in this review |

## Findings

| # | Severity | Finding | Where | Status |
| ---: | --- | --- | --- | --- |
| 1 | Note | The ADMX templates haven't been imported into a domain or Intune and applied to a Windows PC | deploy/policy | Documented limitation |

None blocking.

## Documented limitations

| Limitation | Where users are told | Backlog item that tracks it |
| --- | --- | --- |
| Group Policy templates are tested against the schema and a simulated registry, not a live domain or Intune push | docs/enterprise-install.md | PB-002 (live harness), or a Windows check on the product owner's PC |
| The sign-in tenant key (#2419) isn't on main yet; it widens trust, so it must join `app_settings.LOOSENING` when it lands | Not user-facing until it lands | PB-061 follow-up with #2419 |

## Inventory and backlog changes

- PB-061: In progress → In review. Evidence links this review.
- No feature row status changed.
