"""Live tests against a real, non-production Fabric workspace (FD-101).

Every other test fakes Fabric. This one calls it, so a person can prove on their own tenant that
sign-in, the Fabric REST API, OneLake files and Delta tables work the way the app relies on:

    fabric-desktop live-test --workspace <workspace id> --dry-run   # what it would do; writes nothing
    fabric-desktop live-test --workspace <workspace id> --yes       # run it

Safety, in order:

1. The workspace must say it is for testing: its description contains ``fabric-desktop-live-test``,
   which a workspace admin adds on purpose.
2. Even then, a workspace whose name has "prod", "prd" or "production" as a word is refused, and so
   is any workspace the organization's policy file names as a Platform workspace.
3. The harness only creates new things named ``fdlt_<run id>``, records each one in a ledger file
   the moment it exists, and only ever deletes what that ledger lists. ``--cleanup <ledger>``
   finishes a cleanup that was cut off.

Opt-in checks (KF-DE-005) run only when named (``--check``) or with ``--all``, so the default run
is unchanged: two writers appending to one Delta table at once, a notebook publish round trip, a
pipeline definition round trip, and (only with ``--run-in-fabric``, which spends capacity) running
that notebook in Fabric, a table write lease held on OneLake against a second writer (PB-003), and
a MERGE that must keep the table in the Fabric-safe Delta profile. Each writes only
``fdlt_<run id>`` things that go in the ledger, or files inside the test lakehouse.

Results go to ``<out>/<run id>/result.json`` and ``summary.md``. Neither holds a token.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests

from . import delta_dml, delta_profile, jsonfile, leases, redact
from .fabric_api import FabricError
from .onelake import FileStore, TableRef, storage_options

_log = logging.getLogger(__name__)

MARKER = "fabric-desktop-live-test"
PREFIX = "fdlt_"
SCHEMA = "live-test-result@1"
# Opt-in checks: what each writes. OneLake writes and item creation use the workspace's capacity.
EXTRA_CHECKS = {
    "delta.concurrent": "Two writers append to one Delta table (dbo.livetest_concurrent in the test lakehouse) at "
                        "the same time. Writes about 100 rows to OneLake, which uses capacity.",
    "notebook.publish": "Publishes a two-cell notebook item named fdlt_<run id>_nb, reads its definition back and "
                        "compares the cells, then deletes it. Creating an item uses capacity.",
    "pipeline.definition": "Creates a one-activity (Wait) DataPipeline item named fdlt_<run id>_pl, reads its "
                           "definition back, parses and re-exports it, then deletes it. Creating an item uses capacity.",
    "pipeline.mailbox": "Writes a run request under Files/_fabric_desktop in the test lakehouse, lets the pipeline "
                        "mailbox take it, cancel it and keep a second agent off it, and looks up a pipeline run that "
                        "doesn't exist. Writes a few small files to OneLake, which uses capacity.",
    "notebook.run": "Runs the published fdlt_<run id>_nb notebook in Fabric (Run in Fabric) and waits for it. "
                    "Spends Spark capacity, so it only runs with --run-in-fabric.",
    "lease.onelake": "Holds a table write lease on OneLake (a small file under Files/_fabric_desktop/leases in the "
                     "test lakehouse) and checks a second writer is refused until it is released. A few small "
                     "OneLake writes, which use capacity.",
    "delta.merge_profile": "Runs a MERGE INTO dbo.livetest_merge in the test lakehouse the way the app's SQL does "
                           "(delta_dml), under the table's lease, and checks the table stays in the Fabric-safe "
                           "Delta profile. Writes 4 rows to OneLake, which uses capacity.",
}
ALL_CHECKS = ("delta.concurrent", "notebook.publish", "pipeline.definition", "pipeline.mailbox", "lease.onelake",
              "delta.merge_profile")  # what --all selects
ITEM_KINDS = {"notebook": "Notebook", "pipeline": "DataPipeline"}  # Fabric type; deleted before the lakehouse
_RUN_TIMEOUT = 30 * 60
_PROD_WORD = re.compile(r"(?<![a-z0-9])(prod|prd|production)(?![a-z0-9])", re.IGNORECASE)


class Refused(Exception):
    """The workspace isn't one the harness may write to."""


def refusals(workspace: dict[str, Any], protected_ids: set[str]) -> list[str]:
    """Every reason this workspace isn't safe to test in; empty when it is."""
    name = str(workspace.get("displayName") or "")
    reasons = []
    if MARKER not in str(workspace.get("description") or ""):
        reasons.append(f"The workspace description doesn't contain {MARKER}. A workspace admin adds it to say "
                       "this workspace is for tests.")
    if _PROD_WORD.search(name):
        reasons.append(f"The workspace name {name!r} looks like production.")
    if str(workspace.get("id") or "").lower() in protected_ids:
        reasons.append("Your organization's policy file names this workspace as a Platform workspace.")
    return reasons


def policy_workspace_ids() -> set[str]:
    """Platform workspaces the organization's policy file names; the harness never writes to them."""
    from . import app_settings
    platform = app_settings.load()["platform"]
    if platform["source"] != "policy":
        return set()
    return {str(cfg.get("workspace_id", "")).lower() for cfg in (platform["value"] or {}).values()
            if isinstance(cfg, dict) and cfg.get("workspace_id")}


def scrub(text: str) -> str:
    """Remove anything token-shaped from text that goes into a result."""
    return redact.secrets(text)[:2000]


def cell(text: Any, limit: int = 2000) -> str:
    """Text for one cell of a report's Markdown table, or a name in its lines: credential-shaped values
    removed, control characters (a carriage return, a terminal escape) and line breaks folded to
    spaces, cut to ``limit``, and ``|`` escaped so it can't open a column of its own. The report is
    printed to the terminal and written to report.md, and a name or detail can come from a workspace."""
    return redact.one_line(text, limit).replace("|", "\\|")


@dataclass
class Check:
    id: str
    title: str
    status: str = "skipped"  # passed, failed, skipped
    seconds: float = 0.0
    detail: str = ""


@dataclass
class Ledger:
    """What this run created, written to disk the moment each thing exists."""

    path: Path
    run_id: str
    workspace_id: str
    created: list[dict[str, str]] = field(default_factory=list)

    def add(self, **resource: str) -> None:
        self.created.append(resource)
        self.save()

    def remove(self, resource: dict[str, str]) -> None:
        self.created = [r for r in self.created if r != resource]
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        jsonfile.write_text(self.path, json.dumps({"run_id": self.run_id, "workspace_id": self.workspace_id,
                                                    "created": self.created}, indent=2))

    @classmethod
    def load(cls, path: Path) -> "Ledger":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(path, data["run_id"], data["workspace_id"], list(data.get("created", [])))


TableAccess = Callable[[TableRef], tuple[str, dict[str, str]]]


class _MailboxAgent:
    """Just enough of an Agent for pipeline.mailbox: a name and a queue nothing ever runs."""

    def __init__(self, name: str, queue: Any):
        self.config = type("Config", (), {"name": name})()
        self.queue, self.clock = queue, time.time

    def enqueue(self, env: str, entities: list[str], backfill: str, cascade: bool, source: str,
                dedupe_key: str) -> dict[str, Any]:
        payload = {"env": env, "entities": entities, "backfill": backfill, "cascade": cascade}
        return self.queue.enqueue("meta", payload, source=source, dedupe_key=dedupe_key)

    def has_free_worker(self) -> bool:
        return True


def _status(control: Any, rid: str) -> dict[str, Any]:
    stored = control.read(f"status/{rid}.json")
    if stored is None:
        raise RuntimeError("No agent wrote a status for the request")
    return json.loads(stored.data)


class LiveTest:
    """One run of the live checks against one workspace."""

    def __init__(self, workspace_id: str, fabric: Any, files: Any, credential: Any, out_dir: Path,
                 table_access: TableAccess | None = None, protected_ids: set[str] | None = None,
                 bad_token_files: Any | None = None, run_id: str = "", clock: Callable[[], float] = time.monotonic,
                 extra: set[str] | tuple[str, ...] = (), run_in_fabric: bool = False,
                 write_table: Callable[[TableRef, Any, str], Any] | None = None,
                 sleep: Callable[[float], None] = time.sleep, mailbox_control: Callable[[], Any] | None = None,
                 lease_backend: Callable[[], leases.Backend] | None = None):
        self.workspace_id = workspace_id
        # The control files pipeline.mailbox writes: the test lakehouse's Files area; tests pass a local folder.
        self.mailbox_control = mailbox_control or self._onelake_control
        self.fabric, self.files, self.credential = fabric, files, credential
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.name = f"{PREFIX}{self.run_id}"
        self.dir = Path(out_dir) / self.run_id
        self.ledger = Ledger(self.dir / "ledger.json", self.run_id, workspace_id)
        self.protected_ids = {i.lower() for i in (protected_ids or set())}
        self.table_access = table_access or (lambda ref: (ref.uri, storage_options(credential.storage_token())))
        self.bad_token_files = bad_token_files if bad_token_files is not None else FileStore(lambda: "not-a-token")
        self.clock, self.sleep = clock, sleep
        unknown = set(extra) - set(EXTRA_CHECKS)
        if unknown:
            raise ValueError(f"Unknown checks: {', '.join(sorted(unknown))}. Choose from {', '.join(EXTRA_CHECKS)}.")
        self.extra = set(extra) | ({"notebook.publish", "notebook.run"} if run_in_fabric else set())
        self.run_in_fabric = run_in_fabric
        # The write path notebooks and jobs use (publish.publish); tests pass one that writes to local disk.
        self.write_table = write_table or self._publish
        # Where the lease checks keep lease files: the test lakehouse on OneLake; tests pass a local folder.
        self.lease_backend = lease_backend or (lambda: leases.OneLakeBackend(
            self.workspace_id, self.lakehouse_id, self.credential.storage_token,
            timeout=leases.lease_timeout(leases.TABLE_LEASE_TTL)))
        self.checks: list[Check] = []
        self.workspace: dict[str, Any] = {}
        self.lakehouse_id = ""
        self.notebook_id = ""

    # ---- what it does

    def plan(self) -> list[str]:
        folder = f"Files/_fabric_desktop_livetest/{self.run_id}"
        return [
            "Sign in for the Fabric API and for OneLake.",
            f"Read workspace {self.workspace_id} and check it is marked for tests and isn't production.",
            f"Create a schema-enabled lakehouse named {self.name} in it.",
            f"Write, list, read and delete a small file under {folder}.",
            "Write a 3-row Delta table dbo.livetest, read it back, append, merge, and read version 0.",
            "Ask for a lakehouse that doesn't exist, and call OneLake with a bad token, to check errors are reported.",
            *[f"Opt-in {id}: {EXTRA_CHECKS[id]}".replace("<run id>", self.run_id)
              for id in EXTRA_CHECKS if id in self.extra],
            *(["Opt-in notebook.run: skipped; add --run-in-fabric to run the notebook in Fabric (spends capacity)."]
              if "notebook.publish" in self.extra and not self.run_in_fabric else []),
            f"Delete the lakehouse {self.name}{' and the items above' if self.extra else ''}, and check it is gone.",
            f"Write the results to {self.dir}.",
        ]

    def _check(self, id: str, title: str, fn: Callable[[], str | None]) -> Check:
        check = Check(id, title)
        start = self.clock()
        try:
            check.detail = fn() or ""
            check.status = "passed"
        except Exception as exc:  # every failure is a result, not a crash
            check.status = "failed"
            check.detail = scrub(f"{type(exc).__name__}: {exc}")
        check.seconds = round(self.clock() - start, 3)
        self.checks.append(check)
        return check

    def _skip(self, id: str, title: str, why: str) -> None:
        self.checks.append(Check(id, title, "skipped", 0.0, why))

    # ---- the run

    def run(self) -> dict[str, Any]:
        started = datetime.now(timezone.utc).isoformat(timespec="seconds")
        signed_in = self._check("auth.fabric", "Get a token for the Fabric API", self._fabric_token)
        self._check("auth.onelake", "Get a token for OneLake", self._storage_token)
        if signed_in.status != "passed":
            return self._finish(started, refused=[])
        found = self._check("fabric.workspace", "Read the workspace", self._read_workspace)
        if found.status != "passed":
            return self._finish(started, refused=[])
        refused = refusals(self.workspace, self.protected_ids)
        if refused:
            return self._finish(started, refused=refused)
        try:
            self._write_checks()
            self._extra_checks()
        finally:
            self.cleanup()
        return self._finish(started, refused=[])

    def _fabric_token(self) -> str:
        if not self.credential.fabric_token():
            raise RuntimeError("Sign-in returned an empty token")
        return "Token received (not recorded)."

    def _storage_token(self) -> str:
        if not self.credential.storage_token():
            raise RuntimeError("Sign-in returned an empty token")
        return "Token received (not recorded)."

    def _read_workspace(self) -> str:
        self.workspace = self.fabric.get_workspace(self.workspace_id)
        return f"{self.workspace.get('displayName', '')} (capacity {self.workspace.get('capacityId', 'none')})"

    def _write_checks(self) -> None:
        lh = self._check("fabric.create_lakehouse", "Create a schema-enabled lakehouse", self._create_lakehouse)
        if lh.status != "passed":
            for id, title in _AFTER_LAKEHOUSE:
                self._skip(id, title, "Needs the lakehouse, which couldn't be created.")
            return
        self._check("fabric.get_lakehouse", "Read the lakehouse's properties", self._get_lakehouse)
        self._check("onelake.files", "Write, list, read and delete a file in OneLake", self._files)
        wrote = self._check("delta.write", "Write a Delta table to OneLake", self._delta_write)
        if wrote.status == "passed":
            self._check("delta.read", "Read the Delta table back", self._delta_read)
            self._check("delta.append", "Append rows (a second commit)", self._delta_append)
            self._check("delta.merge", "Merge (update one row, insert one)", self._delta_merge)
            self._check("delta.time_travel", "Read version 0 of the table", self._delta_time_travel)
        else:
            for id, title in _AFTER_WRITE:
                self._skip(id, title, "Needs the Delta table, which couldn't be written.")
        self._check("errors.not_found", "A missing lakehouse is reported as an error", self._not_found)
        self._check("errors.bad_token", "OneLake refuses a bad token", self._bad_token)

    def _create_lakehouse(self) -> str:
        created = self.fabric.create_lakehouse(self.workspace_id, self.name,
                                               description=f"Created by the Fabric Desktop live test {self.run_id}; "
                                                           "safe to delete.")
        self.lakehouse_id = str(created.get("id") or "")
        if not self.lakehouse_id:
            raise RuntimeError("Fabric didn't return the new lakehouse's id")
        self.ledger.add(kind="lakehouse", workspace_id=self.workspace_id, id=self.lakehouse_id, name=self.name)
        return f"Lakehouse {self.name} ({self.lakehouse_id})"

    def _get_lakehouse(self) -> str:
        props = self.fabric.get_lakehouse(self.workspace_id, self.lakehouse_id).get("properties") or {}
        if not props.get("oneLakeTablesPath"):
            raise RuntimeError("The lakehouse properties have no oneLakeTablesPath")
        return "Properties include the OneLake Tables and Files paths."

    def _files(self) -> str:
        folder = f"Files/_fabric_desktop_livetest/{self.run_id}"
        path = f"{folder}/probe.txt"
        body = f"Fabric Desktop live test {self.run_id}\n".encode()
        self.files.upload(self.workspace_id, self.lakehouse_id, path, body)
        names = [e["name"] for e in self.files.list(self.workspace_id, self.lakehouse_id, folder)]
        if "probe.txt" not in names:
            raise RuntimeError(f"The new file isn't listed in {folder} (found {names})")
        back = self.files.read_bytes(self.workspace_id, self.lakehouse_id, path)
        if back != body:
            raise RuntimeError("The file read back differs from what was written")
        self.files.delete(self.workspace_id, self.lakehouse_id, folder)
        if self.files.read_bytes(self.workspace_id, self.lakehouse_id, path) is not None:
            raise RuntimeError("The file is still there after deleting it")
        return f"{len(body)} bytes written, listed, read back identically and deleted."

    def _table(self) -> tuple[str, dict[str, str]]:
        return self.table_access(TableRef(self.workspace_id, self.lakehouse_id, "dbo", "livetest"))

    def _delta_write(self) -> str:
        import pyarrow as pa
        from deltalake import write_deltalake
        uri, opts = self._table()
        write_deltalake(uri, pa.table({"id": [1, 2, 3], "name": ["a", "b", "c"]}), mode="overwrite",
                        storage_options=opts)
        return "3 rows written as version 0."

    def _rows(self, version: int | None = None) -> list[dict[str, Any]]:
        from deltalake import DeltaTable
        uri, opts = self._table()
        table = DeltaTable(uri, storage_options=opts, version=version)
        return sorted(table.to_pyarrow_table().to_pylist(), key=lambda r: r["id"])

    def _delta_read(self) -> str:
        rows = self._rows()
        if rows != [{"id": 1, "name": "a"}, {"id": 2, "name": "b"}, {"id": 3, "name": "c"}]:
            raise RuntimeError(f"Read back {rows}")
        return "The 3 rows read back unchanged."

    def _delta_append(self) -> str:
        import pyarrow as pa
        from deltalake import DeltaTable, write_deltalake
        uri, opts = self._table()
        write_deltalake(uri, pa.table({"id": [4], "name": ["d"]}), mode="append", storage_options=opts)
        version = DeltaTable(uri, storage_options=opts).version()
        if version != 1 or len(self._rows()) != 4:
            raise RuntimeError(f"Expected version 1 with 4 rows, found version {version}")
        return "Version 1 has 4 rows."

    def _delta_merge(self) -> str:
        import pyarrow as pa
        from deltalake import DeltaTable
        uri, opts = self._table()
        source = pa.table({"id": [2, 5], "name": ["B", "e"]})
        (DeltaTable(uri, storage_options=opts)
         .merge(source, predicate="t.id = s.id", source_alias="s", target_alias="t")
         .when_matched_update_all().when_not_matched_insert_all().execute())
        names = {r["id"]: r["name"] for r in self._rows()}
        if names != {1: "a", 2: "B", 3: "c", 4: "d", 5: "e"}:
            raise RuntimeError(f"After the merge the table holds {names}")
        return "One row updated and one inserted."

    def _delta_time_travel(self) -> str:
        if len(self._rows(version=0)) != 3:
            raise RuntimeError("Version 0 doesn't have the original 3 rows")
        return "Version 0 still has the original 3 rows."

    def _not_found(self) -> str:
        missing = str(uuid.uuid4())
        try:
            self.fabric.get_lakehouse(self.workspace_id, missing)
        except FabricError as exc:
            return f"Reported as {exc.code or 'an error'}."
        raise RuntimeError("Asking for a lakehouse that doesn't exist didn't fail")

    def _bad_token(self) -> str:
        try:
            self.bad_token_files.list(self.workspace_id, self.lakehouse_id, "Files")
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status in (401, 403):
                return f"Refused with {status}."
            raise RuntimeError(f"Refused with {status}, expected 401 or 403") from None
        raise RuntimeError("OneLake accepted a bad token")

    # ---- opt-in checks (KF-DE-005): only what was asked for with --check, --all or --run-in-fabric

    def _extra_checks(self) -> None:
        if "delta.concurrent" in self.extra:
            if self.lakehouse_id:
                self._check("delta.concurrent", "Two writers append to one Delta table at once", self._delta_concurrent)
            else:
                self._skip("delta.concurrent", "Two writers append to one Delta table at once",
                           "Needs the lakehouse, which couldn't be created.")
        if "notebook.publish" in self.extra:
            published = self._check("notebook.publish", "Publish a notebook and read its cells back",
                                    self._notebook_publish)
            if not self.run_in_fabric:
                self._skip("notebook.run", "Run the notebook in Fabric",
                           "skipped: needs --run-in-fabric (it spends capacity).")
            elif published.status == "passed":
                self._check("notebook.run", "Run the notebook in Fabric", self._notebook_run)
            else:  # running cells that didn't publish as written would spend capacity proving nothing
                self._skip("notebook.run", "Run the notebook in Fabric",
                           "Needs the notebook published as written, and notebook.publish failed.")
        if "pipeline.definition" in self.extra:
            self._check("pipeline.definition", "Create a pipeline, read its definition back and re-export it",
                        self._pipeline_definition)
        if "pipeline.mailbox" in self.extra:
            if self.lakehouse_id:
                self._check("pipeline.mailbox", "A pipeline run request is taken, cancelled and kept to one agent",
                            self._pipeline_mailbox)
            else:
                self._skip("pipeline.mailbox", "A pipeline run request is taken, cancelled and kept to one agent",
                           "Needs the lakehouse, which couldn't be created.")
        for id, title, fn in (("lease.onelake", "A second writer is refused while a lease is held", self._lease),
                              ("delta.merge_profile", "A MERGE keeps the table in the Fabric-safe Delta profile",
                               self._merge_profile)):
            if id not in self.extra:
                continue
            if self.lakehouse_id:
                self._check(id, title, fn)
            else:
                self._skip(id, title, "Needs the lakehouse, which couldn't be created.")

    def _publish(self, ref: TableRef, data: Any, mode: str) -> Any:
        from .publish import publish
        return publish(data, ref, mode, self.credential.storage_token())

    def _delta_concurrent(self) -> str:
        import pyarrow as pa
        from deltalake import DeltaTable
        ref = TableRef(self.workspace_id, self.lakehouse_id, "dbo", "livetest_concurrent")
        self.write_table(ref, pa.table({"id": [0], "writer": ["seed"]}), "overwrite")
        n, start = 50, threading.Barrier(2)
        attempts: dict[str, int] = {}
        errors: dict[str, str] = {}

        def writer(name: str, first: int) -> None:
            rows = pa.table({"id": list(range(first, first + n)), "writer": [name] * n})
            try:
                start.wait(timeout=60)
            except threading.BrokenBarrierError:
                _log.info("Live test writer %s started without the other", name)
            for attempt in range(1, 4):
                attempts[name] = attempt
                try:
                    self.write_table(ref, rows, "append")
                    return
                except Exception as exc:  # recorded as the check's failure below
                    if attempt == 3 or not _commit_conflict(exc):
                        errors[name] = f"{type(exc).__name__}: {exc}"
                        return
                    self.sleep(attempt)

        threads = [threading.Thread(target=writer, args=(name, first), daemon=True)
                   for name, first in (("A", 1), ("B", 1 + n))]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=300)
        if any(t.is_alive() for t in threads):
            raise RuntimeError("A writer was still committing after 5 minutes")
        if errors:
            raise RuntimeError("; ".join(f"Writer {k} failed: {v}" for k, v in sorted(errors.items())))
        uri, opts = self.table_access(ref)
        table = DeltaTable(uri, storage_options=opts)
        ids = sorted(table.to_pyarrow_table().column("id").to_pylist())
        expected = list(range(0, 1 + 2 * n))
        if ids != expected:
            lost = sorted(set(expected) - set(ids))
            raise RuntimeError(f"Expected {len(expected)} rows, found {len(ids)}"
                               + (f"; lost ids {lost[:10]}" if lost else "; some rows were written twice"))
        if table.version() != 2:
            raise RuntimeError(f"Expected both appends as versions 1 and 2, found version {table.version()}")
        retried = [f"writer {k} retried ({v} attempts)" for k, v in sorted(attempts.items()) if v > 1]
        return (f"Both commits are there (version 2, {len(ids)} rows = 1 + {n} + {n}, none lost). "
                + (", ".join(retried) + "." if retried else
                   "Neither writer had to retry; Delta resolved any commit race itself."))

    def _lease(self) -> str:
        backend = self.lease_backend()
        key = "dbo.livetest_lease"
        first = leases.Leases(backend, owner=f"live test {self.run_id} A", ttl=leases.TABLE_LEASE_TTL, wait=0)
        second = leases.Leases(backend, owner=f"live test {self.run_id} B", ttl=leases.TABLE_LEASE_TTL, wait=0)
        with first.hold(key):
            if (stolen := second.try_acquire(key)) is not None:
                backend.delete(key, stolen)  # B's copy, so leaving A's hold reports this, not a lost lease
                raise RuntimeError("A second writer got the lease while the first held it: OneLake didn't refuse "
                                   "a create with If-None-Match: *")
            if backend.replace(key, b"{}", '"0x8D0000000000000"') is not None:
                raise RuntimeError("OneLake replaced the lease file with a stale ETag: it ignored If-Match")
            try:
                with second.hold(key):
                    raise RuntimeError("A second writer held the lease while the first held it")
            except leases.LeaseBusy:
                pass
        if backend.read(key) is not None:
            raise RuntimeError("The lease file was still there after the first writer released it")
        etag = second.try_acquire(key)
        if not etag:
            raise RuntimeError("The second writer couldn't take the lease after the first released it")
        if not backend.delete(key, etag):
            raise RuntimeError("Releasing the second writer's lease failed")
        return ("While writer A held the lease, writer B's create was refused (If-None-Match), a stale "
                "replace was refused (If-Match) and B's wait ended busy; after A released it, B took it and released it.")

    def _merge_profile(self) -> str:
        import pyarrow as pa
        from deltalake import DeltaTable
        ref = TableRef(self.workspace_id, self.lakehouse_id, "dbo", "livetest_merge")
        self.write_table(ref, pa.table({"id": [1, 2, 3], "amount": [10, 20, 30]}), "overwrite")
        uri, opts = self.table_access(ref)
        change = delta_dml.parse("MERGE INTO dbo.livetest_merge AS t USING s ON t.id = s.id "
                                 "WHEN MATCHED THEN UPDATE SET * WHEN NOT MATCHED THEN INSERT *")
        source = pa.table({"id": [2, 4], "amount": [200, 40]})
        # The same lease the write path takes on this table, so no other KratosForge writer overlaps.
        with leases.table_lease(self.lease_backend(), "dbo", "livetest_merge", owner=f"live test {self.run_id}"):
            table = delta_profile.open_target(uri, opts)  # refuses a table outside the profile before writing
            if table is None:
                raise RuntimeError("The seeded table couldn't be opened")
            changed = delta_dml.apply(change, table, source, source.column_names)
        after = DeltaTable(uri, storage_options=opts)
        delta_profile.check_table(after)
        rows = {r["id"]: r["amount"] for r in after.to_pyarrow_table().to_pylist()}
        if rows != {1: 10, 2: 200, 3: 30, 4: 40}:
            raise RuntimeError(f"After the MERGE the table holds {rows}")
        protocol = after.protocol()
        return (f"MERGE updated {changed['rows_updated']} and inserted {changed['rows_inserted']} row(s) as "
                f"version {after.version()}; the table stays in the profile (reader {protocol.min_reader_version}, "
                f"writer {protocol.min_writer_version}, no deletion vectors).")

    def _new_item_name(self, suffix: str, item_type: str) -> str:
        name = f"{self.name}_{suffix}"
        if self.fabric.find_item(self.workspace_id, item_type, name):
            raise RuntimeError(f"A {item_type} named {name} already exists; the live test won't overwrite it.")
        return name

    def _notebook_publish(self) -> str:
        from .fabric_run import fabric_ipynb
        name = self._new_item_name("nb", "Notebook")
        cells = [{"type": "markdown", "source": f"# KratosForge live test {self.run_id}"},
                 {"type": "code", "source": "message = 'fabric-desktop live test'\nprint(message)"}]
        # The same conversion and API call as Publish to Fabric (cloud_service._publish).
        item = self.fabric.save_notebook(self.workspace_id, name, json.dumps(fabric_ipynb(cells, None)).encode())
        if not item.get("id"):  # a create can come back without the item; look it up
            item = next((n for n in self.fabric.list_notebooks(self.workspace_id) if n["displayName"] == name), item)
        self.notebook_id = str(item.get("id") or "")
        if not self.notebook_id:
            raise RuntimeError("Fabric didn't return the new notebook's id")
        self.ledger.add(kind="notebook", workspace_id=self.workspace_id, id=self.notebook_id, name=name)
        back = _ipynb_cells(self.fabric.get_item_definition(self.workspace_id, self.notebook_id, fmt="ipynb"))
        want = [(c["type"], c["source"]) for c in cells]
        if back != want:
            raise RuntimeError(f"The cells read back differ from those published: {back!r}")
        return f"Notebook {name} ({self.notebook_id}) published; its {len(want)} cells read back unchanged."

    def _notebook_run(self) -> str:
        from .fabric_run import TERMINAL
        job_type = "RunNotebook"  # what Run in Fabric starts (cloud_service.NOTEBOOK_JOB)
        job_id = self.fabric.run_item_job(self.workspace_id, self.notebook_id, job_type)
        deadline = self.clock() + _RUN_TIMEOUT
        while True:
            job = self.fabric.job_instance(self.workspace_id, self.notebook_id, job_id)
            status = job.get("status")
            if status == "Completed":
                return f"Job {job_id} completed in Fabric."
            if status in TERMINAL:
                reason = (job.get("failureReason") or {}).get("message", "") if isinstance(job.get("failureReason"), dict) else ""
                raise RuntimeError(f"Job {job_id} ended {status}. {redact.one_line(reason or '', 600)}".strip())
            if self.clock() > deadline:
                self.fabric.cancel_job(self.workspace_id, self.notebook_id, job_id)
                raise RuntimeError(f"Job {job_id} hadn't finished after {_RUN_TIMEOUT // 60} minutes; cancelled it.")
            self.sleep(10)

    def _pipeline_definition(self) -> str:
        from .orchestration import definition
        name = self._new_item_name("pl", "DataPipeline")
        content = {"properties": {"activities": [{"name": "Wait1", "type": "Wait", "dependsOn": [],
                                                  "typeProperties": {"waitTimeInSeconds": 1}}]}}
        item = self.fabric.save_item(self.workspace_id, "DataPipeline", name,
                                     parts={definition.CONTENT_PART: json.dumps(content)},
                                     description=f"Created by the Fabric Desktop live test {self.run_id}; safe to delete.")
        item_id = str(item.get("id") or "")
        if not item_id:
            raise RuntimeError("Fabric didn't return the new pipeline's id")
        if item.get("created") is False:  # appeared since the check above; not ours, so never in the ledger
            raise RuntimeError(f"{name} already existed, so the live test didn't record it for deleting")
        self.ledger.add(kind="pipeline", workspace_id=self.workspace_id, id=item_id, name=name)
        back = self.fabric.get_item_definition(self.workspace_id, item_id)
        imported = definition.loads(json.dumps(back), name_hint=name)
        if imported.pipeline.problems:
            raise RuntimeError("The definition read back has problems: "
                               + "; ".join(str(p) for p in imported.pipeline.problems))
        names = [a.name for a in imported.pipeline.all_activities()]
        if names != ["Wait1"]:
            raise RuntimeError(f"Expected the one Wait1 activity, read back {names}")
        exported = definition.export(imported)
        again = definition.loads(exported if isinstance(exported, str) else json.dumps(exported), name_hint=name)
        if again.pipeline.content != imported.pipeline.content:
            raise RuntimeError("Exporting the definition and reading it again changed it")
        return (f"Pipeline {name} ({item_id}) created; its definition parses "
                f"({imported.source_format}, {len(names)} activity) and round-trips through export.")

    def _onelake_control(self) -> Any:
        from .mailbox import OneLakeControl
        return OneLakeControl(self.workspace_id, self.lakehouse_id, self.credential.storage_token)

    def _pipeline_mailbox(self) -> str:
        """The agent's side of the mailbox (mailbox.py) against OneLake's conditional writes, with a
        queue that never runs anything: claim, a second agent kept off, a cancel, and the run check."""
        import tempfile

        from .jobqueue import JobQueue
        from .mailbox import Mailbox

        control, env, rid = self.mailbox_control(), "livetest", f"{PREFIX}{self.run_id}"
        missing = {"item_id": str(uuid.uuid4()), "run_id": str(uuid.uuid4())}
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:  # Windows may hold the queue files
            def agent(name: str) -> Any:
                return _MailboxAgent(name, JobQueue(Path(tmp) / f"{name}.sqlite3"))

            def verify(_env: str, item_id: str, run_id: str) -> dict[str, Any]:
                return self.fabric.job_instance(self.workspace_id, item_id, run_id)

            a = Mailbox(agent(f"{rid}_a"), lambda _: control, [env], verify_run=verify)
            b = Mailbox(agent(f"{rid}_b"), lambda _: control, [env], verify_run=verify)
            body = {"contract": 1, "env": env, "entities": [], "submitted_by": f"live test {self.run_id}",
                    "pipeline_run": missing}
            if not control.create(f"requests/{rid}.json", json.dumps(body).encode()):
                raise RuntimeError(f"requests/{rid}.json already existed")
            try:
                a._poll_env(env)
                first = _status(control, rid)
                if first.get("state") != "accepted" or first.get("agent") != f"{rid}_a":
                    raise RuntimeError(f"Expected accepted by agent A, the status says {first.get('state')} by {first.get('agent')}")
                run = first.get("pipeline_run") or {}
                if run.get("verified") is not False or "couldn't find" not in str(run.get("detail")):
                    raise RuntimeError(f"A made-up pipeline run wasn't reported as not found: {run}")
                if control.create(f"status/{rid}.json", b"{}"):
                    raise RuntimeError("A second claim of the same request succeeded; If-None-Match isn't honoured")
                b._poll_env(env)
                if _status(control, rid).get("agent") != f"{rid}_a":
                    raise RuntimeError("Agent B changed a status agent A holds")
                control.create(f"cancel/{rid}.json", b"{}")
                a._poll_env(env)
                final = _status(control, rid)
                if final.get("state") != "cancelled":
                    raise RuntimeError(f"After the cancel file the status says {final.get('state')}, not cancelled")
            finally:  # leave nothing in the control folder, whether or not the check passed
                for folder in ("cancel", "requests", "status"):
                    stored = control.read(f"{folder}/{rid}.json")
                    if stored is not None:
                        control.delete(f"{folder}/{rid}.json", stored.etag)
        return ("Request taken once by agent A, left alone by agent B, cancelled; "
                f"a made-up pipeline run was reported as not found ({run.get('detail')}).")

    # ---- cleanup

    def cleanup(self) -> list[Check]:
        """Delete what this run's ledger lists, and nothing else: items first, then the lakehouse."""
        done = []
        ordered = sorted(self.ledger.created, key=lambda r: r.get("kind") == "lakehouse")
        for resource in ordered:
            kind = resource.get("kind")
            name = resource.get("name", "")
            ours = name == self.name if kind == "lakehouse" else name.startswith(f"{self.name}_")
            if kind not in ("lakehouse", *ITEM_KINDS) or resource.get("workspace_id") != self.workspace_id or not ours:
                continue  # never delete what this run didn't name (fdlt_<run id>, fdlt_<run id>_*)
            check = self._check(f"cleanup.{kind}", f"Delete {kind} {resource['name']}",
                                lambda r=resource: self._delete(r))
            if check.status == "passed":
                self.ledger.remove(resource)
            done.append(check)
        return done

    def _delete(self, resource: dict[str, str]) -> str:
        # The ledger is a file on disk: check the live item is still the one this run made before deleting it.
        if resource["kind"] in ITEM_KINDS:
            live = next((i for i in self.fabric.list_items(self.workspace_id) if i.get("id") == resource["id"]), None)
            if live is None:
                return "Already gone."
            if live.get("type") != ITEM_KINDS[resource["kind"]] or live.get("displayName") != resource["name"]:
                raise RuntimeError(f"Item {resource['id']} is now {live.get('type')} {live.get('displayName')!r}, "
                                   f"not the {resource['kind']} {resource['name']} this run made; not deleted")
            self.fabric.delete_item(self.workspace_id, resource["id"])
            if any(i.get("id") == resource["id"] for i in self.fabric.list_items(self.workspace_id)):
                raise RuntimeError(f"The {resource['kind']} is still listed after deleting it")
            return "Deleted, and no longer listed."
        try:
            live = self.fabric.get_lakehouse(self.workspace_id, resource["id"])
        except FabricError as exc:
            if exc.status == 404 or exc.code in ("ItemNotFound", "EntityNotFound"):
                return "Already gone."
            raise
        if live.get("displayName") != resource["name"]:
            raise RuntimeError(f"Lakehouse {resource['id']} is now {live.get('displayName')!r}, "
                               f"not {resource['name']}; not deleted")
        self.fabric.delete_lakehouse(self.workspace_id, resource["id"])
        try:
            self.fabric.get_lakehouse(self.workspace_id, resource["id"])
        except FabricError:
            return "Deleted, and no longer found."
        raise RuntimeError("The lakehouse can still be read after deleting it")

    # ---- results

    def _finish(self, started: str, refused: list[str]) -> dict[str, Any]:
        counts = {s: sum(c.status == s for c in self.checks) for s in ("passed", "failed", "skipped")}
        outcome = "refused" if refused else ("failed" if counts["failed"] else "passed")
        result = {
            "schema": SCHEMA, "run_id": self.run_id, "outcome": outcome, "started_at": started,
            "finished_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "workspace": {"id": self.workspace_id, "name": self.workspace.get("displayName", "")},
            "identity": {"name": getattr(self.credential, "name", "") or "signed-in user"},  # never the tenant id
            "refused": refused, "counts": counts, "checks": [asdict(c) for c in self.checks],
            "opt_in": {"checks": sorted(self.extra), "run_in_fabric": self.run_in_fabric},
            "left_behind": self.ledger.created,
        }
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "result.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
        (self.dir / "summary.md").write_text(summary(result), encoding="utf-8")
        result["files"] = [str(self.dir / "result.json"), str(self.dir / "summary.md")]
        return result


_AFTER_WRITE = [("delta.read", "Read the Delta table back"), ("delta.append", "Append rows (a second commit)"),
                ("delta.merge", "Merge (update one row, insert one)"), ("delta.time_travel", "Read version 0 of the table")]
_AFTER_LAKEHOUSE = [("fabric.get_lakehouse", "Read the lakehouse's properties"),
                    ("onelake.files", "Write, list, read and delete a file in OneLake"),
                    ("delta.write", "Write a Delta table to OneLake"), *_AFTER_WRITE,
                    ("errors.not_found", "A missing lakehouse is reported as an error"),
                    ("errors.bad_token", "OneLake refuses a bad token")]


def _commit_conflict(exc: Exception) -> bool:
    """A Delta commit lost a race with another writer and can be tried again."""
    text = f"{type(exc).__name__} {exc}".lower()
    return any(w in text for w in ("commitfailed", "conflict", "concurrent", "version already exists"))


def _ipynb_cells(definition: dict[str, Any]) -> list[tuple[str, str]]:
    """(type, source) of each cell in a notebook's getDefinition result (format ipynb)."""
    import base64
    parts = (definition.get("definition") or definition).get("parts") or []
    part = next((p for p in parts if str(p.get("path", "")).endswith(".ipynb")), None)
    if part is None:
        raise RuntimeError("The notebook definition has no .ipynb part")
    nb = json.loads(base64.b64decode(part["payload"]).decode("utf-8-sig"))
    out = []
    for cell in nb.get("cells", []):
        source = cell.get("source", "")
        out.append(("markdown" if cell.get("cell_type") == "markdown" else "code",
                    "".join(source) if isinstance(source, list) else str(source)))
    return out


def _safe(fn: Callable[[], Any]) -> str:
    try:
        return str(fn() or "")
    except Exception as exc:
        _log.info("Couldn't read a live-test detail: %s", redact.describe(exc))
        return ""


def summary(result: dict[str, Any]) -> str:
    mark = {"passed": "Passed", "failed": "**Failed**", "skipped": "Skipped"}
    lines = [f"# KratosForge live test {result['run_id']}", "",
             f"Outcome: **{result['outcome']}**. Workspace {cell(result['workspace']['name'], 200) or result['workspace']['id']}, "
             f"started {result['started_at']}.", ""]
    if (result.get("opt_in") or {}).get("checks"):
        lines += [f"Opt-in checks: {', '.join(result['opt_in']['checks'])}.", ""]
    if result["refused"]:
        lines += ["Nothing was written, because:", ""] + [f"- {cell(r)}" for r in result["refused"]] + [""]
    lines += ["| Check | Result | Seconds | Detail |", "| --- | --- | --- | --- |"]
    for c in result["checks"]:
        lines.append(f"| {cell(c['title'], 200)} | {mark[c['status']]} | {c['seconds']} | {cell(c['detail'])} |")
    if result["left_behind"]:
        lines += ["", "**Left behind** (cleanup failed; run `fabric-desktop live-test --cleanup` with this run's ledger.json):", ""]
        lines += [f"- {r['kind']} {cell(r['name'], 200)} ({cell(r['id'], 100)})" for r in result["left_behind"]]
    return "\n".join(lines) + "\n"


def cleanup_from_ledger(path: Path, fabric: Any, credential: Any) -> dict[str, Any]:
    """Finish a cleanup that was cut off: delete what the ledger lists, nothing else."""
    ledger = Ledger.load(path)
    test = LiveTest(ledger.workspace_id, fabric, None, credential, path.parent.parent, run_id=ledger.run_id,
                    bad_token_files=object())
    test.ledger = ledger
    checks = test.cleanup()
    return {"run_id": ledger.run_id, "checks": [asdict(c) for c in checks], "left_behind": ledger.created}
