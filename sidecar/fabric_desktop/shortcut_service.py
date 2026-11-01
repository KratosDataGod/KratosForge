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
from . import governance_notices as notices
from . import shortcut_center as center
from . import shortcut_watch as watch
from . import shortcuts as shortcuts_mod
from .auth import ONELAKE_SCOPES
from .central import core
from .errors import NotSetUp
from .fabric_api import FabricClient, FabricError
from .meta.project import ENVIRONMENTS
from .onelake import FileStore
from .redact import describe

_log = logging.getLogger(__name__)
RISKS_REPAIR = "After an update, a platform admin runs Platform > Settings > Finish or repair once."



def _skipped_problem(skipped: list[dict[str, Any]]) -> str:
    """What the platform didn't record from this PC, said once."""
    return (f"The platform didn't record {len(skipped)} change{'' if len(skipped) == 1 else 's'} to shortcuts "
            "(removed, repointed or no longer broken): only a platform admin, or a member of a project with a "
            "product in that lakehouse, can. The next scan by one of them records it.")

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
        lh_names = {l["lakehouse_id"]: l.get("name", "") for l in lakehouses}
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
            recorded = self._sc_call("record_shortcuts", problems, {}, lakehouses=scanned, shortcuts=[
                {"lakehouse_id": r["lakehouse_id"], "workspace_id": r["host_workspace_id"],
                 "workspace_name": r["workspace_name"], "lakehouse_name": r["lakehouse_name"], "path": r["path"],
                 "name": r["name"], "target": r["rest_target"],
                 # the target's names while it's there, so it can be named (and found) once it's gone
                 "target_workspace_name": names.get(r.get("workspace_id") or "", "") if r["target_type"] == "OneLake" else "",
                 "target_lakehouse_name": lh_names.get(r.get("item_id") or "", "") if r["target_type"] == "OneLake" else ""}
                for r in rows])
            if (recorded or {}).get("skipped"):
                problems.append(_skipped_problem(recorded["skipped"]))
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
            if r["target_type"] == "OneLake":
                r["target_workspace_name"] = names.get(r.get("workspace_id") or "") or stored.get("target_workspace_name") or ""
                r["target_lakehouse_name"] = lh_names.get(r.get("item_id") or "") or stored.get("target_lakehouse_name") or ""
        flagged = center.risks(rows, envs)
        for r in rows:
            r["risks"] = flagged.get(r["id"], [])
            r["same_env_target"] = (center.same_env_target(r, envs, lakehouses)
                                    if any(x["kind"] == "cross_env" for x in r["risks"]) else None)
        center.accept_risks(rows, self._sc_accepted(problems))
        requests = [q for q in self._sc_call("list_requests", problems, []) if q.get("kind") == "shortcut"]
        week = [c for c in self._sc_call("shortcut_changes", problems, [], days=7)
                if c["kind"] in ("added", "removed", "repointed")]
        last = {}
        for c in self._sc_call("shortcut_changes", problems, [], days=3650, limit=5000):
            last.setdefault(c["shortcut_id"], c)
        for r in rows:
            c = last.get(r["id"])
            r["last_change"] = {k: c[k] for k in ("at", "kind", "by_user", "how")} if c else None
        rows.sort(key=lambda r: (r["health"] != "broken", r["health"] != "unreachable", not center.open_risks(r),
                                 f"{r['workspace_name']} {r['lakehouse_name']} {r['full_path']}".lower()))
        return {"pending": False, "rows": rows, "errors": inv.get("errors", {}), "problems": problems,
                "counts": center.counts(rows, week, requests), "needs": center.needs(rows, requests),
                "requests": requests, "checked_at": max((r["checked_at"] for r in rows), default=""),
                "admin": self._sc_admin()}

    # ---- health

    def shortcut_check(self, items: list[dict[str, Any]]) -> dict[str, Any]:
        """List one entry of each shortcut through OneLake: [{id, lakehouse_id, host_workspace_id,
        path, full_path, target_type}]. Records each result with the platform."""
        token = self.auth.storage_token()  # type: ignore[attr-defined]  # here: MSAL stays on one thread
        results = watch.probe(FileStore(lambda: token), items)  # the same check the agent's schedule makes
        problems: list[str] = []
        changes = self._sc_call("record_shortcut_health", problems, [], results=results)
        skipped = [c for c in changes if c.get("kind") == "skipped"]
        changes = [c for c in changes if c.get("kind") != "skipped"]
        if skipped:
            problems.append(_skipped_problem(skipped))
        problem = self._governance_notify(lambda: notices.shortcut_events(changes))  # type: ignore[attr-defined]
        if problem:
            problems.append(problem)
        return {"results": results, "changes": changes, "problems": problems}

    # ---- a lakehouse that's gone: find where it went, repoint many at once

    def _sc_lakehouses(self) -> list[dict[str, str]]:
        """Every lakehouse in every workspace the person can list, as the catalog lists them."""
        token = self.auth.fabric_token()  # type: ignore[attr-defined]  # once: MSAL stays on one thread
        api = FabricClient(lambda: token)

        def listing(ws: dict[str, Any]) -> list[dict[str, str]]:
            try:
                found = FabricClient(lambda: token).list_lakehouses(ws["id"])
            except Exception as exc:  # noqa: BLE001 - a paused capacity, no access: list the rest
                _log.info("Couldn't list the lakehouses of workspace %s: %s", ws.get("id"), describe(exc))
                return []
            return [{"workspace_id": ws["id"], "workspace_name": ws.get("displayName", ""), "lakehouse_id": lh["id"],
                     "lakehouse_name": lh.get("displayName", "")} for lh in found]

        with ThreadPoolExecutor(max_workers=8) as pool:
            return [l for found in pool.map(listing, api.list_workspaces()) for l in found]

    def shortcut_find_moved(self, shortcuts: list[dict[str, Any]]) -> dict[str, Any]:
        """Broken OneLake shortcuts whose target lakehouse is gone: a lakehouse with its name that the
        person can list, and which of the shortcuts' tables or folders are there, asked of OneLake
        the way a health check asks. Reads nothing else and changes nothing.
        Returns {candidate: {workspace_id, workspace_name, lakehouse_id, lakehouse_name} or None,
        found: [shortcut ids], missing: [shortcut ids]}."""
        name = next((s.get("target_lakehouse_name") for s in shortcuts if s.get("target_lakehouse_name")), "")
        gone = next((s.get("item_id") for s in shortcuts if s.get("item_id")), "")
        empty = {"candidate": None, "found": [], "missing": [s["id"] for s in shortcuts], "name": name}
        if not name or not shortcuts:
            return empty
        candidates = center.moved_candidates(name, gone, self._sc_lakehouses())[:5]
        if not candidates:
            return empty
        token = self.auth.storage_token()  # type: ignore[attr-defined]
        files = FileStore(lambda: token)

        def there(pair: tuple[dict[str, str], dict[str, Any]]) -> bool:
            cand, s = pair
            status, _ = files.probe(cand["workspace_id"], cand["lakehouse_id"], str(s.get("target_path") or "").strip("/"))
            return status is not None and 200 <= status < 300

        best: tuple[dict[str, str], list[str]] | None = None
        for cand in candidates:
            with ThreadPoolExecutor(max_workers=8) as pool:
                hits = list(pool.map(there, [(cand, s) for s in shortcuts]))
            found = [s["id"] for s, hit in zip(shortcuts, hits) if hit]
            if best is None or len(found) > len(best[1]):
                best = (cand, found)
        cand, found = best  # type: ignore[misc]
        return {"candidate": cand, "found": found, "missing": [s["id"] for s in shortcuts if s["id"] not in found],
                "name": name}

    def shortcut_repoint_many(self, items: list[dict[str, Any]], workspace_id: str, lakehouse_id: str,
                              workspace_name: str = "", lakehouse_name: str = "") -> dict[str, Any]:
        """Repoint several OneLake shortcuts at one lakehouse. Each keeps its name, place and target
        path; only the lakehouse changes. One at a time with shortcut_repoint, so one that fails is
        put back as it was and the rest carry on. ``items``: the command center's rows."""
        done: list[str] = []
        failed: list[dict[str, str]] = []
        warnings: list[str] = []
        problems: list[str] = []
        note = f"Repointed with {len(items) - 1} more at {lakehouse_name or lakehouse_id}" if len(items) > 1 else ""
        for s in items:
            source = {"type": "oneLake", "workspace_id": workspace_id, "item_id": lakehouse_id,
                      "path": str(s.get("target_path") or "").strip("/")}
            try:
                out = self.shortcut_repoint(s["host_workspace_id"], s["lakehouse_id"], s["path"], s["name"], source,
                                            s.get("lakehouse_name", ""), s.get("workspace_name", ""), workspace_name, note)
            except Exception as exc:  # noqa: BLE001 - one failure doesn't stop the rest
                _log.info("Repointing shortcut %s failed: %s", s.get("name"), describe(exc))
                failed.append({"name": str(s.get("name")), "error": str(exc)})
                continue
            done.append(str(s["name"]))
            warnings += [w for w in out.get("warnings") or [] if w not in warnings]
            problems += [p for p in out.get("problems") or [] if p not in problems]
        return {"done": done, "failed": failed, "warnings": warnings, "problems": problems}

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

    def _sc_risk_call(self, op: str, **kwargs: Any) -> Any:
        """Accepted risks came with an update: a UDF_Platform made before it has no such function,
        and Fabric answers 404, which the client reads as "UDF_Platform wasn't found". Say what to do."""
        missing = f"The platform can't keep accepted shortcut risks yet: UDF_Platform has no {op} function. {RISKS_REPAIR}"
        try:
            return self._catalog().call(op, **kwargs)  # type: ignore[attr-defined]
        except NotSetUp:
            raise  # its own words say what to do; Finish or repair wouldn't help
        except LookupError as exc:
            _log.info("%s: %s", op, describe(exc))
            raise LookupError(missing) from exc
        except core.PlatformError as exc:
            if op in str(exc) and ("not found" in str(exc).lower() or "no operation" in str(exc).lower()):
                _log.info("%s: %s", op, describe(exc))
                raise LookupError(missing) from exc
            raise

    def _sc_accepted(self, problems: list[str]) -> list[dict[str, Any]]:
        """The accepted risks; none, with a problem line, when the platform can't say."""
        try:
            return self._sc_risk_call("list_shortcut_risks")
        except Exception as exc:  # noqa: BLE001 - the shortcuts still show, every risk flagged
            msg = str(exc) if isinstance(exc, LookupError) else f"The platform couldn't list shortcut risks: {exc}"
            if msg not in problems:
                problems.append(msg)
            return []

    def shortcut_accept_risk(self, shortcut_id: str, kind: str, reason: str) -> dict[str, Any]:
        """A platform admin accepts a flagged risk (Dev reading Prod on purpose), and says why."""
        return self._sc_risk_call("accept_shortcut_risk", shortcut_id=shortcut_id, kind=kind, reason=reason)

    def shortcut_withdraw_risk(self, shortcut_id: str, kind: str) -> dict[str, Any]:
        """Take back an accepted risk, so the shortcut is flagged again."""
        return {"accepted": self._sc_risk_call("withdraw_shortcut_risk", shortcut_id=shortcut_id, kind=kind)}

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
        if "undoable" not in c:
            raise RuntimeError("Not undone: the platform needs Platform > Settings > Finish or repair once first.")
        if not c["undoable"]:
            raise RuntimeError("Not undone: only a change made by a platform admin, or by a member of a project "
                               "with a product in that lakehouse, can be undone. Nothing was changed.")
        api = self._scoped(ONELAKE_SCOPES)  # type: ignore[attr-defined]

        def now(d: dict[str, Any]) -> dict[str, Any] | None:
            """The shortcut at that place in Fabric now, if there is one."""
            return next((shortcuts_mod.describe(x) for x in api.list_shortcuts(d["workspace_id"], d["lakehouse_id"])
                         if str(x.get("path") or "").strip("/") == d["path"] and x.get("name") == d["name"]), None)

        def same(found: dict[str, Any] | None, target: Any) -> bool:
            return found is not None and core._canon(found["rest_target"]) == core._canon(target)

        # Undo changes only what the change made: a shortcut changed again since is left alone.
        where = lambda d: f"{d['path']}/{d['name']}"  # noqa: E731
        if c["kind"] == "removed" and c.get("before"):
            b = c["before"]
            ref = {"workspace_id": b["workspace_id"], "lakehouse_id": b["lakehouse_id"], "path": b["path"],
                   "name": b["name"], "workspace_name": c["workspace_name"], "lakehouse_name": c["lakehouse_name"]}
            if now(b) is not None:
                raise RuntimeError(f"Not undone: there's a shortcut at {where(b)} again, so nothing was changed.")
            api.create_shortcut(b["workspace_id"], b["lakehouse_id"], b["path"], b["name"], b["target"])
            self._all_shortcuts = None
            problem = self._sc_log("added", ref, None, b, f"Recreated (undo of change {change_id})")
        elif c["kind"] == "repointed" and c.get("before"):
            b = c["before"]
            ref = {"workspace_id": b["workspace_id"], "lakehouse_id": b["lakehouse_id"], "path": b["path"],
                   "name": b["name"], "workspace_name": c["workspace_name"], "lakehouse_name": c["lakehouse_name"]}
            current = now(b)
            if not same(current, (c.get("after") or {}).get("target")):
                raise RuntimeError(f"Not undone: {where(b)} " + ("is gone" if current is None else "points somewhere else")
                                   + " since that change, so nothing was changed.")
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
            current = now(a)
            if not same(current, a.get("target")):
                raise RuntimeError(f"Not undone: {where(a)} " + ("is already gone" if current is None else
                                   "points somewhere else now") + ", so nothing was removed.")
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
                why = f"Request from {req.get('requested_by') or 'someone'}: {req.get('reason') or ''}".strip(": ")
                try:
                    self._create_shortcut(d["workspace_id"], d["item_id"], d["where"], d["name"], d["source"],  # type: ignore[attr-defined]
                                         d.get("lakehouse_name", ""), workspace_name=d.get("workspace_name", ""),
                                         target_workspace_name=d.get("target_workspace_name", ""), note=why,
                                         via_request=True)
                except FabricError as exc:
                    if exc.status != 409 and "AlreadyExists" not in str(exc.code or ""):
                        raise
                    ours = self._sc_made_before(req, why, exc)
                    self._sc_progress(req, found=not ours)
                    out = super().platform_decide(id, approve, note)  # type: ignore[misc]
                    return out | {"next": [*(out.get("next") or []), f"{d['name']} was already in "
                                           f"{d.get('lakehouse_name') or 'the lakehouse'}, pointing where this request "
                                           "asks, so it was kept"]}
                self._sc_progress(req)
        return super().platform_decide(id, approve, note)  # type: ignore[misc]

    def _sc_progress(self, req: dict[str, Any], found: bool = False) -> None:
        """Record the shortcut on the request, so declining it after a later step fails says it's in OneLake.
        ``found``: it was there already and this request's own approval hadn't logged making it, so it may be
        one made by hand before the request (or one whose create answer was lost): a decline says to check."""
        d = req["detail"]
        sid = core.shortcut_id(d["item_id"], f"{shortcuts_mod.location(d['where'])}/{d['name']}")
        self._rq_progress(req, {"shortcut_id": sid, **({"found": True} if found else {})})  # type: ignore[attr-defined]

    def _sc_made_before(self, req: dict[str, Any], why: str, conflict: Exception) -> bool:
        """Fabric refused a shortcut request's shortcut because one is there already. It counts as made only
        when it points exactly where the request asks: an earlier approval made it and its answer, or the
        approval after it, was lost (#1052). Its "added" entry is recorded once. True when this request's own
        approval had already logged making it; otherwise it may have been there before the request (#1260)."""
        d = req["detail"]
        api = self._scoped(ONELAKE_SCOPES)  # type: ignore[attr-defined]
        where = shortcuts_mod.location(d["where"])
        there = next((s for s in api.list_shortcuts(d["workspace_id"], d["item_id"])
                      if str(s.get("path") or "").strip("/") == where and s.get("name") == d["name"]), None)
        if there is None:
            raise conflict
        wanted = shortcuts_mod.target(d["source"])
        if not shortcuts_mod.same_target(there.get("target") or {}, wanted):
            found = shortcuts_mod.describe(there)
            raise core.PlatformError(
                f"{d.get('lakehouse_name') or 'The lakehouse'} already has a shortcut {where}/{d['name']}, pointing at "
                f"{found['target_type']} {found['target'] or '(no path)'}, not where this request asks. Decline the "
                "request, or remove that shortcut and approve it again.") from conflict
        sid = core.shortcut_id(d["item_id"], f"{where}/{d['name']}")
        since = str(req.get("requested_at") or "")
        logged = self._catalog().call("shortcut_changes", days=3650, shortcut=sid)  # type: ignore[attr-defined]
        ours = any(c.get("kind") == "added" and str(c.get("at") or "") >= since for c in logged)
        if not ours:
            ref = {"workspace_id": d["workspace_id"], "lakehouse_id": d["item_id"], "path": where, "name": d["name"],
                   "workspace_name": d.get("workspace_name", ""), "lakehouse_name": d.get("lakehouse_name", "")}
            self._sc_log("added", ref, None, self._sc_def(d["workspace_id"], d["item_id"], where, d["name"], wanted), why)
        self._all_shortcuts = None
        return ours
