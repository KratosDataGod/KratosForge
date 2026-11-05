# Where to run a notebook (Preview)

KratosForge can say whether a notebook is better run **on this PC** or **in Fabric**, and show the evidence behind that. This is FINOPS-005's advisor. It gives advice only: it never changes a notebook's **Runs on**, never starts a run, and never works out Fabric CU from the time a run took on this PC.

The advice is available to tools connected through [MCP or the local API](../ai/README.md) as `fabric_desktop_placement_advice` (Preview, read-only). It has no screen in the app yet: Prove will show it on its screens.

## The answer

| Place | Means |
| --- | --- |
| **Run it on this PC** (`pc`) | It can run here, and the newest proof (or, without one, side-by-side run) of its current code got the same results on this PC and in Fabric. Or a cell needs this PC (for example, a local file path). |
| **Keep it in Fabric** (`fabric`) | A cell needs Fabric, the proof's (or side-by-side run's) results differ, or the run on this PC failed in the proof. |
| **Either place works** (`either`) | It runs in both places and neither run of the proof (or side-by-side run) wrote a table, so only time was compared. Files written under Files/ aren't compared. |
| **Not enough evidence** (`unknown`) | Neither a proof nor a side-by-side run covers its current code, or the comparison was inconclusive or a side failed. The answer says what to do next. |

## The evidence

Every reason cites what it used:

- **Compatibility.** Which cells can't run on this PC or in Fabric, from the same check the notebook screen runs.
- **The newest proof of the current code.** A [proof](../prove/how-proofs-work.md) counts only if it ran the notebook's current code (its code revision). If the notebook changed since, the proof says nothing about it, and the answer is "changed since it was last compared".
- **How the results were compared, as recorded.** "Every row compared" means a complete comparison. "Some tables compared by checksum only" is reported as such, never as every row matching.
- **Time** measured in the proof, on this PC and in Fabric.
- **Fabric job CU** of the proof's Fabric run, with its classification (Measured or Estimated; see [Capacity and cost evidence](cost-evidence.md)). That is the Fabric job CU a run on this PC leaves out. It is capacity use, not money saved, and OneLake reads and writes from this PC still use capacity.

- **A side-by-side run of the current code**, when there is no proof. It compares each table's row count, columns and a checksum of every row, and the advice says so: it is not a row-by-row comparison. Side-by-side runs of other code, or older ones that didn't record which code they ran, are shown as context only.

## Permissions

None beyond what the app already has. The advice reads the notebook, its proof receipts and side-by-side records on this PC. It calls nothing in Fabric.
