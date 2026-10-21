"""Where to run a notebook: on this PC or in Fabric, and the evidence for it (FINOPS-005).

Reports only. It never changes a notebook's "Runs on", never starts a run, and never turns time
on this PC into CU. The evidence it weighs:

* **Compatibility**: which cells can't run on this PC or in Fabric (``fabric_run.compute_check``).
* **Proofs**: the newest Prove receipt for the notebook's current code. A receipt counts only for
  the same code revision; if the notebook changed since, its results don't say anything about it.
  The verification level is cited as recorded: "complete" compared every row, "fingerprint"
  compared some tables by checksum only, and isn't read as every row matching.
* **Fabric job CU** of the proof's Fabric run, with its classification (measured or estimated): what
  a run here would leave out of Fabric job CU. It is capacity use, not money saved.

* **Side-by-side runs** of the current code (their ``code_revision``), when there is no proof: they
  compare each table's row count, columns and a checksum of every row, which is cited as such. Runs
  that didn't record their code, or ran other code, are listed as context only.

Pure: no I/O. The caller (``advisor_service``) gathers the receipts and the compatibility check.
"""

from __future__ import annotations

from typing import Any

from . import measurement as ms

PC, FABRIC, EITHER, UNKNOWN = "pc", "fabric", "either", "unknown"
PLACES = (PC, FABRIC, EITHER, UNKNOWN)
_HEADLINE = {PC: "Run it on this PC", FABRIC: "Keep it in Fabric", EITHER: "Either place works",
             UNKNOWN: "Not enough evidence to choose"}
_LEVEL = {"complete": "every row compared", "fingerprint": "some tables compared by checksum only, not row by row"}
CAVEAT = ("Advice only: nothing changes until you change Runs on. A run on this PC still reads and writes OneLake, "
          "which uses capacity.")


def _reason(kind: str, text: str, **evidence: Any) -> dict[str, Any]:
    return {"kind": kind, "text": text, "evidence": evidence}


def _cells(reasons: list[dict[str, Any]]) -> str:
    return "; ".join((f"cell {r['cell']}: " if r.get("cell") else "") + r["text"] for r in reasons[:3]) + (
        f"; and {len(reasons) - 3} more" if len(reasons) > 3 else "")


def _cu_reason(cost: dict[str, Any]) -> dict[str, Any] | None:
    """The proof's Fabric job CU, as what a run here leaves out of Fabric job CU."""
    m = ((cost or {}).get("fabric") or {}).get("measurement")
    if not m:
        return None
    if m.get("classification") == ms.UNKNOWN or m.get("value") is None:
        return _reason("cu", f"The Fabric run's job CU isn't known ({m.get('reason') or 'not recorded'}).", measurement=m)
    cu_h = ms.convert(m["value"], m["unit"], ms.CU_HOURS)
    label = m.get("label") or m.get("classification")
    return _reason("cu", f"The proof's Fabric run used {cu_h:.3g} CU hours of Fabric job CU ({label.lower()}); a run "
                         "on this PC starts no Fabric job. This is capacity use, not money saved.", measurement=m)


def _sbs(c: dict[str, Any]) -> tuple[bool | None, int, bool]:
    """A side-by-side run's verdict ("same"), how many tables it compared, and whether a side failed."""
    failed = any((c.get(side) or {}).get("status") not in (None, "ok") for side in ("local", "fabric"))
    return (c.get("verdict") or {}).get("same"), len(c.get("tables") or []), failed


def advise(name: str, revision: str, compat: dict[str, Any], proofs: list[dict[str, Any]],
           comparisons: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """Where to run notebook ``name`` (at code ``revision``), with the evidence for it.

    ``compat``: ``fabric_run.compute_check``'s result. ``proofs``: Prove receipts (``{"id", "receipt"}``),
    newest first. ``comparisons``: side-by-side records, newest first."""
    reasons: list[dict[str, Any]] = []
    local_ok, fabric_ok = compat["local"]["ok"], compat["fabric"]["ok"]
    if not local_ok:
        reasons.append(_reason("compatibility", "Can't run on this PC as it is: " + _cells(compat["local"]["reasons"]),
                               side="pc", cells=compat["local"]["reasons"]))
    if not fabric_ok:
        reasons.append(_reason("compatibility", "Can't run in Fabric as it is: " + _cells(compat["fabric"]["reasons"]),
                               side="fabric", cells=compat["fabric"]["reasons"]))
    if local_ok and fabric_ok:
        reasons.append(_reason("compatibility", "No cell stops it on this PC or in Fabric.", side="both"))

    proofs = [p for p in proofs if (p.get("receipt") or {}).get("kind") == "proof"]
    same = [p for p in proofs if ((p["receipt"].get("notebook") or {}).get("code") or {}).get("revision") == revision]
    proof = same[0] if same else None
    next_step = None
    if proof:
        r = proof["receipt"]
        v = r.get("verification") or {}
        level = v.get("level")
        reasons.append(_reason("proof", (v.get("text") or v.get("verdict") or "Proved.")
                               + (f" Verification: {_LEVEL.get(level, level)}." if level else ""),
                               receipt_id=proof["id"], verdict=v.get("verdict"), level=level,
                               created_at=r.get("created_at")))
        faster = ((r.get("scorecard") or {}).get("time") or {}).get("faster")
        if faster:
            reasons.append(_reason("time", faster + " (measured in the proof).", receipt_id=proof["id"]))
        if cu := _cu_reason(r.get("cost") or {}):
            reasons.append(cu | {"evidence": cu["evidence"] | {"receipt_id": proof["id"]}})
    elif proofs:
        reasons.append(_reason("proof", "Unknown: the notebook changed since it was last compared, so that proof "
                                        "doesn't cover the current code.", receipt_id=proofs[0]["id"],
                               created_at=proofs[0]["receipt"].get("created_at")))
        next_step = "Prove the notebook again to compare the current code on this PC and in Fabric."
    else:
        reasons.append(_reason("proof", "No proof yet: Prove hasn't run this notebook on this PC and in Fabric."))
        next_step = "Prove the notebook to compare its results and time on this PC and in Fabric."
    done = [c for c in comparisons or [] if c.get("state") == "done"]
    sbs = next((c for c in done if c.get("code_revision") == revision), None) if not proof else None
    if sbs:
        same, n, sbs_failed = _sbs(sbs)
        what = ("not comparable, as a run failed" if sbs_failed else "the same" if same else "different" if same is False
                else "not compared, as neither run wrote a table")
        reasons.append(_reason("comparison", f"A side-by-side run of the current code on {str(sbs.get('started_at') or '')[:10]} "
                                             f"found the results {what}"
                               + (f" ({n} table{'s' if n != 1 else ''} compared by row count, columns and a checksum of "
                                  "every row, not row by row)." if n and not sbs_failed else "."),
                               comparison_id=sbs.get("id"), same=same, method="row count, columns and row checksum"))
        if faster := (sbs.get("speed") or {}).get("end_to_end"):
            reasons.append(_reason("time", faster + " (measured in the side-by-side run).", comparison_id=sbs.get("id")))
        next_step = "Prove the notebook for a row-by-row comparison and its Fabric job CU."
    elif done and not proof:
        c = done[0]
        why = ("ran other code" if c.get("code_revision") else "didn't record which code it ran")
        reasons.append(_reason("context", "A side-by-side run on " + str(c.get("started_at") or "")[:10] + " said: "
                               + ((c.get("verdict") or {}).get("text") or "no verdict") + f" It {why}, so it isn't "
                               "counted for the current code.", comparison_id=c.get("id")))

    verdict = ((proof or {}).get("receipt", {}).get("verification") or {}).get("verdict")
    if sbs and verdict is None:
        same, _, sbs_failed = _sbs(sbs)
        verdict = "failed-sbs" if sbs_failed else "same" if same else "nothing" if same is None else "differs"
    if not local_ok and not fabric_ok:
        place = UNKNOWN
        next_step = "Change the cells listed so it can run in at least one place."
    elif not local_ok:
        place = FABRIC
    elif not fabric_ok or verdict == "same":
        place = PC
    elif verdict == "differs":
        place = FABRIC
        next_step = "Look at the tables that differ before running it on this PC."
    elif verdict == "nothing":
        place = EITHER
        reasons.append(_reason("scope", "Only tables are compared. Files written under Files/ aren't, so check them "
                                        "yourself if the notebook writes any."))
    elif verdict == "failed-sbs":
        place = UNKNOWN
    elif verdict in ("failed", "inconclusive"):
        pc_failed = (((proof or {}).get("receipt", {}).get("runs") or {}).get("pc") or {}).get("status") not in (None, "ok")
        place = FABRIC if verdict == "failed" and pc_failed else UNKNOWN
        next_step = next_step or "Look at the proof's result, then prove it again."
    else:
        place = UNKNOWN
    return {"notebook": name, "revision": revision, "place": place, "headline": _HEADLINE[place], "reasons": reasons,
            "next_step": next_step, "enforced": False, "caveat": CAVEAT}
