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

Results go to ``<out>/<run id>/result.json`` and ``summary.md``. Neither holds a token.
"""

from __future__ import annotations

import json
import logging
import re
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests

from . import redact
from .fabric_api import FabricError
from .onelake import FileStore, TableRef, storage_options

_log = logging.getLogger(__name__)

MARKER = "fabric-desktop-live-test"
PREFIX = "fdlt_"
SCHEMA = "live-test-result@1"
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
        self.path.write_text(json.dumps({"run_id": self.run_id, "workspace_id": self.workspace_id,
                                         "created": self.created}, indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> "Ledger":
        data = json.loads(path.read_text(encoding="utf-8"))
        return cls(path, data["run_id"], data["workspace_id"], list(data.get("created", [])))


TableAccess = Callable[[TableRef], tuple[str, dict[str, str]]]


class LiveTest:
    """One run of the live checks against one workspace."""

    def __init__(self, workspace_id: str, fabric: Any, files: Any, credential: Any, out_dir: Path,
                 table_access: TableAccess | None = None, protected_ids: set[str] | None = None,
                 bad_token_files: Any | None = None, run_id: str = "", clock: Callable[[], float] = time.monotonic):
        self.workspace_id = workspace_id
        self.fabric, self.files, self.credential = fabric, files, credential
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.name = f"{PREFIX}{self.run_id}"
        self.dir = Path(out_dir) / self.run_id
        self.ledger = Ledger(self.dir / "ledger.json", self.run_id, workspace_id)
        self.protected_ids = {i.lower() for i in (protected_ids or set())}
        self.table_access = table_access or (lambda ref: (ref.uri, storage_options(credential.storage_token())))
        self.bad_token_files = bad_token_files if bad_token_files is not None else FileStore(lambda: "not-a-token")
        self.clock = clock
        self.checks: list[Check] = []
        self.workspace: dict[str, Any] = {}
        self.lakehouse_id = ""

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
            f"Delete the lakehouse {self.name}, and check it is gone.",
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

    # ---- cleanup

    def cleanup(self) -> list[Check]:
        """Delete what this run's ledger lists, and nothing else."""
        done = []
        for resource in list(self.ledger.created):
            if resource.get("kind") != "lakehouse" or resource.get("workspace_id") != self.workspace_id \
                    or not resource.get("name", "").startswith(PREFIX):
                continue  # never delete what this harness didn't name
            check = self._check("cleanup.lakehouse", f"Delete lakehouse {resource['name']}",
                                lambda r=resource: self._delete(r))
            if check.status == "passed":
                self.ledger.remove(resource)
            done.append(check)
        return done

    def _delete(self, resource: dict[str, str]) -> str:
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
            "identity": {"name": getattr(self.credential, "name", "") or "signed-in user",
                         "tenant": _safe(lambda: self.credential.signed_in_tenant())},
            "refused": refused, "counts": counts, "checks": [asdict(c) for c in self.checks],
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


def _safe(fn: Callable[[], Any]) -> str:
    try:
        return str(fn() or "")
    except Exception as exc:
        _log.debug("Couldn't read a live-test detail: %s", redact.describe(exc))
        return ""


def summary(result: dict[str, Any]) -> str:
    mark = {"passed": "Passed", "failed": "**Failed**", "skipped": "Skipped"}
    lines = [f"# Fabric Desktop by KratosBI live test {result['run_id']}", "",
             f"Outcome: **{result['outcome']}**. Workspace {result['workspace']['name'] or result['workspace']['id']}, "
             f"started {result['started_at']}.", ""]
    if result["refused"]:
        lines += ["Nothing was written, because:", ""] + [f"- {r}" for r in result["refused"]] + [""]
    lines += ["| Check | Result | Seconds | Detail |", "| --- | --- | --- | --- |"]
    for c in result["checks"]:
        detail = c["detail"].replace("|", "\\|").replace("\n", " ")
        lines.append(f"| {c['title']} | {mark[c['status']]} | {c['seconds']} | {detail} |")
    if result["left_behind"]:
        lines += ["", "**Left behind** (cleanup failed; run `fabric-desktop live-test --cleanup` with this run's ledger.json):", ""]
        lines += [f"- {r['kind']} {r['name']} ({r['id']})" for r in result["left_behind"]]
    return "\n".join(lines) + "\n"


def cleanup_from_ledger(path: Path, fabric: Any, credential: Any) -> dict[str, Any]:
    """Finish a cleanup that was cut off: delete what the ledger lists, nothing else."""
    ledger = Ledger.load(path)
    test = LiveTest(ledger.workspace_id, fabric, None, credential, path.parent.parent, run_id=ledger.run_id,
                    bad_token_files=object())
    test.ledger = ledger
    checks = test.cleanup()
    return {"run_id": ledger.run_id, "checks": [asdict(c) for c in checks], "left_behind": ledger.created}
