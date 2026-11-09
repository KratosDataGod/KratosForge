# Implementation review: PB-130 Data product promises, status and change windows (FEAT-MESH-04, FEAT-MESH-05, FEAT-GOV-01)

| Field | Value |
| --- | --- |
| Backlog item | PB-130 Data product promises, status and change windows (FEAT-MESH-04, FEAT-MESH-05, FEAT-GOV-01) |
| Pull request | #1737, #1744, #1774, #1775 and #1780, all merged; main at 56e3826b |
| Package | none |
| Reviewer | Product management thread, which didn't write these PRs |
| Date | 2026-11-07 |
| Environment | Linux container, with the engine from source in a Python 3.12 venv and a local platform database. No Fabric tenant and no Windows app were used |

## Outcome

**Accepted with Documented Limitations** (recommended). All three criteria are met on a local platform. Each screen is Preview. The Status view is only as current as the team's own incident check, and the change window only advises.

Recommended by: Product management thread. Decided by (product owner): Chris, 'Accept with limits' on the decision card on 2026-11-07 (project message cmsg_018T4o2pQdpbLBNhHpgVNNGpTcvHfkEFgjqcbKt2fwWMst).

## Acceptance criteria

| # | Criterion (copied from the backlog item) | Result | Evidence |
| ---: | --- | --- | --- |
| 1 | A product's promise is shown to readers and its team sees Kept or Broken | Met | The promise lives in the product file and the catalog (`test_a_promise_is_written_read_and_described`, `test_the_catalog_keeps_the_promise_readers_see`, #1737). The gate refuses NaN, infinite hours and bad times (#1744). Readers see the promise on the product page; only its own team sees Kept or Broken, worked out from the open incidents (`product-promise.test.cjs`: "a reader sees what the product promises, and no Now column", "its own team sees each promise kept or broken, from the open incidents", #1774) |
| 2 | A broken promise opens one incident | Met | `workloads/evaluate.py` turns a late load or too many rejected rows into one signal (`test_both_causes_make_one_signal`), doesn't report an entity's own lateness twice (`test_the_entitys_own_lateness_isnt_reported_twice`), and opens nothing for a kept promise or where the promise doesn't apply (#1737) |
| 3 | Status and change window views read the same records | Met | Status reads `catalog.product_status`, which the team's incident check writes only when a status changes. Everyone reads the same row per product and environment (`test_a_team_reports_and_everyone_reads`, `test_the_incident_check_reports_and_readers_see_it_with_the_products`, #1780). The change window is one platform setting: the Platform > Govern card and the promote warning both read it through `change_window`, and it's judged in the window's own time zone (`change-window.test.cjs`, `mesh.change_window_notes`, #1775) |

## Checks

| Check | Result | Evidence or note |
| --- | --- | --- |
| Scope matches the item | Yes | The five PRs listed in the item; docs/data-mesh.md describes each one |
| Full suites pass | Partly | test_product_promises.py, test_product_status.py, test_dataops.py and test_central_roles.py: 408 passed here. product-promise, change-window and product-status browser tests: 18 passed, with the axe test skipped because axe-core isn't installed here (CI runs it). Main CI is green at 56e3826b |
| Reachable where docs say; Preview behavior right | Yes | The Status button, the promise on the product page and the Change window card all sit behind Preview ("with Preview features hidden the card isn't shown"). A platform made before these features shows nothing until Finish or repair |
| OneLake data safety | Yes | Nothing writes to OneLake. Reporting writes one catalog row, and only when the status changed |
| Authentication and roles | Yes | Only a project's own members or a platform admin can report its status (`test_only_the_projects_own_members_report`). Only admins set the window; others read it with no buttons |
| Docs match the code | Yes | docs/data-mesh.md: promises, the Change window paragraph and "How each product is doing now" |
| Accessibility | Partly | The axe test on the change window card and dialog runs in CI, not here. No Narrator pass on Windows |

## Findings

None blocking.

## Documented limitations

| Limitation | Where users are told | Backlog item that tracks it |
| --- | --- | --- |
| All three screens are Preview, and nothing was run against a Fabric tenant or in the Windows app | docs/data-mesh.md, which marks each section (Preview) | PB-002 (tenant smoke test) |
| Status is only as current as the team's own incident check (the app while it's open, or the team's agent). A product nobody has checked shows Not checked | docs/data-mesh.md, "How each product is doing now" | none: by design |
| The change window warns and never blocks a promote | docs/data-mesh.md, "The change window" | none: by design ("Risk check advises, never blocks") |

## Inventory and backlog changes

- PB-130: In progress → In review. Evidence links this review.
- No feature row status changed.
