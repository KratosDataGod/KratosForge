# PR-ready specifications

Implementation packages a Claude thread (or a person) can pick up and build as one pull request.
Each follows the same sixteen sections, from the business problem to the Definition of Done, and
names its backlog item in [the product backlog](../PRODUCT_BACKLOG.md).

**None of these is approved.** A package is built only after the product owner moves its backlog
item to Approved. Packages exist only for items no specialist program owns; owned items are
specified by their programs (see [the backlog's ownership table](../PRODUCT_BACKLOG.md#how-items-are-owned)).

## Recommended execution sequence

| Order | Package | Priority | Effort | Why this position |
| ---: | --- | --- | --- | --- |
| 1 | [PB-002 Tenant smoke test](PB-002.md) | P0 | M | Every other verification claim waits on it; needs the maintainer's non-production workspace |
| 2 | [PB-071 Rust tests in CI](PB-071.md) | P1 required | S | Makes later shell changes safe; PB-008 and PB-072 depend on it |
| 3 | [PB-010 Security and network docs](PB-010.md) | P1 required | S | IT reviewers read these first; docs only |
| 4 | [PB-078 Preview tags match the docs](PB-078.md) | P1 required | S | Lets organizations hide what isn't proven |
| 5 | [PB-013 Local-parity defects](PB-013.md) | P1 required | M | Removes wrong answers in local execution |
| 6 | [PB-051 Fabric client resilience](PB-051.md) | P1 required | S | Fewer failed actions on a live tenant; informs PB-002 reruns |
| 7 | [PB-043 Cancel a Fabric run](PB-043.md) | P2 | S | Stops runaway capacity use from where people watch runs |
| 8 | [PB-077 User and agent docs](PB-077.md) | P2 | S | Docs only |
| 9 | [PB-018 Publish by item id](PB-018.md) | P2 | S | Prevents duplicate Fabric notebooks |
| 10 | [PB-044 Agent queue prune and requeue](PB-044.md) | P2 | S | Agent hygiene before wider agent use |

Packages 2 to 10 touch different files and can run in parallel threads, except that PB-010 and
PB-077 both edit docs and should merge one after the other.

## Package template

Every package has these sections:

1. PR title and ID
2. Business problem
3. Current verified behavior (with file references and what was observed versus read)
4. Desired behavior
5. In scope and out of scope
6. Architecture and files
7. Functional requirements
8. Nonfunctional requirements
9. Dependencies
10. Security and compatibility
11. Acceptance criteria
12. Unit and integration tests
13. Manual verification
14. Documentation
15. Rollback
16. Definition of Done

After a package's PR merges, it goes through the
[implementation review](../IMPLEMENTATION_REVIEW_PROCESS.md) before its backlog item can be
Accepted.
