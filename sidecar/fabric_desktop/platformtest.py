"""The central platform flows of the tenant smoke test (PB-052 part 1), on the workspace set aside for tests.

    fabric-desktop smoke-test --workspace <workspace id> --platform --yes

The platform (central/setup.py: LH_Meta, LH_Landing, SQL_Platform, UDF_Platform, VL_Platform) has only
been tested with Fabric faked. These flows make one in the marked test workspace, use it the way an
admin and a team do, and delete it again:

    11  Finish or repair once, from empty
    12  Finish or repair once, again (or, with --platform-upgrade, from a platform a previous build made)
    13  Register a data product
    14  Issue and look up surrogate keys
    15  Ask for, approve and end a policy exception
    16  Restrict who reads a central lakehouse family (central_restrict), when one is set up here

The same guards as the live test apply: the workspace's description must contain
``fabric-desktop-live-test``, a name that looks like production or a Platform workspace in the
policy file is refused. On top of that the workspace must hold none of the platform's items (so a real
platform is never changed), unless ``upgrade`` says a previous build's platform is there on purpose.
Every item the run makes or takes over is written to ``platform-ledger.json`` before it is used, and
deleted by id at the end unless ``keep``. Everything the flows write inside the platform is named
``fdlt_<run id>`` and goes when SQL_Platform is deleted.
"""

from __future__ import annotations

import datetime as dt
import json
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Callable

from . import livetest
from .central import core, udf
from .central import setup as setup_mod

ITEMS = {setup_mod.META: "meta_id", setup_mod.LANDING: "landing_id", setup_mod.SQL: "sql_id",
         setup_mod.FUNCTIONS: "functions_id", setup_mod.VARLIB: "varlib_id"}
# Deleted in this order: what depends on SQL_Platform first, the lakehouses last.
_DELETE_ORDER = (setup_mod.VARLIB, setup_mod.FUNCTIONS, setup_mod.SQL, setup_mod.LANDING, setup_mod.META)
_LAKEHOUSES = {setup_mod.META, setup_mod.LANDING}
# The Fabric item type each platform item is. A lakehouse and a SQL database come with a SQL
# endpoint of the same name (and, when made before Fabric stopped that, a default semantic model),
# which are theirs, not a clash.
ITEM_TYPES = {setup_mod.META: "Lakehouse", setup_mod.LANDING: "Lakehouse", setup_mod.SQL: "SQLDatabase",
              setup_mod.FUNCTIONS: "UserDataFunction", setup_mod.VARLIB: "VariableLibrary"}
_THEIR_OWN = ("SQLEndpoint", "SemanticModel")
CONNECTION_HELP = (f"Open {setup_mod.FUNCTIONS} in the test workspace in Fabric, choose Manage connections, add "
                   f"{setup_mod.SQL} with the alias \"{udf.ALIAS}\", publish, then press Enter here.")
FLOW_TITLES = {
    11: "Central platform: Finish or repair once, from empty",
    12: "Central platform: Finish or repair once, again or from a previous version",
    13: "Central platform: register a data product",
    14: "Central platform: issue and look up surrogate keys",
    15: "Central platform: a policy exception, asked for, approved and ended",
    16: "Central platform: restrict a central lakehouse family (central_restrict)",
}


def _platform_items(items: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], list[str]]:
    """The workspace's platform items, by name, matched on name AND type; and what clashes with them:
    an item of another type with a platform name (not a lakehouse's or SQL database's own SQL endpoint
    or default semantic model),
    or two items of a platform item's type with its name."""
    found: dict[str, dict[str, Any]] = {}
    clashes = []
    for item in items:
        name, kind = item.get("displayName"), item.get("type")
        if name not in ITEMS:
            continue
        if kind == ITEM_TYPES[name]:
            if name in found:
                clashes.append(f"two {kind} items named {name}")
            found[name] = item
        elif not (kind in _THEIR_OWN and ITEM_TYPES[name] in ("Lakehouse", "SQLDatabase")):
            clashes.append(f"a {kind or 'item of unknown type'} named {name}, where the platform's is a {ITEM_TYPES[name]}")
    return found, clashes


class Skip(Exception):
    """A flow that can't run here, with the reason; recorded as skipped, never as failed."""


@dataclass
class Check:
    flow: int
    id: str
    title: str
    status: str = "skipped"
    detail: str = ""


class PlatformTest:
    """One run of the central platform flows. ``client`` builds the platform client for (workspace
    id, UDF_Platform id); ``setup`` is central/setup.py's (tests pass a fake). ``pause`` is asked to
    wait while a person does a by-hand step (the CLI waits for Enter; None never waits).
    ``restrict`` runs flow 16 and raises Skip when no central lakehouse is set up in the test workspace."""

    def __init__(self, workspace_id: str, fabric: Any, execute_token: Callable[[], str], out_dir: Path,
                 client: Callable[[str, str], Any], setup: Callable[..., dict[str, Any]] = setup_mod.setup,
                 user: Callable[[], str] = lambda: "", run_id: str = "", protected_ids: set[str] | None = None,
                 upgrade: bool = False, keep: bool = False, pause: Callable[[str], None] | None = None,
                 restrict: Callable[[], str] | None = None):
        self.workspace_id = workspace_id
        self.fabric, self.execute_token, self.client_for, self.setup = fabric, execute_token, client, setup
        self.user = user
        self.run_id = run_id or uuid.uuid4().hex[:12]
        self.dir = Path(out_dir)
        self.protected_ids = {i.lower() for i in (protected_ids or set())}
        self.upgrade, self.keep, self.pause, self.restrict = upgrade, keep, pause, restrict
        self.name = f"fdlt_{self.run_id}"
        self.checks: list[Check] = []
        self.config: dict[str, Any] = {}
        self.ledger: list[dict[str, str]] = []
        self.left_behind: list[dict[str, str]] = []
        self.refused: list[str] = []

    def plan(self) -> list[str]:
        start = ("take over the platform a previous build made in it (--platform-upgrade) and run Finish or repair "
                 "once on it" if self.upgrade else
                 "check it holds no platform, then run Finish or repair once from empty: it creates "
                 + ", ".join(ITEMS))
        return [
            f"Flows 11-12: in workspace {self.workspace_id}, {start}; then run it again, which must keep everything. "
            "Creating items and calling UDF_Platform uses capacity.",
            f"If {setup_mod.FUNCTIONS} has no connection to {setup_mod.SQL} yet, stop and ask a person to add it in Fabric.",
            f"Flow 13: register project {self.name} and data product {self.name}_product.",
            f"Flow 14: create key table {self.name}, issue keys for two business keys, look one up and issue them again.",
            f"Flow 15: publish policy {self.name.replace('_', '-')}, ask for an exception to it, approve it, see it active, end it.",
            "Flow 16: restrict who reads a central lakehouse family, only if one is set up in this workspace.",
            "Delete every platform item it made or took over" + (": not deleted, --platform-keep" if self.keep else "")
            + f"; the ledger is {self.dir / 'platform-ledger.json'}.",
        ]

    # ---- running

    def _check(self, flow: int, id: str, fn: Callable[[], str]) -> Check:
        check = Check(flow, f"platform.{id}", FLOW_TITLES[flow])
        try:
            check.detail = fn() or ""
            check.status = "passed"
        except Skip as why:
            check.detail = str(why)
        except Exception as exc:  # every failure is a result, not a crash
            check.status = "failed"
            check.detail = livetest.scrub(f"{type(exc).__name__}: {exc}")
        self.checks.append(check)
        return check

    def run(self) -> dict[str, Any]:
        self.dir.mkdir(parents=True, exist_ok=True)
        try:
            workspace = self.fabric.get_workspace(self.workspace_id)
        except Exception as exc:  # noqa: BLE001 - reported as the refusal, nothing was made
            self.refused = [livetest.scrub(f"Couldn't read the workspace: {exc}")]
            return self._finish()
        self.refused = livetest.refusals(workspace, self.protected_ids)
        found, clashes = _platform_items(self.fabric.list_items(self.workspace_id)) if not self.refused else ({}, [])
        if clashes:
            self.refused.append("The workspace holds items the platform's names would clash with: " + "; ".join(clashes)
                                + ". The platform flows don't take over or work around them; use a workspace without them.")
        elif found and not self.upgrade:
            self.refused.append(f"The workspace already holds {', '.join(sorted(found))}. The platform flows only run "
                                "on a workspace with no platform, so they never change a real one; add --platform-upgrade "
                                "only for a platform a previous build made in this test workspace.")
        elif self.upgrade and not found and not self.refused:
            self.refused.append("--platform-upgrade needs the platform a previous build made in this workspace, and "
                                "there is none: run Finish setup there from that build first.")
        if self.refused:
            return self._finish()
        self.config = {"workspace_id": self.workspace_id, "workspace_name": workspace.get("displayName") or ""}
        for name, item in found.items():  # a previous build's platform: taken over, so deleted at the end
            self.config[ITEMS[name]] = item["id"]
            self._remember(name, item["id"])
        try:
            if self.upgrade:
                self.checks.append(Check(11, "platform.finish_empty", FLOW_TITLES[11], "skipped",
                                         "Not run: --platform-upgrade starts from a previous build's platform."))
            else:
                self._check(11, "finish_empty", lambda: self._finish_setup(first=True))
            ready = self._check(12, "upgrade" if self.upgrade else "repair", lambda: self._finish_setup(first=False))
            if ready.status == "passed":
                client = self.client_for(self.workspace_id, self.config["functions_id"])
                self._check(13, "product", lambda: self._product(client))
                self._check(14, "keys", lambda: self._keys(client))
                self._check(15, "exception", lambda: self._exception(client))
            else:
                for flow, id in ((13, "product"), (14, "keys"), (15, "exception")):
                    self.checks.append(Check(flow, f"platform.{id}", FLOW_TITLES[flow], "skipped",
                                             "Not run: the platform isn't ready (flow 12)."))
            self._check(16, "restrict", self._restrict)
        finally:
            self.cleanup()
        return self._finish()

    def _remember(self, name: str, item_id: str) -> None:
        if not any(e["id"] == item_id for e in self.ledger):
            self.ledger.append({"name": name, "id": item_id})
            self._save_ledger()

    def _save_ledger(self) -> None:
        body = {"run_id": self.run_id, "workspace_id": self.workspace_id, "items": self.ledger}
        (self.dir / "platform-ledger.json").write_text(json.dumps(body, indent=2), encoding="utf-8")

    def _finish_setup(self, first: bool) -> str:
        """Finish or repair once, as Platform > Settings runs it (setup.setup)."""
        out = self._setup()
        if not out["ready"] and self.pause and _needs_connection(out):
            self.pause(CONNECTION_HELP)
            out = self._setup()
        details = "; ".join(f"{s['step']}: {s['detail']}" for s in out["steps"])
        if not out["ready"]:
            raise RuntimeError(f"Not ready. {details}")
        if not first:
            made = [s["step"] for s in out["steps"] if str(s["detail"]).startswith("Created ")
                    and s["step"] != "Platform tables"]
            if made and not self.upgrade:
                raise RuntimeError(f"Running it again created {', '.join(made)}: it should keep what's there. {details}")
        version = self.client_for(self.workspace_id, self.config["functions_id"]).call("ensure_schema")
        if int((version or {}).get("schema_version") or 0) != core.SCHEMA_VERSION:
            raise RuntimeError(f"The platform's schema is version {(version or {}).get('schema_version')}, "
                               f"this build's is {core.SCHEMA_VERSION}")
        return f"Ready, schema version {core.SCHEMA_VERSION}. {details}"

    def _setup(self) -> dict[str, Any]:
        out = self.setup(self.fabric, self.execute_token, "dev", "", "", dict(self.config))
        self.config = dict(out["config"])
        for name, key in ITEMS.items():  # in the ledger as soon as it exists, before anything uses it
            if self.config.get(key):
                self._remember(name, self.config[key])
        return out

    def _product(self, client: Any) -> str:
        me = self.user()
        client.call("register_project", project={"name": self.name, "description": "Made by the KratosForge smoke test"})
        out = client.call("register_product", product={"project": self.name, "name": f"{self.name}_product",
                                                       "owner": me, "access": "private", "status": "draft"},
                          ports=[], env="dev")
        listed = [p for p in client.call("list_products") if p.get("id") == f"{self.name}/{self.name}_product"]
        if not listed:
            raise RuntimeError("The product was registered but isn't in the platform's product list")
        return f"Registered {out['id']} (version {out.get('version')}); it's listed."

    def _keys(self, client: Any) -> str:
        client.call("ensure_key_table", name=self.name, business_key="code",
                    description="Made by the KratosForge smoke test")
        first = client.call("issue_keys", name=self.name, business_keys=["A", "B"])
        again = client.call("issue_keys", name=self.name, business_keys=["B", "A"])
        if first.get("keys") != again.get("keys"):
            raise RuntimeError(f"Issuing the same business keys again gave other keys: {first} then {again}")
        found = client.call("lookup_key", name=self.name, value="A")
        if not found or found["sk"] != first["keys"]["A"]:
            raise RuntimeError(f"Looking up A gave {found}, not the key it was issued ({first['keys'].get('A')})")
        return f"Issued {first.get('inserted')} keys, the same ones again; A looks up as {found['sk']}."

    def _exception(self, client: Any) -> str:
        pid = self.name.replace("_", "-")
        client.call("create_policy", policy={"id": pid, "name": f"Smoke test {self.run_id}", "category": "classification",
                                             "severity": "low", "enforcement": "warn", "rule": "no_pii_dev"})
        client.call("publish_policy", id=pid, note="smoke test")
        until = (dt.date.today() + dt.timedelta(days=7)).isoformat()
        req = client.call("request", kind="exception", target="", reason="Smoke test of the exception flow",
                          detail={"policy": pid, "scope": "project", "scope_target": self.name, "until": until})
        client.call("decide_request", id=req["id"], approve=True, note="smoke test")
        active = [e for e in client.call("list_policy_exceptions", policy=pid, active=True) if e.get("id") == req["id"]]
        if not active:
            raise RuntimeError("The approved exception isn't listed as active")
        client.call("end_policy_exception", id=req["id"], reason="Smoke test done")
        still = [e for e in client.call("list_policy_exceptions", policy=pid, active=True) if e.get("id") == req["id"]]
        if still:
            raise RuntimeError("The exception is still active after it was ended")
        return f"Exception {req['id']} to {pid} until {until}: asked, approved, active, then ended."

    def _restrict(self) -> str:
        if self.restrict is None:
            raise Skip("No central lakehouse is set up in the test workspace: run flow 16 by hand (Optimize > Access > "
                       "Who may read it; it needs OneLake security on and a lakehouse with schemas) and tick it in the "
                       "results file.")
        return self.restrict()

    # ---- cleanup and results

    def cleanup(self) -> None:
        if self.keep:
            self.left_behind = list(self.ledger)
            return
        for entry in sorted(self.ledger, key=lambda e: _DELETE_ORDER.index(e["name"])):
            try:
                if entry["name"] in _LAKEHOUSES:
                    self.fabric.delete_lakehouse(self.workspace_id, entry["id"])
                else:
                    self.fabric.delete_item(self.workspace_id, entry["id"])
            except Exception as exc:  # noqa: BLE001 - recorded as left behind, the rest are still deleted
                self.left_behind.append(entry | {"why": livetest.scrub(str(exc))[:200]})
        self._save_ledger()

    def _finish(self) -> dict[str, Any]:
        failed = any(c.status == "failed" for c in self.checks)
        return {"run_id": self.run_id, "refused": self.refused, "checks": [asdict(c) for c in self.checks],
                "outcome": "refused" if self.refused else "failed" if failed else "passed",
                "ledger": self.ledger, "left_behind": self.left_behind, "kept": self.keep}


def _needs_connection(out: dict[str, Any]) -> bool:
    """Setup stopped where UDF_Platform has no connection to SQL_Platform yet: a by-hand step in Fabric."""
    return any(s["step"] == "Platform tables" and s["status"] == "failed" for s in out["steps"])
