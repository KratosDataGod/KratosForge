"""Validate notebook: what the app calls (validate.py has the checks).

``notebook_validate_check`` reads the cells and answers at once. ``notebook_validate_start`` also
runs every code cell from the top in a fresh kernel process of its own, so the person's kernel
and its variables are left alone, and returns at once; ``notebook_validate_status`` follows it.

The clean run reads the lakehouse's tables at the versions they have when it starts and, with the
proof runtime bound, writes tables to ``Files/_fabric_desktop/proofs/validate-<id>/pc/`` instead of
the lakehouse's own tables; that folder is deleted when the run ends. Writes the runtime can't
redirect (a direct deltalake or notebookutils.fs write) stop the run before it starts; what it
can't contain at all (network calls, files on this PC) needs the person's go first. It starts no
Fabric job; reading and writing OneLake still uses some capacity.

Finished validations are kept on this PC (``validations.json``, the newest MAX_KEPT), so Save
report and the panel can show the last result after a restart.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from . import cleanup, envprofile, lakehouse, notebook_cells, receipts, redact, validate
from .kernel_proc import ProcessKernel
from .proof_runtime import PROOF_ROOT

METHODS = {"notebook_validate_check", "notebook_validate_start", "notebook_validate_status",
           "notebook_validate_cancel", "notebook_validate_list", "notebook_validate_save",
           "notebook_cleanup_plan", "notebook_cleanup_apply"}

_log = logging.getLogger(__name__)

MAX_KEPT = 100
LIMIT_NAMES = {"time": "the cell time limit", "memory": "the notebook memory limit"}
ERROR_CHARS = 4000     # of a failed cell's traceback kept as evidence
_ID = re.compile(r"^validate-\d{8}-\d{6}-[0-9a-f]{6}$")


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


class _CleanKernel(ProcessKernel):
    """A fresh kernel process for one clean run. It is told its lakehouse directly instead of
    following the engine's attached one, so it doesn't change what the person's cells run against,
    and its writes aren't added to the notebook writes Monitor shows."""

    def __init__(self, ref: dict[str, str] | None, schemas: bool, work_dir: Path, **kw: Any):
        super().__init__(**kw)
        self._context = None if not ref else {
            "workspace_id": ref["workspace_id"], "lakehouse_id": ref["lakehouse_id"], "name": ref.get("name", ""),
            "workspace_name": ref.get("workspace_name") or "", "work_dir": str(work_dir), "history_file": None,
            "schemas_enabled": schemas}

    def _sync_lakehouse(self) -> None:
        key = (json.dumps(self._context, sort_keys=True), lakehouse.write_guard is not None)
        if key != self._attached:
            self._request("attach", context=self._context, guard=key[1])
            self._attached = key


class ValidateMixin:
    # ---- state

    def _validations(self) -> dict[str, dict[str, Any]]:
        if not hasattr(self, "_validate_state"):
            self._validate_state: dict[str, dict[str, Any]] = {}
            self._validate_lock = threading.Lock()
        return self._validate_state

    def _validations_file(self) -> Path:
        return Path(self.root) / "validations.json"  # type: ignore[attr-defined]

    def _validations_saved(self) -> list[dict[str, Any]]:
        try:
            return json.loads(self._validations_file().read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []

    def _validation_keep(self, record: dict[str, Any]) -> None:
        rows = [r for r in self._validations_saved() if r.get("id") != record["id"]] + [_public(record)]
        path = self._validations_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rows[-MAX_KEPT:], indent=1), encoding="utf-8")
        tmp.replace(path)

    def _validate_running(self) -> dict[str, Any] | None:
        return next((r for r in self._validations().values() if r["state"] == "running"), None)

    # ---- what the app calls

    def notebook_validate_check(self, name: str, cells: list[dict[str, Any]], lakehouse_ref: dict[str, str] | None = None,
                                runs_on: str | None = None) -> dict[str, Any]:
        """The code checks, without running anything. ``runs_on``: "pc" (or "local"), "fabric", or None when not known."""
        out = validate.check(cells, lakehouse_ref, _runs_on(runs_on), self._fabric_observed(),  # type: ignore[attr-defined]
                             **self._checks(name))  # type: ignore[attr-defined]
        return {"name": name, "code": receipts.code_revision(cells), "verdict": validate.verdict(out["findings"], None)} | out

    def notebook_cleanup_plan(self, cells: list[dict[str, Any]]) -> dict[str, Any]:
        """Clean up's tidy-ups and suggestions (cleanup.py). Reads the code only."""
        return {"code": receipts.code_revision(cells)} | cleanup.plan(cells)

    def notebook_cleanup_apply(self, cells: list[dict[str, Any]], ids: list[str]) -> dict[str, Any]:
        """The cells after the chosen tidy-ups. Nothing is saved: the app puts them in the notebook."""
        return cleanup.apply(cells, ids)

    def notebook_validate_start(self, name: str, cells: list[dict[str, Any]], lakehouse_ref: dict[str, str] | None = None,
                                runs_on: str | None = None, allow_uncontained: bool = False) -> dict[str, Any]:
        """Start a clean run. Refused while another runs, when a cell would write outside the
        sandbox, and, until ``allow_uncontained``, when a cell does something the run can't contain."""
        self._validations()
        with self._validate_lock:
            if self._validate_running():
                raise RuntimeError("A validation is already running. Wait for it, or cancel it.")
            plan = validate.run_plan(cells, lakehouse_ref, spark=True)
            if not plan["ok"]:
                b = plan["blockers"][0]
                raise ValueError(f"A clean run can't contain cell {b['cell']}: it {b['text']}. "
                                 "Change that cell to read and write through the lakehouse module, or run it yourself.")
            if plan["uncontained"] and not allow_uncontained:
                u = plan["uncontained"][0]
                raise PermissionError(f"Confirm to continue. Cell {u['cell']}: {u['text']}")
            static = validate.check(cells, lakehouse_ref, _runs_on(runs_on), self._fabric_observed(),  # type: ignore[attr-defined]
                                    **self._checks(name))  # type: ignore[attr-defined]
            code = [(n, c) for n, c in enumerate(cells, 1) if notebook_cells.runs(c, skip_fabric_step=True)]
            rid = "validate-" + time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
            record: dict[str, Any] = {
                "id": rid, "name": name,
                "state": "running", "started_at": _now(), "finished_at": None, "message": "",
                "lakehouse": {k: (lakehouse_ref or {}).get(k, "") for k in ("name", "workspace_id", "workspace_name", "lakehouse_id")},
                "runs_on": _runs_on(runs_on), "code": receipts.code_revision(cells),
                "cells_total": len(code), "cell": None, "cells": [],
                # Every key is here from the start: status reads the record while the run fills it in.
                "run": {"status": "running", "seconds": None, "failed_cell": None, "error": None, "stopped_by": None,
                        "sandbox_tables": None},
                "findings": static["findings"], "counts": static["counts"], "tables": static["tables"],
                "uncontained": plan["uncontained"], "limits": validate.LIMITS, "verdict": None, "summary": "",
                "sandbox": f"{PROOF_ROOT}/{rid}/pc" if lakehouse_ref else "", "environment": None,
                "_cancel": False, "_kernel": None}
            self._validations()[record["id"]] = record
        if lakehouse_ref:
            self.auth.storage_token()  # type: ignore[attr-defined]  # sign in now, while the app is in front
        threading.Thread(target=self._validate_run, args=(record, cells, code, lakehouse_ref), daemon=True,
                         name=f"validate-{record['id']}").start()
        return self.notebook_validate_status(record["id"])

    def notebook_validate_status(self, id: str) -> dict[str, Any]:  # noqa: A002 - the app's parameter name
        record = self._validations().get(id) or next((r for r in self._validations_saved() if r.get("id") == id), None)
        if record is None:
            raise LookupError(f"No validation {id}")
        return _public(record)

    def notebook_validate_cancel(self, id: str) -> dict[str, Any]:  # noqa: A002
        record = self._validations().get(id)
        if record is None or record["state"] != "running":
            return {"cancelled": False}
        record["_cancel"] = True
        kernel = record.get("_kernel")
        if kernel is not None:
            kernel.stop()  # its own process: nothing in it is worth keeping
        return {"cancelled": True}

    def notebook_validate_list(self, name: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        """Validations, newest first, without their findings (notebook_validate_status has them)."""
        rows = {r["id"]: r for r in self._validations_saved()}
        rows.update({r["id"]: _public(r) for r in self._validations().values()})
        out = [{k: v for k, v in r.items() if k not in ("findings", "cells", "uncontained")}
               for r in rows.values() if not name or r.get("name") == name]
        return sorted(out, key=lambda r: r.get("started_at") or "", reverse=True)[:limit]

    def notebook_validate_save(self, id: str, format: str = "markdown") -> dict[str, Any]:  # noqa: A002
        """Save a finished validation's report to Downloads, as Markdown or JSON, as a new file."""
        record = self.notebook_validate_status(id)
        if record["state"] == "running":
            raise RuntimeError("The validation is still running.")
        if format not in ("markdown", "json"):
            raise ValueError("format is markdown or json")
        text = validate.to_markdown(record) if format == "markdown" else json.dumps(record, indent=1)
        folder = Path.home() / "Downloads"
        folder.mkdir(parents=True, exist_ok=True)
        stem = re.sub(r"[^\w.-]+", "-", f"validate-{record['name']}-{record['id'][9:24]}").strip("-")
        ext = "md" if format == "markdown" else "json"
        path, n = folder / f"{stem}.{ext}", 2
        while n <= 1000:
            try:  # created exclusively: a file already there (even one made a moment ago) is never replaced
                with path.open("x", encoding="utf-8") as f:
                    f.write(text)
                return {"path": str(path), "format": format}
            except FileExistsError:
                path, n = folder / f"{stem}-{n}.{ext}", n + 1
        raise ValueError("Couldn't find a free file name in Downloads for the report.")

    # ---- the clean run

    def _validate_kernel(self, lakehouse_ref: dict[str, str] | None) -> ProcessKernel:
        from .service import _apply_limits
        schemas = bool(lakehouse_ref) and self._schemas_enabled(lakehouse_ref["workspace_id"], lakehouse_ref["lakehouse_id"])  # type: ignore[attr-defined]
        kernel = _CleanKernel(lakehouse_ref, schemas, Path(self.root),  # type: ignore[attr-defined]
                              token=self._token_for,  # type: ignore[attr-defined]
                              load_notebook=lambda nb: self.notebooks.load(nb)["cells"],  # type: ignore[attr-defined]
                              variable_libraries=self._notebook_variables)  # type: ignore[attr-defined]
        _apply_limits(kernel)
        return kernel

    def _validate_run(self, record: dict[str, Any], cells: list[dict[str, Any]], code: list[tuple[int, dict[str, Any]]],
                      lakehouse_ref: dict[str, str] | None) -> None:
        run = record["run"]
        kernel = None
        bound = False
        try:
            kernel = self._validate_kernel(lakehouse_ref)
            record["_kernel"] = kernel
            kernel.notebook_name = record["name"]
            kernel.resources_dir = self._resources_dir(record["name"])  # type: ignore[attr-defined]
            kernel.onelake_writes = self._onelake_writes(record["name"])  # type: ignore[attr-defined]
            if lakehouse_ref:
                schemas = self._schemas_enabled(lakehouse_ref["workspace_id"], lakehouse_ref["lakehouse_id"])  # type: ignore[attr-defined]
                pins = self._proof_tables(lakehouse_ref).versions()  # type: ignore[attr-defined]
                root, options = self._proof_location(lakehouse_ref)  # type: ignore[attr-defined]
                kernel.bind_proof({"id": record["id"], "pins": pins, "flat": not schemas, "seed": 0}, "pc", root, options)
                bound = True
            started = time.monotonic()
            status = "ok"
            for n, c in code:
                if record["_cancel"]:
                    status = "cancelled"
                    break
                record["cell"] = n
                row = {"cell": n, "cell_id": c.get("cell_id"), "status": "running", "seconds": 0}
                record["cells"].append(row)
                snap = kernel.start(c["source"])
                version, outputs = -1, snap.get("outputs") or []
                while snap["status"] == "running":
                    if record["_cancel"]:
                        # Cancel may come while the process is still starting, before there is one to stop.
                        if kernel.alive():
                            kernel.stop()
                        snap = {"status": "cancelled", "seconds": row["seconds"]}
                        break
                    snap = kernel.poll(version, 0.5)
                    version = snap.get("version", version)
                    row["seconds"] = snap.get("seconds", row["seconds"])
                    if snap.get("outputs") is not None:
                        outputs = snap["outputs"]
                row.update(status=snap["status"], seconds=snap.get("seconds"))
                if snap["status"] != "ok":
                    status = "cancelled" if record["_cancel"] else "failed"
                    if status == "failed":
                        run["failed_cell"] = n
                        run["error"] = _error(snap.get("outputs") or outputs)
                        if kernel.limit_hit:
                            run["stopped_by"] = LIMIT_NAMES.get(kernel.limit_hit, f"the {kernel.limit_hit} limit")
                    break
            run.update(status=status, seconds=round(time.monotonic() - started, 3))
            if bound:
                run["sandbox_tables"] = _sandbox_tables(kernel)
        except Exception as exc:  # noqa: BLE001 - reported on the panel
            run["status"] = "cancelled" if record["_cancel"] else "error"
            record["message"] = redact.secrets(f"{type(exc).__name__}: {exc}")
        finally:
            if kernel is not None:
                kernel.close()
            record["_kernel"] = None
        # The sandbox is this run's own folder; it goes when the run ends. Its tables were checked
        # by running; the report lists them. None means the kernel couldn't say (it ended).
        if bound and run.get("sandbox_tables") != [] and _ID.match(record["id"]):
            try:
                self._proof_delete_folder(lakehouse_ref, f"{PROOF_ROOT}/{record['id']}")  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001 - the result stands; say what may be left behind
                record["message"] = (record["message"] + f" The sandbox folder {PROOF_ROOT}/{record['id']} couldn't be "
                                     f"deleted ({type(exc).__name__}); if it's there, delete it in Fabric.").strip()
        try:
            self._validate_finish(record, cells)
        except Exception as exc:  # noqa: BLE001 - a validation must never stay "running"
            record["message"] = (record["message"] + f" The result couldn't be put together: {type(exc).__name__}: {exc}").strip()
            record["verdict"], record["finished_at"], record["state"] = None, _now(), "failed"

    def _validate_finish(self, record: dict[str, Any], cells: list[dict[str, Any]]) -> None:
        run = record["run"]
        findings = record["findings"]
        findings += validate.run_findings(cells, run, findings)
        if run["status"] == "error":
            findings.append(validate.finding("error", "run_error", "The clean run couldn't finish",
                                             record["message"], "Check you're signed in, then validate again.", found_by="run"))
        record["findings"] = validate._sorted(findings)
        record["counts"] = validate.counts(findings)
        record["verdict"] = None if run["status"] == "cancelled" else validate.verdict(findings, run)
        ran = sum(1 for c in record["cells"] if c["status"] == "ok")
        record["summary"] = {
            "ok": f"Every code cell ran from the top in a fresh kernel on this PC, in {run['seconds'] or 0:.1f} s.",
            "failed": f"The clean run stopped at cell {run.get('failed_cell')} after {ran} cell{'s' if ran != 1 else ''} succeeded.",
            "cancelled": "Cancelled. Nothing was written to the lakehouse's tables.",
            "error": "The clean run couldn't finish.",
        }.get(run["status"], "")
        if run["status"] in ("ok", "failed") and (skipped := notebook_cells.skipped_step(cells)):
            record["summary"] += " " + skipped
        record["cell"] = None
        record["finished_at"] = _now()
        modules = sorted({m["module"] for c in cells if c.get("type") != "markdown"
                          for m in envprofile.imports(c.get("source") or "")["modules"]})
        record["environment"] = envprofile.runtime(modules)
        try:
            self._validation_keep(record)
        except OSError as exc:
            record["message"] = (record["message"] + f" The result couldn't be saved on this PC: {exc}").strip()
        try:
            record["earned"] = self._progress_observe(record)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - the profile never changes a validation's result
            _log.warning("The validation couldn't be added to the profile: %s", redact.describe(exc))
        record["state"] = "cancelled" if run["status"] == "cancelled" else "done"  # last: whoever sees done sees it all


def _runs_on(value: str | None) -> str | None:
    """"pc" or "fabric"; a notebook's Runs on is saved as "local", "fabric" or "default" (not known here)."""
    return {"pc": "pc", "local": "pc", "fabric": "fabric"}.get(value or "")


def _public(record: dict[str, Any]) -> dict[str, Any]:
    return json.loads(json.dumps({k: v for k, v in record.items() if not k.startswith("_")}))


def _sandbox_tables(kernel: ProcessKernel) -> list[str] | None:
    """The tables the run wrote to its sandbox, or None when its process has ended and can't say."""
    if not kernel.alive():
        return None
    return sorted((kernel.proof_log() or {}).get("writes") or {})


def _error(outputs: list[dict[str, Any]]) -> dict[str, Any]:
    """The failed cell's error, secrets removed, the traceback cut to ERROR_CHARS."""
    err = next((o for o in reversed(outputs) if o.get("type") == "error"), None) or {}
    trace = err.get("traceback") or ""
    if isinstance(trace, list):
        trace = "\n".join(trace)
    return {"ename": err.get("ename") or "", "evalue": redact.secrets(str(err.get("evalue") or "")),
            "traceback": redact.secrets(str(trace))[-ERROR_CHARS:]}
