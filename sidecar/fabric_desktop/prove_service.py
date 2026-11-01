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

import base64
import datetime as dt
import hashlib
import json
import logging
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable

from . import app_settings, depgraph, envprofile, jsonfile, notebook_cells, proof, receipts, redact, replay, scorecard, verify, wizard
from .notebook import check_watches, leaf_name
from .fabric_run import TERMINAL, fabric_ipynb
from .kernel_proc import ProcessKernel
from .measure import RunMeter
from .onelake import DFS_HOST, FileStore, TableRef, check_name, read_storage_options, storage_options
from .proof_runtime import PROOF_ROOT
from .redact import describe
from .side_by_side import machine

NOTEBOOK_JOB = "RunNotebook"
_GUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
POLL_SECONDS = 5
MAX_FABRIC_SECONDS = 6 * 3600
RUNNING = ("preparing", "pc", "fabric", "verifying")
PROOF_SUFFIX = "_proof"

_log = logging.getLogger(__name__)
_PROOFS_SAVING = threading.Lock()  # proofs.jsonl is read, changed and written whole


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _parse(at: Any) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(str(at).replace("Z", "+00:00"))
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=dt.timezone.utc)



SAMPLE_SIGNED_OUT = ("Sign in to read the sample from OneLake, or save the replay bundle without a sample. "
                     "Nothing was saved.")
SAMPLE_UNREACHABLE = ("Couldn't reach OneLake to read the sample. Save the replay bundle without a sample, or try "
                      "again when you're online. Nothing was saved.")

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
        jsonfile.write_text(self._observed_file, json.dumps(seen, indent=1, sort_keys=True))

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
        base = f"receipt-{id[:12]}{'-redacted' if hide else ''}"
        for n in range(1, 1000):  # never replaces a file: a name that is taken gets a number (HD-038)
            path = folder / (f"{base}.{ext}" if n == 1 else f"{base} ({n}).{ext}")
            try:
                with open(path, "x", encoding="utf-8") as f:
                    f.write(out["text"])
            except FileExistsError:
                continue
            return {"path": str(path), "format": out["format"], "hidden": hide or []}
        raise ValueError("Couldn't find a free file name in Downloads for the receipt.")

    # ---- failure replay bundles (replay.py): what someone needs to run a failed notebook again

    def _replay_bundle(self, name: str, cells: list[dict[str, Any]], failed: int,
                       lakehouse_ref: dict[str, str] | None) -> dict[str, Any]:
        if self.proof_busy():
            raise RuntimeError("A proof is using the kernel. Wait for it, or cancel it.")
        kernel = self.sessions.kernel(name)  # type: ignore[attr-defined]
        if kernel.poll().get("status") == "running":
            raise RuntimeError("A cell is running. Save the bundle when it has finished.")
        where = {"process": hasattr(kernel, "limits")}
        if hasattr(kernel, "limits"):
            where["limits"] = kernel.limits()
        return replay.build(name, cells, int(failed), kernel.reads(), lakehouse_ref, where)

    def replay_bundle_preview(self, name: str, cells: list[dict[str, Any]], failed: int,
                              lakehouse_ref: dict[str, str] | None = None) -> dict[str, Any]:
        """What Save replay bundle would save for cell ``failed`` (an index); ``cells`` is the
        notebook screen's, with ``count`` (execution count) and the failed cell's ``outputs``."""
        bundle = self._replay_bundle(name, cells, failed, lakehouse_ref)
        return {"bundle": bundle, "samples_allowed": bool(app_settings.value("allow_replay_samples")),
                "sample_tables": [t["table"] for t in replay.sample_tables(bundle)], "sample_rows": replay.SAMPLE_ROWS}

    def replay_bundle_save(self, name: str, cells: list[dict[str, Any]], failed: int,
                           lakehouse_ref: dict[str, str] | None = None, include_ids: bool = True,
                           include_sample: bool = False) -> dict[str, Any]:
        """Save a replay bundle to the Downloads folder, as a new file. With ``include_sample``,
        up to replay.SAMPLE_ROWS rows of each table read, unless policy turns samples off."""
        if include_sample and not app_settings.value("allow_replay_samples"):
            raise PermissionError("Your organization doesn't allow data samples in replay bundles.")
        bundle = self._replay_bundle(name, cells, failed, lakehouse_ref)
        samples = replay.samples(bundle, self._replay_sample) if include_sample else None
        if not include_ids:
            bundle = replay.strip_ids(bundle)
        folder = Path.home() / "Downloads"
        stem = "".join(ch if ch.isalnum() or ch in "-_" else "_" for ch in name)[:60] or "notebook"
        stamp = time.strftime("%Y%m%d-%H%M%S")
        for n in range(1, 100):
            path = folder / f"replay-{stem}-{stamp}{'' if n == 1 else f'-{n}'}.zip"
            try:  # created exclusively, so a file already there is never replaced
                return replay.write(path, bundle, samples, mode="x") | {"ids_included": include_ids}
            except FileExistsError:
                continue
        raise ValueError("Couldn't find a free file name in Downloads for the bundle.")

    def _replay_sample(self, row: dict[str, Any]) -> Any:
        """Up to replay.SAMPLE_ROWS rows of a table the cells read, at the version they read.

        Save runs in the ordered lane, so everything after it waits for it (#1303): it needs a
        sign-in already there (never a prompt), and reads with short limits, so on a PC that's
        offline it fails within about a minute with a plain error instead of retrying for 30."""
        import requests
        from deltalake import DeltaTable

        from .auth import STORAGE_SCOPES
        if not self.auth.signed_in_user():  # type: ignore[attr-defined]
            raise PermissionError(SAMPLE_SIGNED_OUT)
        try:
            token = self.auth.silent_token(STORAGE_SCOPES)  # type: ignore[attr-defined]
        except requests.RequestException as exc:  # refreshing the sign-in needs the network
            raise ConnectionError(SAMPLE_UNREACHABLE) from exc
        if not token:
            raise PermissionError(SAMPLE_SIGNED_OUT)
        schema, table = row["table"].split(".", 1)
        flat = not self._schemas_enabled(row["workspace_id"], row["lakehouse_id"])  # type: ignore[attr-defined]
        uri = TableRef(row["workspace_id"], row["lakehouse_id"], schema, table, flat=flat).uri
        try:
            opened = DeltaTable(uri, version=row["version"], storage_options=read_storage_options(token))
            return opened.to_pyarrow_dataset().head(replay.SAMPLE_ROWS)
        except OSError as exc:  # delta-rs raises OSError when OneLake doesn't answer
            raise ConnectionError(SAMPLE_UNREACHABLE) from exc

    # ---- opening a bundle and replaying it: the bundle's cells in a notebook of their own, run in
    # the kernel's own process with the proof runtime bound (tables at the bundle's versions, writes
    # to a sandbox under Files/_fabric_desktop/proofs/replay-.../) until the replay ends

    MAX_BUNDLE_BYTES = 100 * 1024 * 1024

    def replay_bundles_list(self) -> list[dict[str, Any]]:
        """Replay bundles in the Downloads folder, newest first."""
        rows = []
        for path in sorted((Path.home() / "Downloads").glob("replay-*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)[:50]:
            try:
                b = replay.read(path)
            except (ValueError, OSError):
                continue
            rows.append({"path": str(path), "file": path.name, "bytes": path.stat().st_size, "notebook": b.get("notebook"),
                         "saved_at": b.get("saved_at"), "failed_cell": b.get("failed_cell"),
                         "samples": len(b.get("samples") or [])})
        return rows

    def replay_bundle_open(self, path: str | None = None, content: str | None = None) -> dict[str, Any]:
        """Open a bundle (a file's ``path``, or its ``content`` in base64) as a new notebook,
        "<notebook> replay", that remembers the bundle. Nothing runs."""
        data = Path(path).read_bytes() if path else base64.b64decode(content or "", validate=True)
        if len(data) > self.MAX_BUNDLE_BYTES:
            raise ValueError("That file is too large for a replay bundle.")
        bundle_id = hashlib.sha256(data).hexdigest()[:16]
        stored = self.root / "replays" / f"{bundle_id}.zip"  # type: ignore[attr-defined]
        stored.parent.mkdir(parents=True, exist_ok=True)
        stored.write_bytes(data)
        try:
            bundle = replay.read(stored)
        except ValueError:
            stored.unlink(missing_ok=True)
            raise
        store = self.notebooks  # type: ignore[attr-defined]
        base = "".join(ch if ch.isalnum() or ch in " -_" else "_" for ch in str(bundle.get("notebook") or "Notebook"))[:60]
        name, n = f"{base} replay", 1
        while store.path(name).exists():
            n += 1
            name = f"{base} replay {n}"
        lh = bundle.get("lakehouse") or {}
        ref = lh if bundle.get("ids_included", True) and lh.get("lakehouse_id") else None
        store.save(name, replay.notebook_cells(bundle), ref, "local")
        store.set_extra(name, "replay", {"bundle": bundle_id, "notebook": bundle.get("notebook"),
                                         "saved_at": bundle.get("saved_at"), "failed_cell": bundle["failed_cell"],
                                         "lakehouse": lh.get("name") or "", "ids_included": bool(bundle.get("ids_included", True))})
        return self.open_notebook(name)  # type: ignore[attr-defined]

    def _replay_of(self, name: str) -> tuple[dict[str, Any], dict[str, Any]]:
        info = self.notebooks.extra(name, "replay")  # type: ignore[attr-defined]
        if not info:
            raise LookupError(f"{name} isn't a replay notebook.")
        return info, replay.read(self.root / "replays" / f"{info['bundle']}.zip")  # type: ignore[attr-defined]

    def _replay_live(self) -> dict[str, Any] | None:
        """The replay holding the kernel, while its process (with the runtime bound) still runs."""
        s = getattr(self, "_replay_session", None)
        session = self.sessions.session(s["name"], create=False) if s else None  # type: ignore[attr-defined]
        k = session.kernel if session else None
        if s and isinstance(k, ProcessKernel) and k.alive() and k.pid == s["pid"]:
            return s
        self._replay_session = None
        return None

    def _replay_guard(self, notebook: str | None) -> None:
        """Called before a cell runs: a replay notebook's cells run only inside its replay (else they
        would read the latest tables and write to the lakehouse), and nothing else runs meanwhile."""
        live = self._replay_live()
        if live and notebook != live["name"]:
            raise RuntimeError(f"The kernel is replaying {live['name']}. End the replay on that notebook first.")
        if not live and notebook and self._is_replay(notebook):
            raise RuntimeError("This is a replay notebook: click Replay on its banner, so tables are read at the "
                               "bundle's versions and writes go to a sandbox. Or keep it as a normal notebook.")

    def _is_replay(self, name: str) -> bool:
        try:
            return bool(self.notebooks.extra(name, "replay"))  # type: ignore[attr-defined]
        except (OSError, ValueError):
            return False

    def _replay_refuse(self, name: str, what: str) -> None:
        """Proofs, side by side and Fabric runs of a replay notebook wait until it's kept as a normal notebook."""
        if self._is_replay(name):
            raise RuntimeError(f"{name} is a replay notebook. Keep it as a normal notebook before {what}.")

    def _replay_root(self, lakehouse_ref: dict[str, str]) -> tuple[str, Callable[[], dict[str, str]], bool]:
        root, options = self._proof_location(lakehouse_ref)
        flat = not self._schemas_enabled(lakehouse_ref["workspace_id"], lakehouse_ref["lakehouse_id"])  # type: ignore[attr-defined]
        return root, options, flat

    def replay_check(self, name: str, lakehouse_ref: dict[str, str] | None = None) -> dict[str, Any]:
        """Before a replay: which tables it pins at which version, any it can't read at that version
        (vacuumed or cleaned up), how this PC's environment differs from the bundle's, and what stops it."""
        info, bundle = self._replay_of(name)
        blockers, notes = [], []
        if not isinstance(self.sessions.kernel(name), ProcessKernel):  # type: ignore[attr-defined]
            blockers.append("Replay runs in the notebook's own process. Turn on Run notebooks in their own process "
                            "in Settings > Preview features.")
        if not lakehouse_ref:
            blockers.append("Attach a lakehouse with Add data items. " + (
                f"The bundle's tables are in {info['lakehouse']}." if info.get("lakehouse") else ""))
        elif info["ids_included"] and (bundle.get("lakehouse") or {}).get("lakehouse_id") not in (None, "", lakehouse_ref["lakehouse_id"]):
            blockers.append(f"The bundle's tables are in {info['lakehouse'] or 'another lakehouse'}. Attach that lakehouse.")
        elif not info["ids_included"]:
            notes.append(f"The bundle doesn't say which lakehouse it read. Its versions are from "
                         f"{info['lakehouse'] or 'its lakehouse'}: check you attached the same one.")
        tables: list[dict[str, Any]] = []
        if lakehouse_ref and not blockers:
            pin, more = replay.tables_to_pin(bundle, None if not info["ids_included"] else lakehouse_ref["lakehouse_id"])
            notes += more
            sampled = {s["table"].split("/", 1)[-1] for s in bundle.get("samples") or []}
            root, options, flat = self._replay_root(lakehouse_ref)
            for t in pin:
                tables.append(self._replay_table(t, root, options, flat) | {"has_sample": t["table"] in sampled})
        modules = sorted({m["module"] for c in bundle["cells"] if c["type"] == "code"
                          for m in envprofile.imports(c["source"])["modules"]})
        diffs = envprofile.differences(bundle.get("environment") or {}, envprofile.runtime(modules), ("Bundle", "This PC"))
        return {"bundle": {k: bundle.get(k) for k in ("notebook", "saved_at", "failed_cell", "error", "limitations")},
                "tables": tables, "environment": diffs, "blockers": blockers, "notes": notes,
                "live": bool((live := self._replay_live()) and live["name"] == name),
                "sandboxes": self.replay_sandboxes(name)}

    def _replay_table(self, t: dict[str, Any], root: str, options: Callable[[], dict[str, str]], flat: bool) -> dict[str, Any]:
        """A pinned table: whether its version can still be read, and its latest version."""
        from deltalake import DeltaTable
        schema, table = t["table"].split(".", 1)
        uri = f"{root}/Tables/{table}" if flat else f"{root}/Tables/{schema}/{table}"
        row: dict[str, Any] = {"table": t["table"], "version": t["version"], "readable": True, "latest": None, "why": ""}

        def opts() -> dict[str, str]:  # two small reads on the ordered lane: OneLake's short read limits (#1303)
            o = options()
            return read_storage_options(o["bearer_token"]) if "bearer_token" in o else o
        try:
            row["latest"] = DeltaTable(uri, storage_options=opts()).version()
            DeltaTable(uri, version=t["version"], storage_options=opts()).to_pyarrow_dataset().head(1)
        except Exception as exc:  # noqa: BLE001 - said on the banner
            row["readable"] = False
            row["why"] = (f"Version {t['version']} can't be read any more (vacuumed or its log was cleaned up)"
                          if row["latest"] is not None else f"The table can't be opened: {type(exc).__name__}")
        return row

    def replay_start(self, name: str, lakehouse_ref: dict[str, str] | None = None,
                     use: dict[str, str] | None = None) -> dict[str, Any]:
        """Start a replay: a fresh kernel process with the proof runtime bound, so the notebook's cells
        (run next, as usual) read tables at the bundle's versions and write to a sandbox. ``use``
        says what to read for a table whose version can't be read: "sample" or "latest"."""
        if self.proof_busy():
            raise RuntimeError("A proof is using the kernel. Wait for it, or cancel it.")
        check = self.replay_check(name, lakehouse_ref)
        if check["blockers"]:
            raise RuntimeError(check["blockers"][0])
        assert lakehouse_ref is not None
        info, bundle = self._replay_of(name)
        use = use or {}
        pins, marked, seeds = {}, {}, []
        for t in check["tables"]:
            if t["readable"]:
                pins[t["table"]] = t["version"]
            elif use.get(t["table"]) == "latest" and t["latest"] is not None:
                pins[t["table"]] = t["latest"]
                marked[t["table"]] = "latest"
            elif use.get(t["table"]) == "sample" and t["has_sample"]:
                seeds.append(t["table"])
                marked[t["table"]] = "sample"
            else:
                raise ValueError(f"{t['table']}: {t['why']}. Choose the bundle's sample or the latest version.")
        rid = "replay-" + time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
        root, options, flat = self._replay_root(lakehouse_ref)
        config = {"id": rid, "pins": pins, "flat": flat, "seed": 0, "seeded": [replay_key(t) for t in seeds]}
        if seeds:
            self._replay_seed(info, rid, root, options, seeds)
        kernel = self.sessions.kernel(name)  # type: ignore[attr-defined]
        self._attach(lakehouse_ref)  # type: ignore[attr-defined]
        kernel.restart()
        kernel.notebook_name = name
        kernel.resources_dir = self._resources_dir(name)  # type: ignore[attr-defined]
        kernel.bind_proof(config, "pc", root, options)
        self._replay_session = {"name": name, "id": rid, "pid": kernel.pid, "marked": marked}
        # Remembered in the notebook (kept when it becomes a normal notebook), so its writes can be deleted later.
        store = self.notebooks  # type: ignore[attr-defined]
        store.set_extra(name, "replay_sandboxes", (store.extra(name, "replay_sandboxes") or []) + [
            {"id": rid, "workspace_id": lakehouse_ref["workspace_id"], "lakehouse_id": lakehouse_ref["lakehouse_id"],
             "lakehouse": lakehouse_ref.get("name") or "", "started_at": _now()}])
        return {"id": rid, "pins": pins, "marked": marked, "sandbox": f"{PROOF_ROOT}/{rid}/pc"}

    def replay_sandboxes(self, name: str) -> list[dict[str, Any]]:
        """The sandboxes this notebook's replays wrote to (Files/_fabric_desktop/proofs/replay-...)."""
        return [dict(x, folder=f"{PROOF_ROOT}/{x.get('id') or '?'}") for x in self.notebooks.extra(name, "replay_sandboxes") or []]  # type: ignore[attr-defined]

    def replay_delete_outputs(self, name: str) -> dict[str, Any]:
        """Delete what this notebook's replays wrote: each replay's folder under Files/. The
        lakehouse's tables aren't touched. A sandbox that can't be deleted stays listed."""
        if (live := self._replay_live()) and live["name"] == name:
            raise RuntimeError("End the replay first.")
        deleted, left, problems = [], [], []
        for box in self.notebooks.extra(name, "replay_sandboxes") or []:  # type: ignore[attr-defined]
            # Never build a path or URL from anything else; an entry that isn't one stays listed.
            if not (re.fullmatch(r"replay-[0-9]{8}-[0-9]{6}-[0-9a-f]{6}", str(box.get("id")))
                    and all(_GUID.fullmatch(str(box.get(k))) for k in ("workspace_id", "lakehouse_id"))):
                left.append(box)
                problems.append("A remembered replay sandbox isn't a replay folder in a lakehouse, so it was left alone.")
                continue
            folder = f"{PROOF_ROOT}/{box['id']}"
            try:
                self._proof_delete_folder({"workspace_id": box["workspace_id"], "lakehouse_id": box["lakehouse_id"]}, folder)
                deleted.append(folder)
            except Exception as exc:  # noqa: BLE001 - reported, and the sandbox stays listed
                left.append(box)
                problems.append(f"{folder} in {box.get('lakehouse') or 'its lakehouse'}: {describe(exc)}")
        self.notebooks.set_extra(name, "replay_sandboxes", left or None)  # type: ignore[attr-defined]
        return {"deleted": deleted, "problems": problems}

    def _replay_seed(self, info: dict[str, Any], rid: str, root: str, options: Callable[[], dict[str, str]],
                     tables: list[str]) -> None:
        """Write the bundle's sample of each table into the replay's sandbox, where reads then find it."""
        import zipfile

        import pyarrow.parquet as pq
        from deltalake import write_deltalake
        path = self.root / "replays" / f"{info['bundle']}.zip"  # type: ignore[attr-defined]
        bundle = replay.read(path)
        with zipfile.ZipFile(path) as z:
            for s in bundle.get("samples") or []:
                table = s["table"].split("/", 1)[-1]
                if table in tables:
                    schema, name = replay_key(table).split(".", 1)
                    check_name("schema", schema)  # replay.read checked it too: never build a path from a bad name
                    check_name("table", name)
                    uri = f"{root}/{PROOF_ROOT}/{rid}/pc/tables/{schema}/{name}"
                    write_deltalake(uri, pq.read_table(z.open(s["file"])), storage_options=options())

    def replay_result(self, name: str, stopped_at: int, status: str,
                      outputs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
        """After the replay ran to the failed cell (or stopped before it): how it ended against the
        bundle, and what it read that differs from what the bundle's run read."""
        live = self._replay_live()
        if not live or live["name"] != name:
            raise RuntimeError("This replay has ended (the kernel restarted). Replay again from the banner.")
        _info, bundle = self._replay_of(name)
        out = replay.outcome(bundle, int(stopped_at), status, outputs or [])
        out["inputs"] = replay.input_differences(bundle, self.sessions.kernel(name).proof_log(), live["marked"])  # type: ignore[attr-defined]
        out["sandbox"] = f"{PROOF_ROOT}/{live['id']}/pc"
        return out

    def replay_end(self, name: str, keep: bool = False) -> dict[str, Any]:
        """End the replay: a fresh kernel, so later runs read the real tables again. With ``keep``
        the notebook becomes a normal notebook (its cells then run as any other)."""
        live = self._replay_live()
        if live and live["name"] == name:
            kernel = self.sessions.kernel(name)  # type: ignore[attr-defined]
            kernel.unbind_proof()
            kernel.restart()
            self._replay_session = None
        if keep:
            self.notebooks.set_extra(name, "replay", None)  # type: ignore[attr-defined]
        return {"ended": True, "kept": keep}

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

    def notebook_debug_get(self, name: str) -> dict[str, Any]:
        """The Variables pane's saved choices: watch expressions (not their values) and the names
        whose hidden-state warning was ignored for this notebook."""
        saved = self.notebooks.extra(name, "debug") or {}  # type: ignore[attr-defined]
        return {"watches": list(saved.get("watches") or []), "ignore_hidden": list(saved.get("ignore_hidden") or [])}

    def notebook_debug_set(self, name: str, watches: list[str] | None = None,
                           ignore_hidden: list[str] | None = None) -> dict[str, Any]:
        """Saves what's given; None keeps the saved value."""
        saved = self.notebook_debug_get(name)
        if watches is not None:
            saved["watches"] = check_watches(watches)
        if ignore_hidden is not None:
            if not isinstance(ignore_hidden, list) or not all(isinstance(n, str) and n.isidentifier() for n in ignore_hidden):
                raise ValueError("ignore_hidden is a list of variable names")
            saved["ignore_hidden"] = sorted(set(ignore_hidden))
        self.notebooks.set_extra(name, "debug", saved if saved["watches"] or saved["ignore_hidden"] else None)  # type: ignore[attr-defined]
        return saved

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
            lines = jsonfile.read_text(self._proofs_file).splitlines()
        except OSError:
            return []
        return [json.loads(line) for line in lines if line.strip()]

    def _proof_save(self, record: dict[str, Any]) -> None:
        # A proof's own thread saves as it ends while a request (Delete outputs) may save too: one at a
        # time, each through its own temporary file, so neither fails nor drops the other's row.
        keep = {k: v for k, v in record.items() if not k.startswith("_")}
        keep["pc"] = {k: v for k, v in record["pc"].items() if k != "cells"}
        with _PROOFS_SAVING:
            rows = [r for r in self._proofs_saved() if r.get("id") != record["id"]] + [keep]
            jsonfile.write_text(self._proofs_file, "".join(json.dumps(r, default=str) + "\n" for r in rows[-200:]))

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
                    compute: str | None = None, workspace: dict[str, str] | None = None,
                    location_ref: dict[str, str] | None = None) -> dict[str, Any]:
        """The plan before a proof: what will happen, what blocks it, what it can't contain.
        ``location_ref`` is the notebook repository's lakehouse for the current environment, used
        when the notebook has none of its own (as a run of its cells does)."""
        lakehouse_ref = lakehouse_ref or location_ref
        check = proof.check(cells, lakehouse_ref, **self._checks(name))  # type: ignore[attr-defined]
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
                    rules: dict[str, Any] | None = None, location_ref: dict[str, str] | None = None) -> dict[str, Any]:
        """Start a proof; returns at once and prove_status follows it. ``rules`` are verification rules
        (verify.py), as {"default": {...}, "tables": {"dbo.t": {...}}}. ``location_ref`` is the
        repository location's lakehouse, used when the notebook has none; it isn't saved into the
        notebook, so the notebook keeps following the environment. Both runs use the Parameters
        bar's values, as side by side does."""
        saved_ref, lakehouse_ref = lakehouse_ref, lakehouse_ref or location_ref
        if self.proof_busy():
            raise RuntimeError("A proof is already running. Wait for it, or cancel it.")
        self._replay_refuse(name, "proving it")
        if self._replay_live():
            raise RuntimeError("A replay is using the kernel. End it first.")
        if any(r["state"] in ("local", "fabric", "checking") for r in self._sbs_state().values()):  # type: ignore[attr-defined]
            raise RuntimeError("A side-by-side run is going. Wait for it, or cancel it.")
        check = proof.check(cells, lakehouse_ref, **self._checks(name))  # type: ignore[attr-defined]
        if not check["ok"]:
            b = check["blockers"][0]
            where = f"Cell {b['cell']} " if b.get("cell") else ""
            raise ValueError(f"This notebook can't be proven yet: {where}{b['text']}")
        rules = _normalized_rules(rules or {})  # refuse bad rules now, not after two runs
        # Fabric gets them as the run's parameters, this PC as the lines runCell adds after the
        # parameter cell. A value the bar shows in red stops it here.
        parameters = self._bar_parameters(name, cells)  # type: ignore[attr-defined]
        suffixes = self._bar_suffixes(name, cells)  # type: ignore[attr-defined]
        assert lakehouse_ref is not None
        saved = self.notebooks.save(name, cells, saved_ref, compute, workspace)  # type: ignore[attr-defined]
        cells = [dict(c) | {"cell_id": cid} for c, cid in zip(cells, saved["cell_ids"])]
        ws = self._publish_workspace(name, lakehouse_ref, workspace)  # type: ignore[attr-defined]
        self.auth.storage_token()  # type: ignore[attr-defined]  # sign in now, while the app is in front
        self.auth.fabric_token()  # type: ignore[attr-defined]
        code = [i for i, c in enumerate(cells) if notebook_cells.runs(c)]
        record: dict[str, Any] = {
            "id": time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6], "name": name, "state": "preparing",
            "started_at": _now(), "finished_at": None, "message": "",
            "lakehouse": {k: lakehouse_ref.get(k, "") for k in ("name", "workspace_id", "workspace_name", "lakehouse_id")},
            "workspace_id": ws["workspace_id"], "workspace_name": ws["workspace_name"], "machine": machine(),
            "check": check, "rules": rules,
            "parameters": parameters, "code": receipts.code_revision(cells),
            "pc": {"status": "pending", "cell": None, "cells_total": len(code), "cells": {}},
            "fabric": {"status": "pending", "engine": check.get("engine", "python")},
            "inputs": {}, "shared": {}, "tables": [], "verdict": None, "scorecard": None, "receipt_id": None,
            "_cancel": False, "_suffixes": suffixes}
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
            self.sessions.kernel(record["name"]).interrupt()  # type: ignore[attr-defined]
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
        if final == "done":
            try:
                record["earned"] = self._progress_observe_proof(record | {"state": final})  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001 - the profile never changes a proof's result
                _log.warning("The proof couldn't be added to the profile: %s", redact.describe(exc))
        record["state"] = final
        self._proof_save(record)

    def _proof_pc(self, record: dict[str, Any], cells: list[dict[str, Any]], code: list[int],
                  lakehouse_ref: dict[str, str], config: dict[str, Any], root: str,
                  options: Callable[[], dict[str, str]], modules: list[str]) -> dict[str, Any]:
        """Every code cell in a fresh kernel with the proof runtime bound: pinned reads, sandboxed writes."""
        pc = record["pc"]
        pc["status"] = "running"
        self._attach(lakehouse_ref)  # type: ignore[attr-defined]
        kernel = self.sessions.kernel(record["name"])  # type: ignore[attr-defined]
        kernel.restart()
        kernel.notebook_name = record["name"]
        kernel.resources_dir = self._resources_dir(record["name"])  # type: ignore[attr-defined]
        kernel.widget_values = dict((self._saved_inputs(record["name"]) or {}).get("widgets") or {})  # type: ignore[attr-defined]
        suffixes = record.get("_suffixes") or []
        kernel.bind_proof(config, "pc", root, options)
        status = "ok"
        try:
            # With notebooks in their own process, the code runs there, so that process is measured.
            process = (lambda: kernel.live_pid) if isinstance(kernel, ProcessKernel) else None
            with RunMeter(process=process) as meter:
                for n, i in enumerate(code, 1):
                    if record["_cancel"]:
                        status = "cancelled"
                        break
                    pc["cell"] = n
                    extra = suffixes[i] if i < len(suffixes) else ""
                    kernel.start(cells[i]["source"] + (f"\n{extra}" if extra else ""))
                    if record["_cancel"]:  # Cancel landed while the cell was starting: its interrupt may have missed it
                        kernel.interrupt()
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
        schemas = not config["flat"]
        spark = proof.runs_spark(cells)
        first, last = proof.fabric_cells(config, modules, {
            "workspace_id": lakehouse_ref["workspace_id"], "lakehouse_id": lakehouse_ref["lakehouse_id"],
            "name": lakehouse_ref.get("name", ""), "schemas": schemas} if spark else None)
        f["engine"] = "spark" if spark else "python"
        started = time.monotonic()
        ipynb = json.dumps(fabric_ipynb([first] + cells + [last], lakehouse_ref, schemas, sql_calls=spark)).encode()
        item_name = f"{leaf_name(record['name'])}{PROOF_SUFFIX}"  # Fabric names have no folders
        item = self.fabric.save_notebook(ws["workspace_id"], item_name, ipynb)  # type: ignore[attr-defined]
        if not item.get("id"):
            item = next((n for n in self.fabric.list_notebooks(ws["workspace_id"])  # type: ignore[attr-defined]
                         if n["displayName"] == item_name), item)
        from .cloud_service import FABRIC_APP
        url = f"{FABRIC_APP}/groups/{ws['workspace_id']}/synapsenotebooks/{item.get('id')}?experience=fabric-developer"
        parameters = record.get("parameters")
        job_id = self.fabric_jobs.run_item_job(ws["workspace_id"], item["id"], NOTEBOOK_JOB,  # type: ignore[attr-defined]
                                               {"parameters": parameters} if parameters else None)
        f.update(status="running", job_id=job_id, item_id=item["id"], item_name=item_name,
                 workspace_id=ws["workspace_id"], url=url, submitted_at=_now())
        self._log_fabric_run({"kind": "Notebook run", "name": item_name, "workspace_id": ws["workspace_id"],  # type: ignore[attr-defined]
                              "workspace_name": ws["workspace_name"], "engine": f["engine"], "item_id": item["id"],
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


def replay_key(table: str) -> str:
    """A table as the proof runtime keys it: schema.table, lower case."""
    schema, _, name = table.rpartition(".")
    return f"{(schema or 'dbo').lower()}.{name.lower()}"


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
