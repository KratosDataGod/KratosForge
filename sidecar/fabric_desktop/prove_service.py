"""Prove this notebook: what the app calls.

A proof runs a notebook on this PC and then in Fabric as a controlled experiment (see
proof_runtime.py): both sides read every table at the version it had when the proof started,
and write to their own folder under ``Files/_fabric_desktop/proofs/<id>/`` instead of the
lakehouse's tables. Then every table either side wrote is verified under explicit rules
(verify.py), the shared lakehouse is checked for changes made meanwhile, and the result is
scored (scorecard.py) and saved as an immutable receipt (receipts.py).

The Fabric side runs a separate notebook item, ``<notebook>_proof``, in the notebook's
publish workspace, so a proof never changes the published notebook that schedules run.
"""

from __future__ import annotations

import datetime as dt
import json
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from . import depgraph, envprofile, proof, receipts, scorecard, verify, wizard
from .fabric_run import TERMINAL, fabric_ipynb
from .measure import RunMeter
from .onelake import DFS_HOST, FileStore, storage_options
from .proof_runtime import PROOF_ROOT
from .side_by_side import machine

NOTEBOOK_JOB = "RunNotebook"
POLL_SECONDS = 5
MAX_FABRIC_SECONDS = 6 * 3600
RUNNING = ("preparing", "pc", "fabric", "verifying")
PROOF_SUFFIX = "_proof"


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _parse(at: Any) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)


class _Cancelled(Exception):
    pass


class ProveMixin:
    root: Path

    # ---- stage 1: dependencies and receipts

    @property
    def receipts(self) -> receipts.ReceiptStore:
        return receipts.ReceiptStore(self.root / "receipts")

    @property
    def _observed_file(self) -> Path:
        return self.root / "fabric_environment.json"

    def _fabric_observed(self) -> dict[str, dict[str, Any]]:
        """Packages earlier runs in Fabric reported: {module or distribution: {"version", "seen_at", "python"}}."""
        try:
            return json.loads(self._observed_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def _remember_fabric_environment(self, env: dict[str, Any], modules: list[str]) -> None:
        seen = self._fabric_observed()
        packages = env.get("packages") or {}
        for m in modules:
            dist, _ = envprofile._distribution(m)
            version = packages.get((dist or m).lower()) or packages.get(m.lower())
            if version:
                seen[m] = {"version": version, "seen_at": _now(), "python": env.get("python")}
        self.root.mkdir(parents=True, exist_ok=True)
        self._observed_file.write_text(json.dumps(seen, indent=1, sort_keys=True), encoding="utf-8")

    def notebook_environment(self, name: str, cells: list[dict[str, Any]], lakehouse_ref: dict[str, str] | None = None,
                             compute: str | None = None, workspace: dict[str, str] | None = None) -> dict[str, Any]:
        """Which modules each cell imports and where each is available: this PC, Fabric. Takes
        what the notebook screen sends (notebookPayload)."""
        return envprofile.check(cells, self._fabric_observed())

    def notebook_dependencies(self, cells: list[dict[str, Any]]) -> dict[str, Any]:
        """How the cells depend on each other, read from their code (depgraph.py)."""
        return depgraph.graph(cells)

    def receipts_list(self, name: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        return self.receipts.list(name, limit)

    def receipt_get(self, id: str) -> dict[str, Any]:  # noqa: A002 - the app's parameter name
        return {"id": id, "receipt": self.receipts.get(id)}

    def receipt_export(self, id: str, format: str = "html", hide: list[str] | None = None) -> dict[str, Any]:  # noqa: A002
        """A readable report of a receipt, with the parts in ``hide`` left out (receipts.REDACTIONS)."""
        body = self.receipts.get(id)
        if format == "markdown":
            return {"format": "markdown", "text": receipts.to_markdown(body, id, hide or [])}
        if format == "html":
            return {"format": "html", "text": receipts.to_html(body, id, hide or [])}
        if format == "json":
            return {"format": "json", "text": receipts.canonical(body).decode("utf-8")}
        raise ValueError("format is markdown, html or json")

    def receipt_redactions(self) -> dict[str, str]:
        return dict(receipts.REDACTIONS)

    def receipt_save(self, id: str, format: str = "html", hide: list[str] | None = None) -> dict[str, Any]:  # noqa: A002
        """Export a receipt to the Downloads folder. A receipt file (json) is saved whole, so it
        can be checked against its id; the parts in ``hide`` are left out of reports only."""
        if format == "json" and hide:
            raise ValueError("A receipt file is saved whole so it can be checked against its id. "
                             "To leave parts out, export a report (HTML or Markdown).")
        out = self.receipt_export(id, format, hide)
        folder = Path.home() / "Downloads"
        folder.mkdir(parents=True, exist_ok=True)
        ext = {"html": "html", "markdown": "md", "json": "json"}[out["format"]]
        path = folder / f"receipt-{id[:12]}{'-redacted' if hide else ''}.{ext}"
        path.write_text(out["text"], encoding="utf-8")
        return {"path": str(path), "format": out["format"], "hidden": hide or []}

    # ---- the new notebook wizard and the proof contract

    def wizard_templates(self) -> dict[str, Any]:
        return wizard.templates()

    def wizard_preview(self, template: str, values: dict[str, Any]) -> dict[str, Any]:
        """The cells a template would write, without saving anything."""
        return wizard.build(template, values)

    def wizard_create(self, template: str, values: dict[str, Any], lakehouse_ref: dict[str, str] | None = None,
                      name: str | None = None) -> dict[str, Any]:
        """Create a notebook from a template, with its proof contract, and return it opened."""
        built = wizard.build(template, values)
        store = self.notebooks  # type: ignore[attr-defined]
        if name:
            if store.path(name).exists():
                raise ValueError(f"A notebook named {name!r} already exists.")
        else:
            name = store.new()["name"]
        store.save(name, built["cells"], lakehouse_ref)
        store.set_extra(name, "proof", built["proof"])
        return self.open_notebook(name)  # type: ignore[attr-defined]

    def prove_rules_get(self, name: str) -> dict[str, Any]:
        """The notebook's proof contract: {"default": rules, "tables": {table: rules}}."""
        return self.notebooks.extra(name, "proof") or {}  # type: ignore[attr-defined]

    def prove_rules_set(self, name: str, rules: dict[str, Any]) -> dict[str, Any]:
        rules = _normalized_rules(rules)
        self.notebooks.set_extra(name, "proof", rules or None)  # type: ignore[attr-defined]
        return rules

    # ---- stage 2: proofs

    @property
    def _proofs_file(self) -> Path:
        return self.root / "proofs.jsonl"

    def _proof_state(self) -> dict[str, dict[str, Any]]:
        if not hasattr(self, "_proof_runs"):
            self._proof_runs: dict[str, dict[str, Any]] = {}
        return self._proof_runs

    def _proofs_saved(self) -> list[dict[str, Any]]:
        try:
            lines = self._proofs_file.read_text(encoding="utf-8").splitlines()
        except OSError:
            return []
        return [json.loads(line) for line in lines if line.strip()]

    def _proof_save(self, record: dict[str, Any]) -> None:
        keep = {k: v for k, v in record.items() if not k.startswith("_")}
        keep["pc"] = {k: v for k, v in record["pc"].items() if k != "cells"}
        rows = [r for r in self._proofs_saved() if r.get("id") != record["id"]] + [keep]
        self.root.mkdir(parents=True, exist_ok=True)
        tmp = self._proofs_file.with_suffix(".tmp")
        tmp.write_text("".join(json.dumps(r, default=str) + "\n" for r in rows[-200:]), encoding="utf-8")
        tmp.replace(self._proofs_file)

    def proof_busy(self) -> bool:
        return any(r["state"] in RUNNING for r in self._proof_state().values())

    # Where a proof reads and writes. Tests replace these with local folders.

    def _proof_location(self, lakehouse_ref: dict[str, str]) -> tuple[str, Callable[[], dict[str, str]]]:
        root = f"abfss://{lakehouse_ref['workspace_id']}@{DFS_HOST}/{lakehouse_ref['lakehouse_id']}"
        return root, lambda: storage_options(self.auth.storage_token())  # type: ignore[attr-defined]

    def _proof_tables(self, lakehouse_ref: dict[str, str]) -> Any:
        """The lakehouse's tables and their versions (side_by_side_service.OneLakeTables)."""
        return self._sbs_tables(lakehouse_ref)  # type: ignore[attr-defined]

    def _proof_read_file(self, lakehouse_ref: dict[str, str], rel: str) -> bytes | None:
        return FileStore(self.auth.storage_token).read_bytes(  # type: ignore[attr-defined]
            lakehouse_ref["workspace_id"], lakehouse_ref["lakehouse_id"], rel)

    def _proof_delete_folder(self, lakehouse_ref: dict[str, str], rel: str) -> None:
        FileStore(self.auth.storage_token).delete(  # type: ignore[attr-defined]
            lakehouse_ref["workspace_id"], lakehouse_ref["lakehouse_id"], rel)

    def _proof_output(self, root: str, options: Callable[[], dict[str, str]], proof_id: str, side: str, key: str) -> Any:
        from deltalake import DeltaTable
        schema, table = key.split(".", 1)
        uri = f"{root}/{PROOF_ROOT}/{proof_id}/{side}/tables/{schema}/{table}"
        return DeltaTable(uri, storage_options=options()).to_pyarrow_dataset()

    # ---- what the app calls

    def prove_check(self, name: str, cells: list[dict[str, Any]], lakehouse_ref: dict[str, str] | None = None,
                    compute: str | None = None, workspace: dict[str, str] | None = None) -> dict[str, Any]:
        """The plan before a proof: what will happen, what blocks it, what it can't contain."""
        check = proof.check(cells, lakehouse_ref)
        env = envprofile.check(cells, self._fabric_observed())
        ws = None
        try:
            ws = self._publish_workspace(name, lakehouse_ref, workspace)  # type: ignore[attr-defined]
        except ValueError as exc:
            check["blockers"].append({"cell": None, "cell_id": None, "text": str(exc)})
            check["ok"] = False
        return {"check": check, "environment": env,
                "plan": {"pc": "Runs every code cell in a fresh kernel on this PC.",
                         "fabric": (f"Publishes a copy as {name}{PROOF_SUFFIX} in {ws['workspace_name'] or 'its workspace'} "
                                    "and runs it there. Your published notebook isn't changed." if ws else ""),
                         "inputs": "Every table is read at the version it has when the proof starts.",
                         "outputs": (f"Writes go to Files/{PROOF_ROOT.split('/', 1)[1]}/<proof>/ in "
                                     f"{(lakehouse_ref or {}).get('name', 'the lakehouse')}, not to its tables."),
                         "identity": "Both runs use your sign-in."}}

    def prove_start(self, name: str, cells: list[dict[str, Any]], lakehouse_ref: dict[str, str] | None = None,
                    compute: str | None = None, workspace: dict[str, str] | None = None,
                    rules: dict[str, Any] | None = None, parameters: dict[str, Any] | None = None) -> dict[str, Any]:
        """Start a proof; returns at once and prove_status follows it. ``rules`` are verification rules
        (verify.py), as {"default": {...}, "tables": {"dbo.t": {...}}}."""
        if self.proof_busy():
            raise RuntimeError("A proof is already running. Wait for it, or cancel it.")
        if any(r["state"] in ("local", "fabric", "checking") for r in self._sbs_state().values()):  # type: ignore[attr-defined]
            raise RuntimeError("A side-by-side run is going. Wait for it, or cancel it.")
        check = proof.check(cells, lakehouse_ref)
        if not check["ok"]:
            b = check["blockers"][0]
            where = f"Cell {b['cell']} " if b.get("cell") else ""
            raise ValueError(f"This notebook can't be proven yet: {where}{b['text']}")
        rules = _normalized_rules(rules or {})  # refuse bad rules now, not after two runs
        assert lakehouse_ref is not None
        saved = self.notebooks.save(name, cells, lakehouse_ref, compute, workspace)  # type: ignore[attr-defined]
        cells = [dict(c) | {"cell_id": cid} for c, cid in zip(cells, saved["cell_ids"])]
        ws = self._publish_workspace(name, lakehouse_ref, workspace)  # type: ignore[attr-defined]
        self.auth.storage_token()  # type: ignore[attr-defined]  # sign in now, while the app is in front
        self.auth.fabric_token()  # type: ignore[attr-defined]
        code = [i for i, c in enumerate(cells) if c.get("type") != "markdown" and (c.get("source") or "").strip()
                and not (c.get("meta") or {}).get("frozen")]
        record: dict[str, Any] = {
            "id": time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6], "name": name, "state": "preparing",
            "started_at": _now(), "finished_at": None, "message": "",
            "lakehouse": {k: lakehouse_ref.get(k, "") for k in ("name", "workspace_id", "workspace_name", "lakehouse_id")},
            "workspace_name": ws["workspace_name"], "machine": machine(), "check": check, "rules": rules,
            "parameters": parameters or {}, "code": receipts.code_revision(cells),
            "pc": {"status": "pending", "cell": None, "cells_total": len(code), "cells": {}},
            "fabric": {"status": "pending", "engine": "python"},
            "inputs": {}, "shared": {}, "tables": [], "verdict": None, "scorecard": None, "receipt_id": None,
            "_cancel": False}
        self._proof_state()[record["id"]] = record
        threading.Thread(target=self._proof_run, args=(record, cells, code, lakehouse_ref, ws), daemon=True,
                         name=f"proof-{record['id']}").start()
        return self.prove_status(record["id"])

    def prove_status(self, id: str) -> dict[str, Any]:  # noqa: A002
        record = self._proof_state().get(id) or next((r for r in self._proofs_saved() if r.get("id") == id), None)
        if record is None:
            raise LookupError(f"No proof {id}")
        return _copy(record)

    def prove_list(self, name: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        saved = {r["id"]: r for r in self._proofs_saved()}
        for r in self._proof_state().values():
            live = _copy(r)
            live["pc"].pop("cells", None)
            saved[r["id"]] = live
        rows = [r for r in saved.values() if not name or r.get("name") == name]
        return sorted(rows, key=lambda r: r.get("started_at") or "", reverse=True)[:limit]

    def prove_cancel(self, id: str) -> dict[str, Any]:  # noqa: A002
        record = self._proof_state().get(id)
        if record is None or record["state"] not in RUNNING:
            return {"cancelled": False}
        record["_cancel"] = True
        if record["state"] == "pc":
            self.kernel.interrupt()  # type: ignore[attr-defined]
        elif record["state"] == "fabric" and record["fabric"].get("job_id"):
            f = record["fabric"]
            try:
                self.fabric_jobs.cancel_job(f["workspace_id"], f["item_id"], f["job_id"])  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001
                record["message"] = f"Couldn't cancel the Fabric run: {exc}"
        return {"cancelled": True}

    def prove_verify(self, id: str, table: str, rules: dict[str, Any] | None = None, offset: int = 0,  # noqa: A002
                     save: bool = False) -> dict[str, Any]:
        """Verify one table of a finished proof again under other rules (a tolerance, key columns,
        ignored columns), without running anything again. ``offset`` pages through differing rows.
        With ``save``, the result is kept as its own receipt, pointing at the proof's receipt."""
        record = self.prove_status(id)
        if record["state"] in RUNNING:
            raise RuntimeError("The proof is still running.")
        row = next((t for t in record.get("tables") or [] if t["table"] == table), None)
        if row is None or not (row.get("pc_written") and row.get("fabric_written")):
            raise LookupError(f"{table} wasn't written by both sides of this proof.")
        ref = record["lakehouse"]
        root, options = self._proof_location(ref)
        out = verify.compare(self._proof_output(root, options, id, "pc", table),
                             self._proof_output(root, options, id, "fabric", table), rules, offset)
        out["table"] = table
        if save:
            parent = record.get("receipt_id")
            body = receipts.new_receipt("verification", notebook={"name": record["name"], "code": record.get("code")},
                                        proof={"id": id, "receipt": parent}, outputs=[out],
                                        verification={"verdict": "same" if out["result"] == "match" else "differs",
                                                      "text": f"{table}: {out['result']} under the rules given.",
                                                      "level": out["level"]},
                                        limitations=out.get("limitations") or [])
            out["receipt_id"] = self.receipts.put(body)
        return out

    def prove_delete_outputs(self, id: str) -> dict[str, Any]:  # noqa: A002
        """Delete what a proof wrote (its folder under Files/). The record and receipt stay."""
        record = self.prove_status(id)
        if record["state"] in RUNNING:
            raise RuntimeError("Cancel the proof first.")
        self._proof_delete_folder(record["lakehouse"], f"{PROOF_ROOT}/{id}")
        saved = self._proof_state().get(id) or record
        saved["outputs_deleted_at"] = _now()
        self._proof_save(saved)
        return {"deleted": f"{PROOF_ROOT}/{id}"}

    # ---- the proof, on its own thread

    def _proof_run(self, record: dict[str, Any], cells: list[dict[str, Any]], code: list[int],
                   lakehouse_ref: dict[str, str], ws: dict[str, str]) -> None:
        modules = sorted({m["module"] for c in cells if c.get("type") != "markdown"
                          for m in envprofile.imports(c.get("source") or "")["modules"]})
        try:
            tables = self._proof_tables(lakehouse_ref)
            schemas = self._schemas_enabled(lakehouse_ref["workspace_id"], lakehouse_ref["lakehouse_id"])  # type: ignore[attr-defined]
            pins = tables.versions()
            config = {"id": record["id"], "pins": pins, "flat": not schemas, "seed": 0}
            record["inputs"]["pinned_tables"] = len(pins)
            root, options = self._proof_location(lakehouse_ref)
            record["state"] = "pc"
            pc_log = self._proof_pc(record, cells, code, lakehouse_ref, config, root, options, modules)
            if record["_cancel"]:
                raise _Cancelled()
            record["state"] = "fabric"
            fabric_log = self._proof_fabric(record, cells, lakehouse_ref, ws, config, modules)
            if record["_cancel"]:
                raise _Cancelled()
            record["state"] = "verifying"
            after = tables.versions()
            self._proof_verify(record, pins, after, pc_log, fabric_log, root, options)
            final = "done"
        except _Cancelled:
            final, record["message"] = "cancelled", "Cancelled. Nothing was written to the lakehouse's tables."
        except Exception as exc:  # noqa: BLE001 - reported on the panel
            final, record["message"] = "failed", f"{type(exc).__name__}: {exc}"
        record["finished_at"] = _now()
        # The record and receipt say how it ended; the state changes last, so whoever sees a
        # finished proof also sees its scorecard and receipt.
        try:
            record["scorecard"] = scorecard.build(record, self._cost_settings())  # type: ignore[attr-defined]
            record["receipt_id"] = self.receipts.put(self._proof_receipt(record | {"state": final}))
        except Exception as exc:  # noqa: BLE001 - the record still says what happened
            record["message"] = (record["message"] + f" The receipt couldn't be saved: {exc}").strip()
        record["state"] = final
        self._proof_save(record)

    def _proof_pc(self, record: dict[str, Any], cells: list[dict[str, Any]], code: list[int],
                  lakehouse_ref: dict[str, str], config: dict[str, Any], root: str,
                  options: Callable[[], dict[str, str]], modules: list[str]) -> dict[str, Any]:
        """Every code cell in a fresh kernel with the proof runtime bound: pinned reads, sandboxed writes."""
        pc = record["pc"]
        pc["status"] = "running"
        self._attach(lakehouse_ref)  # type: ignore[attr-defined]
        kernel = self.kernel  # type: ignore[attr-defined]
        kernel.restart()
        kernel.notebook_name = record["name"]
        kernel.bind_proof(config, "pc", root, options)
        status = "ok"
        try:
            with RunMeter() as meter:
                for n, i in enumerate(code, 1):
                    if record["_cancel"]:
                        status = "cancelled"
                        break
                    pc["cell"] = n
                    kernel.start(cells[i]["source"])
                    snap = kernel.poll(-1, 0.0)
                    outputs = snap.get("outputs") or []
                    while snap["status"] == "running":
                        pc["cells"][str(i)] = {"status": "running", "seconds": snap["seconds"], "outputs": outputs}
                        snap = kernel.poll(snap["version"], 1.0)
                        if snap.get("outputs") is not None:
                            outputs = snap["outputs"]
                    snap = kernel.poll(-1, 0.0)
                    pc["cells"][str(i)] = {"status": snap["status"], "seconds": snap["seconds"],
                                           "outputs": snap.get("outputs") or outputs, "execution_count": snap.get("execution_count")}
                    if snap["status"] != "ok":
                        status = "cancelled" if record["_cancel"] else "failed"
                        pc["failed_cell"] = i + 1
                        break
        finally:
            kernel.unbind_proof()
        pc.update(meter.result())
        pc["status"] = status
        if getattr(kernel, "limit_hit", ""):
            pc["stopped_by"] = f"the {kernel.limit_hit} limit"
        if hasattr(kernel, "limits"):
            pc["limits"] = kernel.limits()
        log = kernel.finish_proof(modules)
        pc["log"] = log
        return log

    def _proof_fabric(self, record: dict[str, Any], cells: list[dict[str, Any]], lakehouse_ref: dict[str, str],
                      ws: dict[str, str], config: dict[str, Any], modules: list[str]) -> dict[str, Any] | None:
        """Publish the proof copy (its own item), run it on the workspace's capacity, read its log."""
        f = record["fabric"]
        f["status"] = "publishing"
        first, last = proof.fabric_cells(config, modules)
        started = time.monotonic()
        schemas = not config["flat"]
        ipynb = json.dumps(fabric_ipynb([first] + cells + [last], lakehouse_ref, schemas)).encode()
        item_name = f"{record['name']}{PROOF_SUFFIX}"
        item = self.fabric.save_notebook(ws["workspace_id"], item_name, ipynb)  # type: ignore[attr-defined]
        if not item.get("id"):
            item = next((n for n in self.fabric.list_notebooks(ws["workspace_id"])  # type: ignore[attr-defined]
                         if n["displayName"] == item_name), item)
        from .cloud_service import FABRIC_APP
        url = f"{FABRIC_APP}/groups/{ws['workspace_id']}/synapsenotebooks/{item.get('id')}?experience=fabric-developer"
        job_id = self.fabric_jobs.run_item_job(ws["workspace_id"], item["id"], NOTEBOOK_JOB)  # type: ignore[attr-defined]
        f.update(status="running", job_id=job_id, item_id=item["id"], item_name=item_name,
                 workspace_id=ws["workspace_id"], url=url, submitted_at=_now())
        self._log_fabric_run({"kind": "Notebook run", "name": item_name, "workspace_id": ws["workspace_id"],  # type: ignore[attr-defined]
                              "workspace_name": ws["workspace_name"], "engine": "python", "item_id": item["id"],
                              "job_id": job_id, "url": url, "proof": record["id"]})
        job: dict[str, Any] = {}
        while time.monotonic() - started < MAX_FABRIC_SECONDS:
            time.sleep(POLL_SECONDS)
            try:
                job = self.fabric.job_instance(ws["workspace_id"], item["id"], job_id)  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001 - a dropped connection: ask again
                f["message"] = f"Status not available: {exc}"
                continue
            f["job_status"] = job.get("status")
            f["elapsed"] = round(time.monotonic() - started)
            if job.get("status") in TERMINAL:
                break
        f["total_seconds"] = round(time.monotonic() - started, 2)
        a, b = _parse(job.get("startTimeUtc")), _parse(job.get("endTimeUtc"))
        f["job_seconds"] = round((b - a).total_seconds(), 2) if a and b else None
        f["status"] = {"Completed": "ok", "Cancelled": "cancelled"}.get(job.get("status", ""), "failed")
        reason = job.get("failureReason") or {}
        f["message"] = reason.get("message", "") if isinstance(reason, dict) else str(reason)
        data = None
        try:
            data = self._proof_read_file(lakehouse_ref, f"{PROOF_ROOT}/{record['id']}/fabric/log.json")
        except Exception as exc:  # noqa: BLE001
            f["log_problem"] = f"The Fabric run's log couldn't be read: {exc}"
        if not data:
            f.setdefault("log_problem", "The Fabric run didn't save its log (it stops at a failing cell).")
            return None
        log = json.loads(data)
        f["log"] = log
        f["run_seconds"] = log.get("seconds")
        if f.get("job_seconds") and f.get("run_seconds") is not None:
            f["start_seconds"] = round(max(f["job_seconds"] - f["run_seconds"], 0), 2)
        if log.get("environment"):
            self._remember_fabric_environment(log["environment"], modules)
        return log

    def _proof_verify(self, record: dict[str, Any], pins: dict[str, int], after: dict[str, int],
                      pc_log: dict[str, Any], fabric_log: dict[str, Any] | None, root: str,
                      options: Callable[[], dict[str, str]]) -> None:
        problems: list[str] = []
        # The shared lakehouse: nothing in a proof writes it, so any change came from elsewhere.
        changed = sorted(t for t in set(pins) | set(after) if pins.get(t) != after.get(t))
        record["shared"] = {"changed": changed, "unchanged": not changed}
        if changed:
            problems.append(f"The lakehouse's tables changed during the proof ({', '.join(changed[:5])}"
                            f"{'...' if len(changed) > 5 else ''}). Something else wrote them; the proof's own writes "
                            "go to its folder. Inputs were pinned, but run the proof again when nothing else is writing.")
        # Inputs: both sides read pinned versions; check they read the same files with the same content.
        fl = fabric_log or {}
        files_pc, files_fab = pc_log.get("files") or {}, fl.get("files") or {}
        mismatched = sorted(p for p in set(files_pc) & set(files_fab) if files_pc[p]["sha256"] != files_fab[p]["sha256"])
        if mismatched:
            problems.append(f"Files read on each side had different content: {', '.join(mismatched)}. They changed "
                            "between the runs, so the two runs didn't start from the same inputs.")
        record["inputs"] |= {
            "tables": [{"name": k, "version": v} for k, v in sorted((pc_log.get("reads") or {}).items())],
            "tables_fabric": [{"name": k, "version": v} for k, v in sorted((fl.get("reads") or {}).items())],
            "files": [{"path": p} | files_pc[p] for p in sorted(files_pc)],
            "files_fabric": [{"path": p} | files_fab[p] for p in sorted(files_fab)],
            "equivalent": not mismatched}
        env_pc, env_fab = pc_log.get("environment") or {}, fl.get("environment") or {}
        record["environment"] = {"pc": env_pc, "fabric": env_fab,
                                 "differences": envprofile.differences(env_pc, env_fab) if env_fab else []}
        # Outputs, table by table.
        rules = record.get("rules") or {}
        written_pc, written_fab = pc_log.get("writes") or {}, fl.get("writes") or {}
        rows = []
        for key in sorted(set(written_pc) | set(written_fab)):
            row: dict[str, Any] = {"table": key, "pc_written": key in written_pc, "fabric_written": key in written_fab,
                                   "modes": {"pc": written_pc.get(key), "fabric": written_fab.get(key)}}
            if not (key in written_pc and key in written_fab):
                row |= {"result": "only_pc" if key in written_pc else "only_fabric", "level": None,
                        "note": f"Written {'on this PC' if key in written_pc else 'in Fabric'} only."}
                rows.append(row)
                continue
            table_rules = (rules.get("default") or {}) | ((rules.get("tables") or {}).get(key) or {})
            try:
                res = verify.compare(self._proof_output(root, options, record["id"], "pc", key),
                                     self._proof_output(root, options, record["id"], "fabric", key), table_rules)
            except Exception as exc:  # noqa: BLE001 - one unreadable output shouldn't hide the rest
                row |= {"result": "error", "level": None, "note": f"Couldn't verify it: {exc}"}
            else:
                row |= res
            rows.append(row)
        record["tables"] = rows
        record["verdict"] = _verdict(record, rows, problems)

    def _proof_receipt(self, record: dict[str, Any]) -> dict[str, Any]:
        """The immutable record of a finished (or failed, or cancelled) proof."""
        pc, fab = record.get("pc") or {}, record.get("fabric") or {}
        identity = None
        try:
            identity = {"user": self.auth.signed_in_user(), "tenant": self.auth.signed_in_tenant()}  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - offline: the receipt says it isn't known
            identity = {"user": None, "tenant": None}
        side = lambda s, keys: {k: s.get(k) for k in keys if s.get(k) is not None}  # noqa: E731
        limits = list(record.get("check", {}).get("limits", []))
        limits += [f"Cell {w['cell']}: {w['text']}" for w in record.get("check", {}).get("warnings", [])]
        for t in record.get("tables") or []:
            limits += [f"{t['table']}: {x}" for x in t.get("limitations") or []]
        return receipts.new_receipt(
            "proof",
            notebook={"name": record["name"], "code": record.get("code"), "parameters": record.get("parameters") or {}},
            proof={"id": record["id"], "state": record["state"], "message": record.get("message", ""),
                   "started_at": record["started_at"], "finished_at": record.get("finished_at"),
                   "sandbox": f"{PROOF_ROOT}/{record['id']}"},
            identity=identity,
            lakehouse=record.get("lakehouse"),
            inputs=record.get("inputs") or {},
            environment=record.get("environment") or {},
            machine=record.get("machine"),
            runs={"pc": side(pc, ("status", "seconds", "cpu_seconds", "peak_memory_mb", "failed_cell", "measured",
                                    "stopped_by", "limits")),
                  "fabric": side(fab, ("status", "total_seconds", "job_seconds", "run_seconds", "start_seconds",
                                       "item_name", "job_id", "workspace_id", "message"))},
            outputs=[{k: v for k, v in t.items() if k != "rules"} for t in record.get("tables") or []],
            verification=(record.get("verdict") or {}) | {"rules": record.get("rules") or {},
                                                          "shared_unchanged": (record.get("shared") or {}).get("unchanged")},
            cost=(record.get("scorecard") or {}).get("cost"),
            scorecard=record.get("scorecard"),
            limitations=limits)


def _verdict(record: dict[str, Any], rows: list[dict[str, Any]], problems: list[str]) -> dict[str, Any]:
    failed = [w for w, s in (("this PC", record["pc"]["status"]), ("Fabric", record["fabric"]["status"])) if s != "ok"]
    if failed:
        stopped = record["pc"].get("stopped_by")
        why = f" (stopped by {stopped})" if stopped and "this PC" in failed else ""
        return {"verdict": "failed", "text": f"The run failed on {' and '.join(failed)}{why}, so the results can't be compared.",
                "problems": problems}
    if problems:
        return {"verdict": "inconclusive", "text": "Inconclusive: " + problems[0], "problems": problems}
    if not rows:
        return {"verdict": "nothing", "text": "Neither run wrote a table, so only the time is compared.", "problems": []}
    bad = [r for r in rows if r.get("result") != "match"]
    levels = sorted({r.get("level") for r in rows if r.get("level")})
    level = "fingerprint" if "fingerprint" in levels else "complete"
    n = len(rows)
    if not bad:
        return {"verdict": "same", "level": level, "problems": [],
                "text": f"Same results in both places ({n} table{'s' if n != 1 else ''}, "
                        f"{'every row compared' if level == 'complete' else 'some by checksum only'})."}
    return {"verdict": "differs", "level": level, "problems": [],
            "text": f"Results differ in {len(bad)} of {n} table{'s' if n != 1 else ''}."}


def _table_key(name: str) -> str:
    """A table as a proof names it: schema.table in lower case, dbo when no schema is given."""
    name = str(name).strip()
    return (name if "." in name else f"dbo.{name}").lower()


def _normalized_rules(rules: dict[str, Any]) -> dict[str, Any]:
    """Proof rules with each table named as a proof names it, checked before anything runs."""
    unknown = set(rules) - {"default", "tables"}
    if unknown:
        raise ValueError(f"Proof rules have a default and tables, not {', '.join(sorted(unknown))}.")
    default = rules.get("default") or {}
    verify.rules(default)
    tables = {}
    for t, r in (rules.get("tables") or {}).items():
        verify.rules(default | (r or {}))
        tables[_table_key(t)] = r or {}
    out: dict[str, Any] = {}
    if default:
        out["default"] = default
    if tables:
        out["tables"] = tables
    return out


def _copy(record: dict[str, Any]) -> dict[str, Any]:
    """A snapshot of a record the proof's thread may be changing right now."""
    for _ in range(20):
        try:
            return json.loads(json.dumps({k: v for k, v in record.items() if not k.startswith("_")}, default=str))
        except RuntimeError:
            time.sleep(0.01)
    raise RuntimeError("The proof is busy; try again")
