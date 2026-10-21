"""The shortcut command center, mixed into ``Service``: every OneLake shortcut the signed-in
person can see, whether it works, who can read it, what changed, and requests for new ones.

The rules are in shortcut_center.py. History, health and requests are kept by the platform
(central/core.py), so a team sees one history; without a platform in Fabric that's the
platform on this PC. Nothing here reads table data or writes to it: a health check lists one
entry of the shortcut's folder through OneLake.
"""

from __future__ import annotations

import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from . import access as access_mod
from . import shortcut_center as center
from . import shortcuts as shortcuts_mod
from .auth import ONELAKE_SCOPES
from .fabric_api import FabricClient
from .meta.project import ENVIRONMENTS
from .onelake import FileStore
from .redact import describe

_log = logging.getLogger(__name__)


class ShortcutMixin:

    # ---- what the command center shows

    def _sc_envs(self) -> dict[str, str]:
        """Workspace id to environment, from the metadata project and the platform."""
        known: dict[str, str] = {}
        try:
            for env, cfg in self._project().environments.items():  # type: ignore[attr-defined]
                if cfg.get("workspace_id"):
                    known[cfg["workspace_id"]] = env
                for ws in (cfg.get("workspaces") or {}).values():
                    if isinstance(ws, dict) and ws.get("id"):
                        known[ws["id"]] = env
        except Exception as exc:  # noqa: BLE001 - no metadata project: names decide
            _log.info("No metadata project; workspace names decide the environment: %s", describe(exc))
        try:
            for env, cfg in self._platform_config().items():  # type: ignore[attr-defined]
                if cfg.get("workspace_id") and env in ENVIRONMENTS:
                    known.setdefault(cfg["workspace_id"], env)
        except Exception as exc:  # noqa: BLE001
            _log.info("No platform configuration; workspace names decide the environment: %s", describe(exc))
        return known

    def _sc_call(self, op: str, problems: list[str], default: Any, **kwargs: Any) -> Any:
        try:
            return self._catalog().call(op, **kwargs)  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - the shortcuts still show without the platform
            msg = f"The platform couldn't {op.replace('_', ' ')}: {exc}"
            if msg not in problems:
                problems.append(msg)
            return default

    def shortcut_center(self, lakehouses: list[dict[str, str]], refresh: bool = False) -> dict[str, Any]:
        """Every shortcut in ``lakehouses`` [{workspace_id, lakehouse_id, name, workspace_name}],
        with health, risks and product, plus counts, needs and the changes since the last scan.
        {"pending": True} while the lakehouses are still being read."""
        inv = self.all_shortcuts(lakehouses, refresh)  # type: ignore[attr-defined]
        if inv.get("pending"):
            return {"pending": True}
        problems: list[str] = []
        refs = {l["lakehouse_id"]: l for l in lakehouses}
        envs = self._sc_envs()
        names = {l["workspace_id"]: l.get("workspace_name", "") for l in lakehouses}
        rows = []
        for lh_id, items in inv["items"].items():
            ref = refs.get(lh_id)
            if ref is None:
                continue  # read for an earlier list of lakehouses
            for s in items:
                rows.append(s | {"lakehouse_id": lh_id, "host_workspace_id": ref.get("workspace_id", ""),
                                 "workspace_name": ref.get("workspace_name", ""), "lakehouse_name": ref.get("name", "")})
        cached = getattr(self, "_all_shortcuts", None)
        if cached is not None and cached is not getattr(self, "_sc_recorded", None):
            self._sc_recorded = cached
            scanned = [{"lakehouse_id": i, "workspace_id": refs.get(i, {}).get("workspace_id", ""),
                        "workspace_name": refs.get(i, {}).get("workspace_name", ""),
                        "lakehouse_name": refs.get(i, {}).get("name", "")} for i in inv["items"] if i in refs]
            self._sc_call("record_shortcuts", problems, {}, lakehouses=scanned, shortcuts=[
                {"lakehouse_id": r["lakehouse_id"], "workspace_id": r["host_workspace_id"],
                 "workspace_name": r["workspace_name"], "lakehouse_name": r["lakehouse_name"], "path": r["path"],
                 "name": r["name"], "target": r["rest_target"]} for r in rows])
        seen = {s["id"]: s for s in self._sc_call("list_seen_shortcuts", problems, [])}
        products = center.port_index(self._sc_call("list_products", problems, []))
        for r in rows:
            sid = center.identity(r)
            stored = seen.get(sid, {})
            r["id"] = sid
            r["health"] = stored.get("health") or "not_checked"
            r["health_detail"] = stored.get("health_detail") or ""
            r["checked_at"] = stored.get("checked_at") or ""
            r["first_seen"] = stored.get("first_seen") or ""
            r["exposure"] = center.exposure(r)
            r["host_env"] = center.env_of(r["host_workspace_id"], r["workspace_name"], envs)
            r["target_env"] = (center.env_of(r.get("workspace_id") or "", names.get(r.get("workspace_id") or "", ""), envs)
                               if r["target_type"] == "OneLake" else "")
            r["product"] = products.get(center.target_key(r))
        flagged = center.risks(rows, envs)
        for r in rows:
            r["risks"] = flagged.get(r["id"], [])
        requests = [q for q in self._sc_call("list_requests", problems, []) if q.get("kind") == "shortcut"]
        week = [c for c in self._sc_call("shortcut_changes", problems, [], days=7)
                if c["kind"] in ("added", "removed", "repointed")]
        last = {}
        for c in self._sc_call("shortcut_changes", problems, [], days=3650, limit=5000):
            last.setdefault(c["shortcut_id"], c)
        for r in rows:
            c = last.get(r["id"])
            r["last_change"] = {k: c[k] for k in ("at", "kind", "by_user", "how")} if c else None
        rows.sort(key=lambda r: (r["health"] != "broken", r["health"] != "unreachable", not r["risks"],
                                 f"{r['workspace_name']} {r['lakehouse_name']} {r['full_path']}".lower()))
        return {"pending": False, "rows": rows, "errors": inv.get("errors", {}), "problems": problems,
                "counts": center.counts(rows, week, requests), "needs": center.needs(rows, requests),
                "requests": requests, "checked_at": max((r["checked_at"] for r in rows), default="")}

    # ---- health

    def shortcut_check(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        """List one entry of each shortcut through OneLake: [{id, lakehouse_id, host_workspace_id,
        path, full_path, target_type}]. Records each result with the platform."""
        token = self.auth.storage_token()  # type: ignore[attr-defined]  # here: MSAL stays on one thread
        files = FileStore(lambda: token)
        hosts: dict[tuple[str, str, str], int | None] = {}
        lock = threading.Lock()

        def host_status(s: dict[str, Any]) -> int | None:
            key = (s["host_workspace_id"], s["lakehouse_id"], s["path"])
            with lock:
                if key in hosts:
                    return hosts[key]
            status, _ = files.probe(s["host_workspace_id"], s["lakehouse_id"], s["path"])
            with lock:
                hosts[key] = status
            return status

        def check(s: dict[str, Any]) -> dict[str, Any]:
            host = host_status(s)
            status, message = (None, "") if host in (401, 403) else files.probe(s["host_workspace_id"], s["lakehouse_id"],
                                                                                s["full_path"])
            return {"id": s["id"]} | center.health_from_probe(s, host, status, message)

        with ThreadPoolExecutor(max_workers=8) as pool:
            results = list(pool.map(check, items))
        problems: list[str] = []
        changes = self._sc_call("record_shortcut_health", problems, [], results=results)
        return {"results": results, "changes": changes, "problems": problems}

    # ---- who can read it

    def _sc_assignments(self, workspace_id: str, cache: dict[str, Any]) -> list[dict[str, Any]] | None:
        if workspace_id not in cache:
            try:
                cache[workspace_id] = self.fabric.list_role_assignments(workspace_id)  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001 - a Viewer can't list roles
                cache[workspace_id] = None
        return cache[workspace_id]

    def _sc_roles(self, api: FabricClient, workspace_id: str, item_id: str, cache: dict[str, Any],
                  problems: list[str]) -> list[dict[str, Any]]:
        key = f"{workspace_id}/{item_id}"
        if key not in cache:
            cache[key] = access_mod.read_roles(lambda: api.list_data_access_roles(workspace_id, item_id))
        roles, problem = cache[key]
        if problem:
            problems.append(problem)
        return roles

    def _sc_readers(self, s: dict[str, Any], api: FabricClient, cache: dict[str, Any]) -> dict[str, Any]:
        mine = self._sc_assignments(s["host_workspace_id"], cache)
        if mine is None:
            return {"mode": center.exposure(s), "here": None, "target": None, "gap": None, "missing": [],
                    "readers": [], "text": "You can't see the roles of the workspace it's in"}
        problems: list[str] = []
        here = access_mod.who_can_read("/" + s["full_path"], mine,
                                       self._sc_roles(api, s["host_workspace_id"], s["lakehouse_id"], cache, problems))
        there = None
        if s["target_type"] == "OneLake" and s.get("workspace_id") and s.get("item_id"):
            theirs = self._sc_assignments(s["workspace_id"], cache)
            if theirs is not None:
                there = access_mod.who_can_read("/" + str(s.get("target_path") or "").strip("/"), theirs,
                                                self._sc_roles(api, s["workspace_id"], s["item_id"], cache, problems))
        out = center.readers_gap(here, there, center.exposure(s)) | {"readers": here, "partial": bool(problems),
                                                                     "problem": "; ".join(problems) or None}
        if problems:
            out["text"] = f"Not complete: {out['text']}. {problems[0]}"
        return out

    def shortcut_readers(self, shortcut: dict[str, Any]) -> dict[str, Any]:
        """Who reads one shortcut, and whether the target lets them in."""
        return self._sc_readers(shortcut, self._scoped(ONELAKE_SCOPES), {})  # type: ignore[attr-defined]

    def shortcut_access(self, rows: list[dict[str, Any]], refresh: bool = False) -> dict[str, Any]:
        """Readers against the target for every shortcut, read in the background."""
        lock = self.__dict__.setdefault("_sc_access_lock", threading.Lock())
        with lock:
            cached = getattr(self, "_sc_access", None)
            if cached is not None and not refresh:
                return dict(cached)
            token = self.auth._token(ONELAKE_SCOPES)  # type: ignore[attr-defined]
            api = FabricClient(lambda: token)
            self._sc_access = {"pending": True, "items": {}}

        def run() -> None:
            cache: dict[str, Any] = {}
            items = {}
            for s in rows:
                try:
                    out = self._sc_readers(s, api, cache)
                    out.pop("readers", None)
                    items[s["id"]] = out
                except Exception as exc:  # noqa: BLE001
                    items[s["id"]] = {"mode": center.exposure(s), "text": str(exc), "gap": None}
            with lock:
                self._sc_access = {"pending": False, "items": items}

        threading.Thread(target=run, daemon=True).start()
        return {"pending": True, "items": {}}

    # ---- rules

    def _sc_admin(self) -> bool:
        try:
            return bool(self._catalog().call("whoami").get("admin"))  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            _log.info("Couldn't ask the shortcut catalog whether this user is an admin: %s", describe(exc))
            return False

    def shortcut_rules(self) -> dict[str, Any]:
        problems: list[str] = []
        rules = self._sc_call("shortcut_rules", problems, {})
        return {"rules": rules, "text": center.RULE_TEXT, "admin": self._sc_admin(), "problems": problems}

    def shortcut_save_rules(self, rules: dict[str, Any]) -> dict[str, Any]:
        self._catalog().call("save_shortcut_rules", rules=rules)  # type: ignore[attr-defined]
        return self.shortcut_rules()

    def _sc_new(self, workspace_id: str, workspace_name: str, source: dict[str, Any],
                target_workspace_name: str = "") -> dict[str, Any]:
        envs = self._sc_envs()
        kind = source.get("type")
        out: dict[str, Any] = {"host_env": center.env_of(workspace_id, workspace_name, envs),
                               "target_type": shortcuts_mod.TARGETS.get(kind or "", kind or ""),
                               "connection_id": source.get("connection_id"), "target_env": "", "product": None}
        if kind == "oneLake":
            out["target_env"] = center.env_of(source.get("workspace_id") or "", target_workspace_name, envs)
            key = (f"onelake:{source.get('workspace_id')}/{source.get('item_id')}/"
                   f"{str(source.get('path') or '').strip('/')}").lower()
            try:
                products = center.port_index(self._catalog().call("list_products"))  # type: ignore[attr-defined]
            except Exception:  # noqa: BLE001
                products = {}
            out["product"] = products.get(key)
        return out

    def shortcut_preflight(self, workspace_id: str, source: dict[str, Any], workspace_name: str = "",
                           target_workspace_name: str = "") -> dict[str, Any]:
        """What the rules say before a shortcut is made: {blocked, warnings}."""
        # The catalog is always there (a file on this PC until a platform is set up), so a failed
        # call means the rules are unknown, not that there are none: block until they can be read.
        try:
            rules = self._catalog().call("shortcut_rules")  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001
            return {"blocked": [f"The platform's shortcut rules couldn't be read, so nothing can be checked: {exc}. "
                                "Try again once the platform answers."], "warnings": [], "unchecked": True}
        return center.check_rules(rules, self._sc_new(workspace_id, workspace_name, source, target_workspace_name),
                                  self._sc_admin())

    def _sc_guard(self, workspace_id: str, source: dict[str, Any], workspace_name: str, target_workspace_name: str,
                  via_request: bool = False) -> list[str]:
        out = self.shortcut_preflight(workspace_id, source, workspace_name, target_workspace_name)
        blocked = [b for b in out["blocked"] if not (via_request and b.startswith("Only platform admins"))]
        if blocked:
            raise PermissionError("The platform's shortcut rules stop this: " + "; ".join(blocked))
        return out["warnings"]

    def _sc_log(self, kind: str, ref: dict[str, Any], before: dict[str, Any] | None,
                after: dict[str, Any] | None, note: str = "") -> str | None:
        """Record a change made here. Returns a problem to show, if the platform couldn't."""
        try:
            self._catalog().call("log_shortcut_change", kind=kind, shortcut=ref, before=before,  # type: ignore[attr-defined]
                                 after=after, note=note)
            return None
        except Exception as exc:  # noqa: BLE001 - the shortcut changed; only its history entry is missing
            return f"Done, but the change couldn't be recorded in the platform's history: {exc}"

    # ---- changes, repoint, undo

    def shortcut_changes(self, days: int = 30, shortcut: str = "") -> dict[str, Any]:
        problems: list[str] = []
        return {"changes": self._sc_call("shortcut_changes", problems, [], days=days, limit=2000, shortcut=shortcut),
                "problems": problems}

    @staticmethod
    def _sc_def(workspace_id: str, item_id: str, path: str, name: str, target: dict[str, Any]) -> dict[str, Any]:
        return {"workspace_id": workspace_id, "lakehouse_id": item_id, "path": path.strip("/"), "name": name,
                "target": target}

    def shortcut_repoint(self, workspace_id: str, item_id: str, path: str, name: str, source: dict[str, Any],
                         lakehouse_name: str = "", workspace_name: str = "", target_workspace_name: str = "",
                         note: str = "") -> dict[str, Any]:
        """Point a shortcut somewhere else. Fabric can't change a shortcut's target, so it's removed
        and made again with the same name; if making it fails, the old one is put back."""
        api = self._scoped(ONELAKE_SCOPES)  # type: ignore[attr-defined]
        current = next((shortcuts_mod.describe(s) for s in api.list_shortcuts(workspace_id, item_id)
                        if str(s.get("path") or "").strip("/") == path.strip("/") and s.get("name") == name), None)
        if current is None:
            raise LookupError(f"Shortcut {name} isn't in {lakehouse_name or 'that lakehouse'} any more")
        new = shortcuts_mod.target(source)
        if center.same_definition(current["rest_target"], new):
            raise ValueError("It already points there")
        warnings = self._sc_guard(workspace_id, source, workspace_name, target_workspace_name)
        api.delete_shortcut(workspace_id, item_id, path, name)
        ref = {"workspace_id": workspace_id, "lakehouse_id": item_id, "workspace_name": workspace_name,
               "lakehouse_name": lakehouse_name, "path": path, "name": name}
        before = self._sc_def(workspace_id, item_id, path, name, current["rest_target"])
        try:
            api.create_shortcut(workspace_id, item_id, path, name, new)
        except Exception as exc:  # noqa: BLE001
            try:
                api.create_shortcut(workspace_id, item_id, path, name, current["rest_target"])
                again = "It was put back as it was."
            except Exception:  # noqa: BLE001
                self._sc_log("removed", ref, before, None, "Repoint failed and the old shortcut couldn't be put back")
                again = "Putting it back failed too: use Recreate on Shortcuts > Changes."
            self._all_shortcuts = None
            raise RuntimeError(f"Not repointed: {exc}. {again}") from exc
        self._all_shortcuts = None
        problem = self._sc_log("repointed", ref, before, self._sc_def(workspace_id, item_id, path, name, new), note)
        return {"ok": True, "warnings": warnings, "problems": [problem] if problem else []}

    def shortcut_undo(self, change_id: str) -> dict[str, Any]:
        """Undo a change: put a removed shortcut back, point a repointed one back, or remove an added one."""
        changes = self._catalog().call("shortcut_changes", days=3650, limit=100000)  # type: ignore[attr-defined]
        c = next((x for x in changes if x["id"] == change_id), None)
        if c is None:
            raise LookupError("That change isn't in the history any more")
        api = self._scoped(ONELAKE_SCOPES)  # type: ignore[attr-defined]
        if c["kind"] == "removed" and c.get("before"):
            b = c["before"]
            ref = {"workspace_id": b["workspace_id"], "lakehouse_id": b["lakehouse_id"], "path": b["path"],
                   "name": b["name"], "workspace_name": c["workspace_name"], "lakehouse_name": c["lakehouse_name"]}
            api.create_shortcut(b["workspace_id"], b["lakehouse_id"], b["path"], b["name"], b["target"])
            self._all_shortcuts = None
            problem = self._sc_log("added", ref, None, b, f"Recreated (undo of change {change_id})")
        elif c["kind"] == "repointed" and c.get("before"):
            b = c["before"]
            ref = {"workspace_id": b["workspace_id"], "lakehouse_id": b["lakehouse_id"], "path": b["path"],
                   "name": b["name"], "workspace_name": c["workspace_name"], "lakehouse_name": c["lakehouse_name"]}
            current = next((shortcuts_mod.describe(s) for s in api.list_shortcuts(b["workspace_id"], b["lakehouse_id"])
                            if str(s.get("path") or "").strip("/") == b["path"] and s.get("name") == b["name"]), None)
            if current is not None:
                api.delete_shortcut(b["workspace_id"], b["lakehouse_id"], b["path"], b["name"])
            try:
                api.create_shortcut(b["workspace_id"], b["lakehouse_id"], b["path"], b["name"], b["target"])
            except Exception as exc:  # noqa: BLE001
                if current is not None:
                    api.create_shortcut(b["workspace_id"], b["lakehouse_id"], b["path"], b["name"], current["rest_target"])
                self._all_shortcuts = None
                raise RuntimeError(f"Not undone: {exc}. The shortcut is as it was.") from exc
            self._all_shortcuts = None
            after = self._sc_def(b["workspace_id"], b["lakehouse_id"], b["path"], b["name"],
                                 current["rest_target"] if current else {})
            problem = self._sc_log("repointed", ref, after, b, f"Pointed back (undo of change {change_id})")
        elif c["kind"] == "added" and c.get("after"):
            a = c["after"]
            ref = {"workspace_id": a["workspace_id"], "lakehouse_id": a["lakehouse_id"], "path": a["path"],
                   "name": a["name"], "workspace_name": c["workspace_name"], "lakehouse_name": c["lakehouse_name"]}
            api.delete_shortcut(a["workspace_id"], a["lakehouse_id"], a["path"], a["name"])
            self._all_shortcuts = None
            problem = self._sc_log("removed", ref, a, None, f"Removed (undo of change {change_id})")
        else:
            raise ValueError("Only an added, removed or repointed shortcut can be undone")
        return {"ok": True, "problems": [problem] if problem else []}

    # ---- requests

    def shortcut_request(self, workspace_id: str, item_id: str, where: str, name: str, source: dict[str, Any],
                         reason: str = "", lakehouse_name: str = "", workspace_name: str = "",
                         target_workspace_name: str = "") -> dict[str, Any]:
        """Ask a platform admin (or the data product's owner) for a shortcut."""
        where = shortcuts_mod.location(where)
        name = shortcuts_mod.check_name(name)
        shortcuts_mod.target(source)  # a request that couldn't be made is refused now, not on approval
        new = self._sc_new(workspace_id, workspace_name, source, target_workspace_name)
        detail = {"workspace_id": workspace_id, "item_id": item_id, "where": where, "name": name, "source": source,
                  "lakehouse_name": lakehouse_name, "workspace_name": workspace_name,
                  "target_workspace_name": target_workspace_name, "target_type": new["target_type"],
                  "product_id": (new.get("product") or {}).get("id", "")}
        return self.platform_request("shortcut", f"{lakehouse_name}/{where}/{name}", reason, detail)  # type: ignore[attr-defined]

    def platform_decide(self, id: str, approve: bool, note: str = "") -> dict[str, Any]:
        """Approving a shortcut request makes the shortcut first, as the person approving, so a
        request is only approved once its shortcut exists."""
        if approve:
            req = next((r for r in self._catalog().call("list_requests", status="waiting")  # type: ignore[attr-defined]
                        if r["id"] == id), None)
            if req is not None and req["kind"] == "shortcut":
                if not req.get("can_decide"):
                    raise PermissionError("Only a platform admin, or the owner of the product it points at, can approve this")
                d = req["detail"]
                self.create_shortcut(d["workspace_id"], d["item_id"], d["where"], d["name"], d["source"],  # type: ignore[attr-defined]
                                     d.get("lakehouse_name", ""), workspace_name=d.get("workspace_name", ""),
                                     target_workspace_name=d.get("target_workspace_name", ""),
                                     note=f"Request from {req.get('requested_by') or 'someone'}: {req.get('reason') or ''}".strip(": "),
                                     via_request=True)
        return super().platform_decide(id, approve, note)  # type: ignore[misc]
