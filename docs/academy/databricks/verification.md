# Databricks path: acceptance and verification strategy

## What each class may claim

| Class | Evidence | May say | May not say |
| --- | --- | --- | --- |
| LOCAL VERIFIED | The learner's code ran in the sandbox on this PC; validators passed on its output | "Your PySpark produced the right silver table on DuckDB's PySpark API on this PC" | That it ran on Spark, Databricks Runtime or Photon |
| SIMULATED | The learner's document or code was checked against KratosForge's model of a Databricks service | "Your job definition behaves as documented in KratosForge's Jobs model" | That Databricks ran it |
| DATABRICKS VERIFIED | The learner's artifact ran in their own workspace and a read-only check confirmed the outcome | "Ran in your Databricks workspace on <compute> on <date>" | Anything about workspaces other than theirs |
| KNOWLEDGE ONLY | Answers to questions | "You answered N of M correctly" | That a skill was demonstrated |

Rules enforced by `problems()`: only the `databricks` adapter gives DATABRICKS VERIFIED; a local
result's engine label and `verifies` sentences may not name a Databricks engine (Databricks
Runtime, Photon, serverless, SQL warehouse, Databricks Spark); a Databricks pack can't hold FABRIC
VERIFIED exercises and a Fabric pack can't hold DATABRICKS VERIFIED ones. An objective that needs
Databricks completes only with DATABRICKS VERIFIED evidence. Readiness shows local and
Databricks-verified experience side by side, never blended.

## How content is proven

- **Every exercise:** the reference solution passes, every planted wrong answer fails with a
  message naming the problem, in CI (`self_test`). Deterministic seeds; two sizes (small for
  guided, large for challenges and capstones).
- **Functional, not textual:** validators read tables, documents and run records. Any correct
  solution passes; a copied reference isn't needed.
- **Idempotency:** where an exercise is a load or a MERGE, `idempotent` reruns the learner's code
  and requires the same table state.
- **Hidden data:** challenges and capstones also grade on a second seed the learner never saw.
- **Partial credit:** weighted checks; a pass needs every `required` check.
- **Models are cited:** every SIMULATED behaviour cites the Databricks page it models, fetched on a
  recorded date. When a recorded real run exists for a model (bundle validate output, Spark stage
  metrics), a test compares the model with it.
- **Questions:** each cites fetched documentation that supports the answer; original wording.
- **Parity claims:** only through `platforms.py` evidence and the parity table (#1772).

## Remote verification

Never in CI and never without the learner's confirm. Inside this project a remote run also needs
Chris's typed OK, and runs first on Databricks Free Edition or a workspace he names. Each remote
check is read-only after the learner's own run; cleanup is proven by a test that refuses untagged
and unlisted objects.

## Quality gates

A story is done only with: unit and integration tests passing; reference solutions validated;
Security read where learner code runs, credentials are used, or Databricks resources are created or
removed; accessibility checks (axe, keyboard paths) for any screen; docs updated; evidence on the PR
(test output, `problems()` empty, a recorded run where relevant); Databricks verification where the
story needs it, or the PR says plainly that it has none; Second quality PASS; Release management merge.
Simulated behaviour is never reported as Databricks execution, and incomplete curriculum is never
called production-ready.
