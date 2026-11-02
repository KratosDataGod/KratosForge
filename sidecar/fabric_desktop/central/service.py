"""The platform and the data mesh, as the desktop UI calls them.

Platform: which Platform workspace each environment uses (from IT's policy.json, else this
PC's settings), creating or joining one, and moving keys into SQL_Platform. Without a
platform in Fabric, everything runs against a SQLite platform on this PC, so a single
engineer can use domains, products and the catalog before the central team exists.

Mesh: the open project's domain, data products, links and catalog text.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
from pathlib import Path
from typing import Any

from .. import app_settings, jsonfile
from .. import governance_notices as notices
from ..auth import EXECUTE_SCOPES, TENANT_READ_SCOPES, TENANT_WRITE_SCOPES
from ..fabric_api import FabricClient, FabricError
from ..meta import keys as keys_mod
from ..meta import mesh
from ..meta.project import ENVIRONMENTS, GateError
from ..onelake import FileStore, TableRef
from ..errors import NotSetUp
from ..redact import describe
from . import core, home, keys_home
from . import setup as setup_mod
from .client import FabricPlatform, LocalPlatform, PlatformKeys
from .core import domain_id
from .migrate import migrate
from .requests_service import DOMAIN_DESCRIPTION, call_or_repair, marked

_log = logging.getLogger(__name__)

CATALOG_ENV = "prod"  # domains, glossary and catalog text don't depend on the environment: they live in Prod's


def _unknown_argument(exc: Exception) -> bool:
    """Whether register_link was refused for naming accepts or columns, as a function older than
    versioned contracts does. The catalog's own accepts refusal is checked on this PC first, so it
    never gets here; any other refusal is real and is raised."""
    message = str(exc)
    return ("accepts" in message or "columns" in message) and "accepts is a version" not in message


class PlatformMixin:
    root: Path

    # ---- which platform

    def _platform_config(self) -> dict[str, dict[str, Any]]:
        policy = app_settings.value("platform") or {}
        mine = self._settings().get("platform", {})  # type: ignore[attr-defined]
        return {env: dict(policy.get(env) or mine.get(env) or {}) for env in ENVIRONMENTS}

    def _platform_source(self) -> str:
        return "policy" if app_settings.load()["platform"]["source"] == "policy" else "user"

    def _user(self) -> str:
        try:
            return self.auth.signed_in_user() or ""  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - offline: changes are recorded without a name
            _log.info("Signed-in user unknown; recording the change without a name: %s", describe(exc))
            return ""

    def _platform(self, env: str) -> LocalPlatform | FabricPlatform:
        cfg = self._platform_config().get(env, {})
        if cfg.get("functions_id"):
            return FabricPlatform(lambda: self.auth._token(EXECUTE_SCOPES),  # type: ignore[attr-defined]
                                  cfg["workspace_id"], cfg["functions_id"])
        cached = getattr(self, "_local_platforms", None)
        if cached is None:
            self._local_platforms: dict[str, LocalPlatform] = {}
            cached = self._local_platforms
        if env not in cached:
            cached[env] = LocalPlatform(self.root / "platform" / f"platform-{env}.sqlite", self._user)
        return cached[env]

    def _catalog(self) -> LocalPlatform | FabricPlatform:
        return self._platform(CATALOG_ENV)

    def _platform_keys(self, env: str) -> PlatformKeys:
        return PlatformKeys(self._keys_platform(env), env)

    # ---- one keys database for every environment (keys_home.py)

    def _keys_config(self) -> dict[str, Any]:
        """The Keys workspace Dev, Test and Prod share; ``shared`` is set once keys have moved in."""
        policy = app_settings.value("platform") or {}
        mine = self._settings().get("platform", {})  # type: ignore[attr-defined]
        return dict(policy.get("keys") or mine.get("keys") or {})

    def _shared_keys(self) -> LocalPlatform | FabricPlatform:
        cfg = self._keys_config()
        if cfg.get("functions_id"):
            return FabricPlatform(lambda: self.auth._token(EXECUTE_SCOPES),  # type: ignore[attr-defined]
                                  cfg["workspace_id"], cfg["functions_id"])
        cached = getattr(self, "_local_shared_keys", None)
        if cached is None:
            cached = self._local_shared_keys = LocalPlatform(self._local_keys_path(), self._user)
        return cached

    def _local_keys_path(self) -> Path:
        return self.root / "platform" / "keys.sqlite"

    def _keys_platform(self, env: str) -> LocalPlatform | FabricPlatform:
        """Where an environment's platform keys are: the shared keys database, else its own platform."""
        return self._shared_keys() if self._keys_config().get("shared") else self._platform(env)

    def _keys_in_fabric(self, env: str) -> bool:
        cfg = self._keys_config() if self._keys_config().get("shared") else self._platform_config().get(env, {})
        return bool(cfg.get("functions_id"))

    def _key_platforms(self) -> list[tuple[str, LocalPlatform | FabricPlatform]]:
        """Every database a new key table goes in: the shared one, else each environment's platform."""
        return [("keys", self._shared_keys())] if self._keys_config().get("shared") else self._distinct_platforms()

    def platform_keys_setup(self, name: str = "", capacity_id: str = "") -> dict[str, Any]:
        """Create (or finish creating) the Keys workspace. Keys move in only with platform_share_keys."""
        out = keys_home.setup(self.fabric, lambda: self.auth._token(EXECUTE_SCOPES),  # type: ignore[attr-defined]
                              name, capacity_id, self._keys_config())
        self._save_platform("keys", out["config"])
        if out["ready"]:
            self._adopt_live_keys()  # a Keys workspace someone else already moved keys into
        return out | {"status": self.platform_status()}

    def platform_keys_join(self, workspace_id: str) -> dict[str, Any]:
        """Use a Keys workspace someone already made. If its keys are already in use, this PC uses them too."""
        self._save_platform("keys", self._keys_config() | keys_home.join(self.fabric, workspace_id))  # type: ignore[attr-defined]
        self._adopt_live_keys()
        return self.platform_status()

    def _adopt_live_keys(self) -> bool:
        """Switch this PC to the shared keys database if another admin's move already made it live."""
        cfg = self._keys_config()
        if cfg.get("shared"):
            return True
        if not cfg.get("functions_id") and not self._local_keys_path().exists():
            return False  # no keys database on this PC yet, and looking must not create one
        if not self._shared_keys().call("keys_state").get("live"):
            return False
        self._save_platform("keys", cfg | {"shared": True})
        return True

    def _share_sources(self) -> list[tuple[str, LocalPlatform | FabricPlatform]]:
        """Each environment's platform once, Prod first, so a database it shares with Dev keeps Prod's numbers."""
        sources, seen = [], set()
        cfg = self._platform_config()
        for env in reversed(ENVIRONMENTS):
            if not cfg[env].get("functions_id") and not (self.root / "platform" / f"platform-{env}.sqlite").exists():
                continue  # no platform on this PC for it, so no keys to move (and looking must not create one)
            client = self._platform(env)
            ident = getattr(client, "functions_id", None) or str(getattr(client, "path", env))
            if ident not in seen:
                seen.add(ident)
                sources.append((env, client))
        return sources

    def _check_can_share(self) -> dict[str, Any]:
        if self._platform_source() == "policy":
            raise PermissionError("Your organization sets the platform, so keys can't be moved from this PC")
        cfg = self._keys_config()
        if not cfg.get("functions_id") and any(c.get("functions_id") for c in self._platform_config().values()):
            # Fabric can't reach a database on this PC, so keys already in Fabric never move here.
            raise LookupError("Create or join the Keys workspace first, in Settings under Platform, "
                              "then move the keys in.")
        return cfg

    def platform_share_keys(self, approve: bool = False) -> dict[str, Any]:
        """Move every environment's key tables into the one keys database and use it from then on.

        Without ``approve`` this only reports what would happen: each key table's keys and how many
        Dev and Test keys get a different number. With it, every environment's keys close first, so
        no run issues a key while they're copied; Prod's keep their numbers. The switch happens only
        if every key table checked out and no environment's keys changed during the move. A move
        that stops leaves the keys closed, so running it again copies the same keys."""
        if self._keys_config().get("shared"):
            return {"ok": True, "already": True, "tables": [], "renumbered": 0, "status": self.platform_status()}
        cfg = self._check_can_share()
        if self._adopt_live_keys():  # another admin's move: the keys are already in use
            return {"ok": True, "already": True, "tables": [], "renumbered": 0, "status": self.platform_status()}
        sources = self._share_sources()
        if not approve:
            return keys_home.plan(sources) | {"ok": False, "preview": True, "message": (
                f"Every environment's keys close while they move into {cfg.get('workspace_name') or 'the keys database on this PC'}, "
                "so runs that need new keys wait. Prod's keys keep their numbers; Dev and Test keys that change "
                "need one re-run of their tables. Confirm to continue.")}
        shared = self._shared_keys()
        if shared.call("whoami").get("can_claim"):
            shared.call("claim_admin")
        admins = {a["user_name"] for a in shared.call("list_admins")}
        for _, client in sources:  # the platform admins come along, so they can still add key tables
            for admin in client.call("list_admins"):
                # An entry that isn't a sign-in name never matched anyone, so it stays behind.
                if admin["user_name"] not in admins and core.is_sign_in_name(admin["user_name"]):
                    shared.call("add_admin", user=admin["user_name"])
                    admins.add(admin["user_name"])
        where = cfg.get("workspace_name") or "the keys database on this PC"
        closed: list[LocalPlatform | FabricPlatform] = []
        for env, client in sources:
            try:
                client.call("close_keys", reason=f"Keys are moving to {where}. Runs that need new keys can run "
                                                 "again once the move finishes.")
                closed.append(client)
            except Exception as exc:  # noqa: BLE001 - an older platform: nothing has moved yet
                for done in closed:
                    done.call("close_keys", reason="")
                if isinstance(exc, (NotSetUp, PermissionError)):
                    raise  # not an older platform: Finish or repair wouldn't help
                raise LookupError(f"{env.title()}'s platform can't close its keys for the move ({exc}). Run Finish "
                                  "or repair on it in Settings under Platform, then move the keys again.") from exc
        stopped = ("Nothing switched, and every environment's keys stay closed so they can't change. "
                   "Move the keys again, or cancel the move to reopen them.")
        try:
            shared.call("stage_keys", staging=True)
            before = keys_home.snapshot(sources)
            report = keys_home.share(sources, shared)
        except Exception as exc:  # noqa: BLE001 - the keys stay closed, so the move can simply run again
            _log.warning("Moving keys stopped: %s", describe(exc))
            return {"ok": False, "tables": [], "renumbered": 0, "error": describe(exc),
                    "message": f"The move stopped: {describe(exc)}. {stopped}", "status": self.platform_status()}
        changed = [f"{env.title()} {name}" for (env, name), v in keys_home.snapshot(sources).items() if before.get((env, name)) != v]
        if changed:
            report |= {"ok": False, "changed_during_move": changed}
        if report["ok"]:
            shared.call("stage_keys", staging=False, live=True)
            self._save_platform("keys", cfg | {"shared": True, "moved_at": dt.datetime.now(dt.timezone.utc)
                                                .isoformat(timespec="seconds")})
            for _, client in sources:
                client.call("close_keys", reason=f"Keys moved to {where}, one keys database for Dev, Test and "
                                                 "Prod. Join it in Settings under Platform.")
        else:
            report["message"] = stopped
        return report | {"status": self.platform_status()}

    def platform_cancel_share_keys(self, approve: bool = False) -> dict[str, Any]:
        """Give up a move that didn't switch: empty what it copied and reopen every environment's keys."""
        self._check_can_share()
        if self._adopt_live_keys():
            raise GateError("save", ["The keys already moved and are in use, so the move can't be cancelled."])
        if not approve:
            tables = [t["name"] for t in self._shared_keys().call("list_key_tables")]
            return {"ok": False, "preview": True, "tables": tables, "message": (
                f"The move never switched, so nothing uses these key tables yet: {', '.join(tables) or 'none'}. "
                "They're removed from the keys database, and every environment issues its own keys again. "
                "Confirm to continue.")}
        cleared = self._shared_keys().call("clear_keys")
        for _, client in self._share_sources():
            client.call("close_keys", reason="")
        return {"ok": True, **cleared, "status": self.platform_status()}

    def platform_status(self) -> dict[str, Any]:
        cfg = self._platform_config()
        keys = self._keys_config()
        return {"source": self._platform_source(), "default_names": {e: setup_mod.default_name(e) for e in ENVIRONMENTS},
                "environments": {env: {"kind": "fabric" if c.get("functions_id") else "local", **c}
                                 for env, c in cfg.items()},
                "keys": {"kind": "fabric" if keys.get("functions_id") else "local", "shared": bool(keys.get("shared")),
                         "default_name": keys_home.DEFAULT_NAME, **keys},
                "landing": {"central": bool(self._central_landing().get("workspace_id")),
                            "default_name": setup_mod.LANDING_WORKSPACE, **self._central_landing()},
                "local_path": str(self.root / "platform")}

    def _save_platform(self, env: str, cfg: dict[str, Any]) -> None:
        if self._platform_source() == "policy":
            raise PermissionError("Your organization sets the platform, so it can't be changed here")
        mine = dict(self._settings().get("platform", {}))  # type: ignore[attr-defined]
        if cfg:
            mine[env] = cfg
        else:
            mine.pop(env, None)
        self._save_settings(platform=mine)  # type: ignore[attr-defined]

    def platform_setup(self, env: str, name: str = "", capacity_id: str = "") -> dict[str, Any]:
        """Create (or finish creating) the Platform workspace of one environment."""
        current = self._platform_config().get(env, {})
        out = setup_mod.setup(self.fabric, lambda: self.auth._token(EXECUTE_SCOPES),  # type: ignore[attr-defined]
                              env, name, capacity_id, current)
        self._save_platform(env, out["config"])
        if out["ready"] and env == CATALOG_ENV:
            self._register_open_project()
        return out | {"status": self.platform_status()}

    def platform_join(self, env: str, workspace_id: str) -> dict[str, Any]:
        cfg = setup_mod.join(self.fabric, env, workspace_id)  # type: ignore[attr-defined]
        self._save_platform(env, cfg)
        return self.platform_status()

    def platform_forget(self, env: str) -> dict[str, Any]:
        """Stop using a Platform workspace on this PC. Nothing in Fabric is deleted."""
        self._save_platform(env, {})
        return self.platform_status()

    # ---- domains and glossary (the central team's lists)

    def platform_domains(self) -> list[dict[str, Any]]:
        return self._catalog().call("list_domains")

    def platform_save_domain(self, domain: dict[str, Any]) -> list[dict[str, Any]]:
        self._catalog().call("save_domain", domain=domain)
        return self.platform_domains()

    def platform_create_domain(self, domain: dict[str, Any], in_fabric: bool = True) -> list[dict[str, Any]]:
        """A new domain, created in Fabric first when ``in_fabric`` and then kept in the platform linked
        to it, as Import from Fabric would. Everything that could stop the platform's save is checked
        before Fabric is asked, so a refused domain is never left in Fabric alone."""
        return self._create_domain(domain, in_fabric)[0]

    def _create_domain(self, domain: dict[str, Any], in_fabric: bool = True,
                       marker: str = "") -> tuple[list[dict[str, Any]], bool]:
        """platform_create_domain, and whether the Fabric domain was adopted. A request's approval
        passes its ``marker`` (never reachable from the app's calls): it goes in the Fabric domain's
        description, and on Fabric's 409 a domain of this name and parent carrying exactly that marker
        is one this request already made, whose answer was lost, so it is kept instead (#1042)."""
        if not in_fabric:
            return self.platform_save_domain(domain), False
        name = str(domain.get("name") or "").strip()
        if not name:
            raise ValueError("A domain needs a name")
        did = domain_id(domain.get("id") or name)
        if not self._catalog().call("whoami").get("admin"):
            raise PermissionError("Only platform admins can create domains. Send a request in Data mesh > Requests.")
        known = {d["id"]: d for d in self.platform_domains()}
        same = known.get(did) or next((d for d in known.values() if d["name"].strip().lower() == name.lower()), None)
        if same:
            raise ValueError(f"The platform already has a domain called {same['name']}")
        parent = known.get(str(domain.get("parent_id") or ""))
        if domain.get("parent_id") and not parent:
            raise ValueError(f"parent domain {domain.get('parent_id')} doesn't exist")
        if parent and not parent.get("fabric_domain"):
            raise ValueError(f"{parent['name']} isn't in Fabric, so its subdomains can't be either. Create "
                             f"{parent['name']} in Fabric first, or choose Import from Fabric.")
        if parent and parent.get("parent_id"):
            raise ValueError("Subdomains go one level deep: pick a top-level domain as the parent")
        admin = self._fabric_admin(write=True)
        parent_fid = str((parent or {}).get("fabric_domain") or "")
        description = str(domain.get("description") or "")
        if marker:  # cut to Fabric's 256 characters with the marker whole, and no marker words typed
            description = marked(description, marker, DOMAIN_DESCRIPTION)
        adopted = False
        try:
            created = admin.create_admin_domain(name, description, parent_fid)
        except FabricError as exc:
            if exc.status in (401, 403):
                raise PermissionError("Only a Fabric administrator can create domains in Fabric, and Fabric refused "
                                      "this account. Nothing was changed.") from exc
            if exc.status == 409 or "AlreadyExists" in str(exc.code or ""):
                found = self._domain_made_by(admin, marker, name, parent_fid) if marker else None
                if found is None:
                    raise ValueError(f"Fabric already has a domain called {name}. Choose Import from Fabric to "
                                     "bring it in.") from exc
                created, adopted = found, True
            else:
                raise
        try:
            saved = self.platform_save_domain(domain | {"id": did, "name": name, "fabric_domain": str(created["id"])})
            return saved, adopted
        except Exception as exc:
            raise RuntimeError(f"{name} was created in Fabric but not saved in the platform ({exc}). Choose Import "
                               "from Fabric to bring it in.") from exc

    @staticmethod
    def _domain_made_by(admin: FabricClient, marker: str, name: str, parent_fid: str) -> dict[str, Any] | None:
        """The Fabric domain of this name and parent whose description carries exactly ``marker``, or
        None. A domain someone else made, or that another request made, has no such marker."""
        for d in admin.list_admin_domains():
            if (str(d.get("displayName") or "").strip().lower() == name.lower()
                    and str(d.get("parentDomainId") or "") == parent_fid
                    and marker in str(d.get("description") or "")):
                return d
        return None

    def platform_delete_domain(self, id: str) -> list[dict[str, Any]]:
        self._catalog().call("delete_domain", did=id)
        return self.platform_domains()

    # ---- domains from Fabric

    def _fabric_admin(self, write: bool = False) -> FabricClient:
        """Fabric's admin API, as the signed-in user. Tenant.Read.All is asked for only here, and
        Tenant.ReadWrite.All only to create a domain (``write``)."""
        scopes = TENANT_WRITE_SCOPES if write else TENANT_READ_SCOPES
        return FabricClient(lambda: self.auth._token(scopes))  # type: ignore[attr-defined]

    def platform_import_fabric_domains(self) -> dict[str, Any]:
        """Bring Fabric's domains and subdomains into the catalog, so everyone sees their names.
        Needs a Fabric administrator (Fabric's domain list is an admin API) who is also a platform admin."""
        try:
            admin = self._fabric_admin()
            fabric = admin.list_admin_domains()
        except FabricError as exc:
            if exc.status in (401, 403):
                raise PermissionError("Only a Fabric administrator can read Fabric's domains. Ask one to open "
                                      "Data mesh > Domains and choose Import from Fabric; after that, everyone "
                                      "sees the domains here.") from exc
            raise
        unread = self._read_domain_admins(admin, fabric)
        result = self._catalog().call("import_fabric_domains", domains=fabric)
        return {"result": result, "fabric": len(fabric), "owners_unread": unread, "domains": self.platform_domains()}

    @staticmethod
    def _read_domain_admins(admin: FabricClient, fabric: list[dict[str, Any]]) -> list[str]:
        """Add each Fabric domain's admins (names and kinds, no email) as ``admins``. A domain whose
        admins can't be read gets no ``admins``, so the catalog keeps what it had; its name is returned."""
        unread = []
        for d in fabric:
            try:
                roles = admin.list_admin_domain_role_assignments(str(d["id"]))
            except (FabricError, KeyError) as exc:
                name = str(d.get("displayName") or d.get("id") or "")
                busy = getattr(exc, "status", None) == 429  # still throttled after the client's retries
                unread.append(f"{name} (Fabric is busy: import again later)" if busy else name)
                continue
            d["admins"] = [{"name": (r.get("principal") or {}).get("displayName") or "",
                            "type": (r.get("principal") or {}).get("type") or "User"}
                           for r in roles if str(r.get("role") or "").lower() in ("admin", "admins")]
        return unread

    def mesh_workspace_domains(self) -> dict[str, Any]:
        """The Fabric domain of each workspace the open project writes to. Any workspace member can
        read this (no admin needed); names come from the catalog once a Fabric admin has imported them."""
        project = self._project()  # type: ignore[attr-defined]
        domains = self._catalog().call("list_domains")
        linked = {d["fabric_domain"]: d for d in domains if d.get("fabric_domain")}
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for env in ENVIRONMENTS:
            cfg = project.environments.get(env, {})
            spaces = [("", cfg.get("workspace_id"), cfg.get("workspace_name", ""))]
            spaces += [(d, w.get("id"), w.get("name", "")) for d, w in (cfg.get("workspaces") or {}).items()]
            for designation, wid, wname in spaces:
                if not wid:
                    continue
                row = {"env": env, "designation": designation, "workspace_id": wid, "workspace_name": wname,
                       "fabric_domain": "", "domain": None, "problem": ""}
                if wid not in seen:
                    try:
                        ws = self.fabric.get_workspace(wid)  # type: ignore[attr-defined]
                        row["workspace_name"] = ws.get("displayName") or wname
                        row["fabric_domain"] = ws.get("domainId") or ""
                    except Exception as exc:  # noqa: BLE001 - one unreadable workspace shouldn't hide the rest
                        row["problem"] = str(exc)
                    seen.add(wid)
                else:
                    row.update({k: v for k, v in next(r for r in rows if r["workspace_id"] == wid).items()
                                if k in ("workspace_name", "fabric_domain", "problem")})
                hit = linked.get(row["fabric_domain"])
                row["domain"] = hit["id"] if hit else None
                rows.append(row)
        # the suggestion follows Prod first: that's where the products people use live
        suggested = None
        for env in reversed(ENVIRONMENTS):
            hit = next((linked[r["fabric_domain"]] for r in rows if r["env"] == env and r["fabric_domain"] in linked), None)
            if hit:
                suggested = ({"domain": hit["parent_id"], "subdomain": hit["id"]} if hit.get("parent_id")
                             else {"domain": hit["id"], "subdomain": ""})
                break
        return {"workspaces": rows, "suggested": suggested,
                "unnamed": sorted({r["fabric_domain"] for r in rows if r["fabric_domain"] and not r["domain"]}),
                "imported": any(d.get("fabric_domain") for d in domains)}

    def platform_glossary(self) -> list[dict[str, Any]]:
        return self._catalog().call("list_glossary")

    def platform_save_term(self, term: dict[str, Any]) -> list[dict[str, Any]]:
        self._catalog().call("save_term", term=term)
        return self.platform_glossary()

    def platform_audit(self, limit: int = 100) -> list[dict[str, Any]]:
        return self._catalog().call("audit", limit=limit)

    # ---- the Platform page: who's an admin, key tables, the landing zone, requests

    def _distinct_platforms(self) -> list[tuple[str, LocalPlatform | FabricPlatform]]:
        """Each environment's platform once (two environments may share one)."""
        seen, out = set(), []
        for env in ENVIRONMENTS:
            client = self._platform(env)
            ident = getattr(client, "functions_id", None) or str(getattr(client, "path", env))
            if ident not in seen:
                seen.add(ident)
                out.append((env, client))
        return out

    def platform_whoami(self) -> dict[str, Any]:
        me = self._catalog().call("whoami")
        return me | {"signed_in": bool(self._user()), "catalog_env": CATALOG_ENV}

    def platform_note_seen(self) -> dict[str, Any]:
        """Tell the platform this person used it today (owner coverage), once a day per app run, in the
        background so the Platform page never waits on it. Only when the organization's policy turns on
        platform_presence: it records each person's last-used date where anyone who reads the catalog sees it."""
        user, today = self._user(), dt.date.today().isoformat()
        if not app_settings.value("platform_presence"):
            return {"noted": False, "reason": "off"}
        if not user or getattr(self, "_seen_noted", None) == (user, today):
            return {"noted": False, "reason": "already"}
        self._seen_noted = (user, today)
        threading.Thread(target=self._note_seen, args=(user,), daemon=True, name="note-seen").start()
        return {"noted": True}

    def _note_seen(self, user: str) -> None:
        try:
            self._catalog().call("note_seen")
        except Exception as exc:  # noqa: BLE001 - only owner coverage's last-seen date depends on it; never in the way
            _log.info("Couldn't note that %s used the platform: %s", user, describe(exc))

    def platform_owner_coverage(self) -> dict[str, Any]:
        """Domains, data products and sources without an owner, without a second owner, or whose owner
        hasn't used the platform lately. Only reads."""
        try:
            return call_or_repair(self._catalog(), "owner_coverage", "check owners",
                                  presence=bool(app_settings.value("platform_presence")))
        except LookupError as exc:
            return {"available": False, "problem": str(exc), "gaps": [], "checked": 0, "covered": 0, "no_owner": 0,
                    "quiet_days": core.QUIET_DAYS}

    def platform_admins(self) -> list[dict[str, Any]]:
        return self._catalog().call("list_admins")

    def _each_platform(self, op: str, **kwargs: Any) -> list[dict[str, Any]]:
        """Run one change on every environment's platform (admins and key tables live in each).
        The catalog's goes first, so a refusal there stops the rest."""
        envs = sorted(self._distinct_platforms(), key=lambda e: e[0] != CATALOG_ENV)
        if self._keys_config().get("shared") and op != "ensure_key_table":
            envs.append(("keys", self._shared_keys()))  # admins of the shared keys database too
        done = []
        for i, (env, client) in enumerate(envs):
            try:
                done.append({"env": env, "ok": True, "result": client.call(op, **kwargs)})
            except Exception as exc:  # noqa: BLE001 - later environments still get the change
                if i == 0:
                    raise
                done.append({"env": env, "ok": False, "message": str(exc)})
        return done

    def platform_claim_admin(self) -> dict[str, Any]:
        self._each_platform("claim_admin")
        return self.platform_whoami()

    def platform_add_admin(self, user: str) -> list[dict[str, Any]]:
        self._each_platform("add_admin", user=user)
        return self.platform_admins()

    def platform_remove_admin(self, user: str) -> list[dict[str, Any]]:
        self._each_platform("remove_admin", user=user)
        return self.platform_admins()

    def platform_key_tables(self, env: str = "dev") -> dict[str, Any]:
        tables = self._keys_platform(env).call("list_key_tables")
        waiting = [r for r in self._catalog().call("list_requests", status="waiting") if r["kind"] == "key_table"]
        return {"env": env, "key_tables": tables, "requested": waiting, "shared": bool(self._keys_config().get("shared"))}

    def platform_create_key_table(self, name: str, business_key: str = "", description: str = "",
                                  domain_id: str = "") -> dict[str, Any]:
        """A new key table in the shared keys database, or in every environment's platform at once
        so Dev, Test and Prod match."""
        name = name.strip().lower()
        if self._keys_config().get("shared"):
            created = self._shared_keys().call("ensure_key_table", name=name, business_key=business_key,
                                               description=description, domain_id=domain_id)
            return {"name": name, "environments": [{"env": "keys", "ok": True, "result": created}]}
        report = self._each_platform("ensure_key_table", name=name, business_key=business_key,
                                     description=description, domain_id=domain_id)
        return {"name": name, "environments": report}

    def platform_sources(self) -> list[dict[str, Any]]:
        return self._catalog().call("list_sources")

    def platform_save_source(self, source: dict[str, Any]) -> list[dict[str, Any]]:
        return self._catalog().call("save_source", source=source)

    def platform_delete_source(self, name: str) -> list[dict[str, Any]]:
        return self._catalog().call("delete_source", name=name)

    # ---- the landing zone: each environment's LH_Landing, or one central landing workspace (Preview)

    def _landing_config(self) -> dict[str, Any]:
        """The central landing workspace: {workspace_id, workspace_name, lakehouse_id} (Shared's LH_Landing)."""
        policy = app_settings.value("platform") or {}
        mine = self._settings().get("platform", {})  # type: ignore[attr-defined]
        return dict(policy.get("landing") or mine.get("landing") or {})

    def _central_landing(self) -> dict[str, Any]:
        cfg = self._landing_config()
        return cfg if cfg.get("workspace_id") else {}

    def _zone_call(self, op: str, **kwargs: Any) -> Any:
        """Landing zones came with an update: a UDF_Platform made before it has no such function, and
        Fabric answers 404, which the client reads as "UDF_Platform wasn't found". Say what to do."""
        missing = (f"The platform can't use landing zones yet: UDF_Platform has no {op} function. After an update, "
                   "a platform admin runs Platform > Settings > Finish or repair once.")
        try:
            return self._catalog().call(op, **kwargs)
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

    def _landing_resolver(self, local: bool = False) -> Any:
        """For a store: where each source's zone is, read from the catalog on the first landing read.
        None without central landing, so each environment reads its own LH_Landing as before. For a
        store on this PC (``local``), a catalog that can't be read gives no map: every zone reads the
        local landing folder there anyway, so Dev runs on without the catalog."""
        if not self._central_landing():
            return None

        def read() -> dict[str, Any] | None:
            try:
                zones = {z["id"]: z for z in self._zone_call("list_landing_zones")}
                sources = {s["name"]: s.get("zone") or "" for s in self._catalog().call("list_sources")}
            except Exception as exc:  # noqa: BLE001 - on a PC the local folder is read; elsewhere the run stops with this
                if not local:
                    raise
                _log.info("Landing zones not read; this PC's landing folder is read instead: %s", describe(exc))
                return None
            return {"zones": zones, "sources": sources}
        return read

    def _landing_zones(self) -> list[dict[str, Any]]:
        """The catalog's landing zones; none when its UDF_Platform predates them."""
        try:
            return self._zone_call("list_landing_zones")
        except Exception as exc:  # noqa: BLE001 - an older catalog has no zones: today's landing zone shows
            _log.info("Landing zones not read from the catalog: %s", describe(exc))
            return []

    def _adopt_landing(self, zones: list[dict[str, Any]]) -> dict[str, Any]:
        """Use the central landing workspace another admin set up: its Shared zone is in the catalog."""
        cfg = self._central_landing()
        shared = next((z for z in zones if z["id"] == core.SHARED_ZONE), None)
        if cfg or not shared or not (shared.get("workspace_id") and shared.get("lakehouse_id")) \
                or self._platform_source() == "policy":
            return cfg
        cfg = {"workspace_id": shared["workspace_id"], "workspace_name": shared.get("workspace_name", ""),
               "lakehouse_id": shared["lakehouse_id"]}
        self._save_platform("landing", cfg)
        return cfg

    def _require_platform_admin(self, action: str) -> None:
        if not self._catalog().call("whoami").get("admin"):
            raise PermissionError(f"Only platform admins can {action}. Ask a platform admin.")

    def platform_landing_setup(self, workspace_name: str = setup_mod.LANDING_WORKSPACE, capacity_id: str = "") -> dict[str, Any]:
        """Create (or keep) the one landing workspace Dev, Test and Prod read, with its LH_Landing, and
        record the Shared zone in the catalog. Each environment's own LH_Landing and its files stay
        where they are: nothing is copied or deleted. Platform admins only."""
        self._require_platform_admin("set up the landing workspace")
        if self._platform_source() == "policy" and not self._central_landing():
            raise PermissionError("Your organization sets the platform, so the landing workspace is added to its policy "
                                  "(platform.landing), not here")
        out = setup_mod.landing_setup(self.fabric, workspace_name, capacity_id, self._landing_config())  # type: ignore[attr-defined]
        cfg = out["config"]
        if cfg.get("lakehouse_id"):
            try:
                self._zone_call("save_landing_zone", zone={
                    "id": core.SHARED_ZONE, "name": "Shared", "workspace_id": cfg["workspace_id"],
                    "workspace_name": cfg.get("workspace_name", ""), "lakehouse_id": cfg["lakehouse_id"],
                    "lakehouse_name": setup_mod.LANDING})
                out["steps"].append({"step": "Shared zone", "status": "done", "detail": "Recorded in the catalog"})
            except Exception as exc:  # noqa: BLE001 - reported as the step's problem; the rest is kept for next time
                out["steps"].append({"step": "Shared zone", "status": "failed", "detail": (
                    f"{exc}. If the catalog's UDF_Platform is older than landing zones, choose Finish setup for "
                    "Prod in Platform > Settings, then set up the landing workspace again.")})
                out["ready"] = False
        # Only a finished setup is used: until the Shared zone is in the catalog, runs read as before.
        if out["ready"] and self._platform_source() != "policy":
            self._save_platform("landing", cfg)
        return out | {"status": self.platform_status()}

    def platform_create_landing_zone(self, zone: dict[str, Any]) -> dict[str, Any]:
        """A new zone in the landing workspace: creates LH_Landing_<Name> there (or keeps the one that's
        there) and records the zone with its domain, readers, label and whether Dev and Test read it.
        A secure zone's OneLake access role and sensitivity label are set in Fabric for now. Platform admins only."""
        self._require_platform_admin("create landing zones")
        cfg = self._central_landing()
        if not cfg:
            raise LookupError("Set up the landing workspace first (Platform > Set up > Landing zone).")
        name = str(zone.get("name") or "").strip()
        if core.zone_key(name) == core.SHARED_ZONE:
            raise ValueError("Shared is made with the landing workspace; give the new zone another name")
        lakehouse = core.zone_lakehouse(name)
        lh, created = setup_mod.zone_lakehouse(self.fabric, cfg["workspace_id"], lakehouse)  # type: ignore[attr-defined]
        zones = self._zone_call("save_landing_zone", zone=dict(zone) | {
            "name": name, "workspace_id": cfg["workspace_id"], "workspace_name": cfg.get("workspace_name", ""),
            "lakehouse_id": lh["id"], "lakehouse_name": lakehouse})
        return {"created": created, "lakehouse": lakehouse, "zones": zones}

    def platform_switch_landing_reads(self) -> list[dict[str, Any]]:
        """Every environment reads Shared sources from the landing workspace from now on, instead of
        its own LH_Landing. Nothing is copied: the files runs need must be there first."""
        return self._zone_call("switch_landing_reads")

    def platform_delete_landing_zone(self, id: str) -> list[dict[str, Any]]:
        """Take a zone out of the catalog; its lakehouse and files stay in Fabric."""
        return self._zone_call("delete_landing_zone", id=id)

    def _zone_listing(self, workspace_id: str, lakehouse_id: str) -> list[dict[str, Any]]:
        store = FileStore(lambda: self.auth.storage_token())  # type: ignore[attr-defined]
        return [{"path": e["path"][len("Files/"):], "size": e["size"], "modified": e.get("modified", "")}
                for e in store.list(workspace_id, lakehouse_id, "Files", recursive=True)
                if not e["is_dir"] and e["path"].startswith("Files/")]

    def _zone_files(self, zones: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[str]]:
        """Every file in the zones' lakehouses, each with its zone; and the zones that couldn't be read."""
        files, problems = [], []
        for z in zones:
            if not (z.get("workspace_id") and z.get("lakehouse_id")):
                continue
            try:
                files += [f | {"zone": z["id"]} for f in self._zone_listing(z["workspace_id"], z["lakehouse_id"])]
            except Exception as exc:  # noqa: BLE001 - a zone you can't read (a secure one) doesn't hide the rest
                problems.append(f"{z.get('name') or z['id']} ({z.get('lakehouse_name')}) couldn't be read: {exc}")
        return files, problems

    def _landing_files(self, env: str) -> list[dict[str, Any]]:
        """Every file in an environment's landing zone: {path under Files, size, modified}."""
        cfg = self._platform_config().get(env, {})
        if cfg.get("landing_id"):
            return self._zone_listing(cfg["workspace_id"], cfg["landing_id"])
        top = self._landing_dir(env) / "Files"  # type: ignore[attr-defined]
        if not top.exists():
            return []
        out = []
        for f in top.rglob("*"):
            if f.is_file():
                st = f.stat()
                out.append({"path": f.relative_to(top).as_posix(), "size": st.st_size,
                            "modified": dt.datetime.fromtimestamp(st.st_mtime, dt.timezone.utc).isoformat(timespec="seconds")})
        return out

    def platform_landing(self, env: str = "dev") -> dict[str, Any]:
        """The landing zone: each source, its entities, newest arrival, and whether it's late against
        the hours the central team expects it every. With the central landing workspace it's the same
        for every environment: the zones, and each source in its zone's lakehouse. Without it, one
        environment's LH_Landing (or this PC's landing folder)."""
        cfg = self._platform_config().get(env, {})
        sources = {s["name"]: s for s in self._catalog().call("list_sources")}
        zones = self._landing_zones()
        # No zones in the catalog yet (or it can't say): each environment's landing zone, as before.
        central = self._adopt_landing(zones) if zones else {}
        problem = None
        if central:
            files, problems = self._zone_files(zones)
            problem = "; ".join(problems) or None
        else:
            try:
                files = self._landing_files(env)
            except Exception as exc:  # noqa: BLE001 - the registered sources still show
                files, problem = [], f"The landing zone couldn't be read: {exc}"
        names = {z["id"]: z.get("name") or z["id"] for z in zones}
        tree: dict[str, dict[str, list[dict[str, Any]]]] = {}
        for f in files:
            parts = f["path"].split("/")
            if len(parts) < 2:
                continue  # a file at the top of Files belongs to no source
            entity = parts[1] if len(parts) > 2 else ""
            tree.setdefault(parts[0], {}).setdefault(entity, []).append(f)
        now = dt.datetime.now(dt.timezone.utc)
        out = []
        for name in sorted(set(sources) | set(tree)):
            src = sources.get(name, {})
            entities = []
            for ent, fs in sorted(tree.get(name, {}).items()):
                newest = max((f["modified"] for f in fs), default="")
                entities.append({"name": ent, "files": len(fs), "size": sum(f["size"] for f in fs), "newest": newest})
            newest = max((e["newest"] for e in entities), default="")
            late = False
            hours = src.get("expected_hours")
            if hours and newest:
                try:
                    late = now - dt.datetime.fromisoformat(newest) > dt.timedelta(hours=float(hours) * 1.25)
                except ValueError:
                    late = False
            elif hours and not newest:
                late = True
            zone = src.get("zone") or ""
            out.append({"name": name, "registered": bool(src), "title": src.get("title", name),
                        "source_id": src.get("source_id"), "code": src.get("code") or name.upper(),
                        "system_type": src.get("system_type", ""), "active": src.get("active", True),
                        "description": src.get("description", ""), "owner": src.get("owner", ""),
                        "expected_hours": hours or "", "readers": src.get("readers", []),
                        "connections": src.get("connections", {}), "entities": entities, "newest": newest,
                        "late": late, "zone": zone, "zone_name": names.get(zone or core.SHARED_ZONE, "")})
        base = {"sources": out, "problem": problem, "system_types": list(keys_mod.SYSTEM_TYPES), "zones": zones,
                "central": bool(central), "landing": central, "default_landing_name": setup_mod.LANDING_WORKSPACE,
                "policy": self._platform_source() == "policy"}
        if central:
            return base | {"env": env, "kind": "fabric", "where": central.get("workspace_name") or central["workspace_id"]}
        return base | {"env": env, "kind": "fabric" if cfg.get("landing_id") else "local",
                       "where": cfg.get("workspace_name") or str(self._landing_dir(env) / "Files")}  # type: ignore[attr-defined]

    def platform_landing_files(self, env: str, source: str, entity: str = "", limit: int = 200) -> list[dict[str, Any]]:
        prefix = f"{source}/{entity}/" if entity else f"{source}/"
        if self._central_landing():
            zones = self._landing_zones()
            if zones:
                zid = next((s.get("zone") for s in self._catalog().call("list_sources") if s["name"] == source), "")
                mine = [z for z in zones if z["id"] == (zid or core.SHARED_ZONE)] or zones
                files, problems = self._zone_files(mine)
                if problems and not files:
                    raise PermissionError(problems[0])
                files = [f for f in files if f["path"].startswith(prefix)]
                return sorted(files, key=lambda f: f["modified"], reverse=True)[:limit]
        files = [f for f in self._landing_files(env) if f["path"].startswith(prefix)]
        return sorted(files, key=lambda f: f["modified"], reverse=True)[:limit]

    def platform_requests(self, status: str = "") -> list[dict[str, Any]]:
        return self._catalog().call("list_requests", status=status)

    def platform_request(self, kind: str, target: str, reason: str = "", detail: dict[str, Any] | None = None,
                         project: str = "") -> dict[str, Any]:
        if not project:
            try:
                project = mesh.project_name(self._project())  # type: ignore[attr-defined]
            except LookupError:
                project = ""
        out = self._catalog().call("request", kind=kind, target=target, project=project, reason=reason,
                                   detail=detail or {})
        self._notify_request(out, notices.request_waiting)
        return out

    def _notify_request(self, req: dict[str, Any], build: Any) -> None:
        """Tell the request's routes about it (platform_notify_service.py). Products are read only
        for the kinds that name one, so the others cost no extra catalog call."""
        if not req or not req.get("id"):
            return

        def events() -> list[dict[str, Any]]:
            kinds = ("access", "shortcut", "data")
            products = self._catalog().call("list_products") if req.get("kind") in kinds else []
            event = build(req, products)
            return [event] if event else []

        problem = self._governance_notify(events)  # type: ignore[attr-defined]
        if problem:
            req.setdefault("problems", []).append(problem)

    def platform_decide(self, id: str, approve: bool, note: str = "") -> dict[str, Any]:
        out = self._catalog().call("decide_request", id=id, approve=approve, note=note)
        if approve and out.get("kind") == "key_table":
            # The catalog's platform made it; the shared keys database, or the other environments, get it too.
            d = out.get("detail") or {}
            for env, client in self._key_platforms():
                if env == CATALOG_ENV:
                    continue
                try:
                    client.call("ensure_key_table", name=out["target"], business_key=d.get("business_key", ""),
                                description=d.get("description", "") or out.get("reason", ""))
                except Exception as exc:  # noqa: BLE001
                    out.setdefault("problems", []).append(f"{env.title()}: {exc}")
        self._notify_request(out, notices.request_closed)
        return out

    def platform_withdraw(self, id: str) -> dict[str, Any]:
        out = self._catalog().call("withdraw_request", id=id)
        self._notify_request(out, notices.request_closed)
        return out

    def platform_revoke(self, product_id: str, project: str) -> list[dict[str, Any]]:
        return self._catalog().call("revoke_grant", product_id=product_id, project=project)

    def platform_access_reviews(self) -> dict[str, Any]:
        """The grants you review: your products', or all of them for a platform admin. Only reads.
        A platform older than access reviews says so instead of failing the page."""
        try:
            return call_or_repair(self._catalog(), "access_reviews", "review access")
        except LookupError as exc:
            return {"available": False, "problem": str(exc), "grants": [], "due": 0, "every_days": core.REVIEW_DAYS}

    def platform_confirm_access(self, product_id: str, projects: list[str]) -> dict[str, Any]:
        """Confirm these projects still need access to the product. Writes catalog.grant_review."""
        return call_or_repair(self._catalog(), "confirm_access", "review access", product_id=product_id,
                              projects=list(projects or []))

    def platform_overview(self, env: str = "dev") -> dict[str, Any]:
        """The central team's home: counts, what needs a decision, the landing zone and each
        environment's platform."""
        catalog = self._catalog()
        problems: list[str] = []
        me = self.platform_whoami()
        domains = catalog.call("list_domains")
        projects = catalog.call("list_projects")
        products = catalog.call("list_products")
        requests = catalog.call("list_requests", status="waiting")
        try:
            keys = self._keys_platform(env).call("list_key_tables")
        except Exception as exc:  # noqa: BLE001
            keys, problems = [], problems + [str(exc)]
        try:
            landing = self.platform_landing(env)
        except Exception as exc:  # noqa: BLE001
            landing = {"sources": [], "problem": str(exc)}
        status = self.platform_status()
        return {"env": env, "me": me, "status": status,
                "counts": {"domains": len([d for d in domains if not d.get("parent_id")]),
                           "subdomains": len([d for d in domains if d.get("parent_id")]),
                           "projects": len(projects), "teams": len({p.get("team") for p in projects if p.get("team")}),
                           "key_tables": len(keys), "keys": sum(int(k.get("keys") or 0) for k in keys),
                           "products": len(products), "published": len([p for p in products if p["status"] == "published"]),
                           "requests": len(requests)},
                "needs_you": [r for r in requests if r.get("can_decide")], "mine": [r for r in requests if r.get("mine")],
                "landing": landing, "recent": catalog.call("audit", limit=8), "problems": problems}

    def mesh_home(self, view: str = "team") -> dict[str, Any]:
        """Data mesh > Home for one of its three views (central/home.py): ``team``, ``oncall`` or
        ``org``. Only reads: the catalog, the open project's incident lists and, on call, the
        landing zone. A source that can't be read is named in ``problems`` and its part is left out."""
        if view not in home.VIEWS:
            raise ValueError(f"view is one of {', '.join(home.VIEWS)}")
        catalog, problems = self._catalog(), []

        def read(what: str, fn: Any) -> Any:
            try:
                return fn()
            except Exception as exc:  # noqa: BLE001 - Home shows what it could read and names the rest
                problems.append(f"{what}: {describe(exc)}")
                return None

        try:
            products = catalog.call("list_products")
            me = catalog.call("whoami")
        except Exception as exc:  # noqa: BLE001 - without the catalog there is no mesh to show
            return {"view": view, "problem": describe(exc)}
        try:
            project = mesh.identity(self._project())  # type: ignore[attr-defined]
        except LookupError:
            project = None
        incidents = []
        if project is not None:
            summary = read("Incidents", self.incidents_summary) or {"envs": []}  # type: ignore[attr-defined]
            incidents = [i for e in summary["envs"] for i in e["incidents"]]
            problems += [f"Incidents in {e['env'].title()}: {e['observation'].get('detail') or e['observation'].get('label')}"
                         for e in summary["envs"] if e["count"] is None and e["observation"].get("state") != "not_set_up"]
        reach = home.incident_reach(products, incidents)
        reviews = (read("Access reviews", lambda: catalog.call("access_reviews"))
                   if "access_reviews" in core.OPERATIONS else None)
        out: dict[str, Any] = {"view": view, "me": me, "project": project, "incidents_read": project is not None}
        if view == "team":
            requests = read("Requests", lambda: catalog.call("list_requests")) or []
            follows = read("Following", lambda: catalog.call("list_follows"))
            out["team"] = home.team(me.get("user") or "", project, products, requests, follows, reviews, reach)
        elif view == "oncall":
            landing = read("Landing zone", lambda: self.platform_landing("prod"))
            shortcuts = read("Shortcuts", lambda: catalog.call("list_seen_shortcuts"))
            coverage = (read("Owners", self.platform_owner_coverage)  # type: ignore[attr-defined]
                        if "owner_coverage" in core.OPERATIONS else None)
            out["oncall"] = home.oncall(reach, landing, shortcuts, coverage)
        else:
            domains = read("Domains", lambda: catalog.call("list_domains")) or []
            requests = read("Requests", lambda: catalog.call("list_requests", status="waiting")) or []
            out["org"] = home.org(domains, products, requests, reviews, reach, dt.datetime.now(dt.timezone.utc))
        return out | {"problems": problems}

    def mesh_map(self) -> dict[str, Any]:
        """The whole mesh for Home's map: domains and subdomains, the projects in each, their
        products and who links to them. Read from the catalog only, so it's quick and never writes."""
        catalog = self._catalog()
        try:
            domains = catalog.call("list_domains")
            projects = catalog.call("list_projects")
            products = catalog.call("list_products")
        except Exception as exc:  # noqa: BLE001 - Home still loads without the platform
            return {"domains": [], "projects": [], "products": [], "links": [], "problem": str(exc), "me": ""}
        try:
            me = mesh.project_name(self._project())  # type: ignore[attr-defined]
        except LookupError:
            me = ""
        links = [{"from": c["consumer_project"], "product": p["id"], "to": p["project"]}
                 for p in products for c in p.get("consumers", [])]
        return {"me": me, "problem": "",
                "domains": [{k: d.get(k) for k in ("id", "name", "parent_id", "fabric_domain", "owners", "fabric_owners")}
                            for d in domains],
                "projects": [{k: p.get(k) for k in ("name", "domain_id", "subdomain_id", "team")} for p in projects],
                "products": [{k: p.get(k) for k in ("id", "project", "name", "domain_id", "access", "status")}
                             for p in products if p["status"] != "draft"],
                "links": links}

    def platform_products(self) -> dict[str, Any]:
        """Every published data product, for the OneLake catalog: what the open project may link
        to now, and what it must ask for."""
        products = [p for p in self._catalog().call("list_products") if p["status"] != "draft"]
        try:
            project = self._project()  # type: ignore[attr-defined]
            me = mesh.identity(project)
        except LookupError:
            me = None
        waiting = {(r["target"], r["project"]) for r in self._catalog().call("list_requests", status="waiting")
                   if r["kind"] == "access"}
        for p in products:
            if me is None:
                p["can_link"], p["why"] = False, "Open a metadata project to link"
            elif p["project"] == me["name"]:
                p["can_link"], p["why"] = False, "Your own product"
            elif p["access"] == "public" or me["name"] in p.get("granted", []):
                p["can_link"], p["why"] = True, ""
            elif p["access"] == "domain" and p.get("domain_id") and p.get("domain_id") == me.get("domain"):
                p["can_link"], p["why"] = True, ""
            else:
                p["can_link"] = False
                p["why"] = "requested" if (p["id"], me["name"]) in waiting else "request"
            p["linked"] = any(c["consumer_project"] == me["name"] for c in p.get("consumers", [])) if me else False
        follows = self._my_follows()
        for p in products:
            p["following"] = p["id"] in (follows or [])
        # Each product's semantic models and reports (Preview); an older platform has none to show.
        groups = self._product_groups(self._catalog())  # type: ignore[attr-defined]
        for p in products:
            p["items"] = [g for g in groups or [] if g["product_id"] == p["id"]]
        return {"project": me, "products": products, "can_follow": follows is not None, "can_group": groups is not None}

    def _my_follows(self) -> list[str] | None:
        """The products the signed-in person follows; None when the platform is older than following."""
        try:
            return self._catalog().call("list_follows")
        except LookupError:
            return None
        except core.PlatformError as exc:
            if "list_follows" in str(exc):
                return None
            raise

    def platform_follow(self, product_id: str, follow: bool = True) -> dict[str, Any]:
        """Follow a product, or stop. Writes catalog.follow; returns the products you follow."""
        op = "follow_product" if follow else "unfollow_product"
        follows = call_or_repair(self._catalog(), op, "follow data products", product_id=product_id)
        return {"following": follows}

    # ---- the open project in the mesh

    def _register_open_project(self) -> None:
        try:
            project = self._project()  # type: ignore[attr-defined]
        except LookupError:
            return
        if project.info:
            i = mesh.identity(project) | {"landing": mesh.landing_readers(project)}
            try:
                out = self._catalog().call("register_project", project=i)
            except core.PlatformError as exc:  # not a member: setting up the platform still finishes
                _log.info("register_project: %s", exc)
                return
            self._keep_catalog_domain(project, out)

    @staticmethod
    def _keep_catalog_domain(project: Any, out: Any) -> None:
        """A move to another domain waits for that domain's owners: project.toml keeps the domain the
        catalog has until they approve, so the files and the catalog agree."""
        if not (isinstance(out, dict) and out.get("domain_request")):
            return
        fields = mesh.identity(project) | {"domain": out.get("domain_id") or "", "subdomain": out.get("subdomain_id") or ""}
        mesh.write_project_file(project.root, fields)
        project.reload()

    def mesh_members(self, project: str = "") -> dict[str, Any]:
        """The open project's members in the platform's catalog, and whether you may change them."""
        name = project or mesh.project_name(self._project())  # type: ignore[attr-defined]
        return call_or_repair(self._catalog(), "list_project_members", "keep a project's members", project=name)

    def mesh_set_member(self, user: str, role: str = "", project: str = "") -> dict[str, Any]:
        """Add a member to the open project, change their role, or take them off (role "")."""
        name = project or mesh.project_name(self._project())  # type: ignore[attr-defined]
        return call_or_repair(self._catalog(), "set_project_member", "keep a project's members",
                              project=name, user=user, role=role)

    def mesh_overview(self) -> dict[str, Any]:
        """Everything the Mesh tabs show: this project, the domains, products here and published elsewhere."""
        project = self._project()  # type: ignore[attr-defined]
        summary = project.summary()
        catalog = self._catalog()
        problems = []
        try:
            domains = catalog.call("list_domains")
            published = catalog.call("list_products")
            projects = catalog.call("list_projects")
        except Exception as exc:  # noqa: BLE001 - the project's own files still show
            domains, published, projects = [], [], []
            problems.append(f"The platform's catalog couldn't be read: {exc}")
        me = mesh.identity(project)
        return {"project": me, "products": summary["products"], "links": summary["links"],
                "layers": summary["layers"], "environments": summary["environments"],
                "designations": summary["designations"], "domains": domains, "published": published,
                "projects": projects, "platform": self.platform_status(), "problems": problems,
                "errors": [e for e in summary["errors"] if e.startswith(("products/", "links/", mesh.PROJECT_FILE, "layer "))]}

    def mesh_set_project(self, fields: dict[str, Any]) -> dict[str, Any]:
        """Write project.toml (the Upgrade), then record the project and its domain in the catalog."""
        project = self._project()  # type: ignore[attr-defined]
        fields = {**mesh.identity(project), **{k: v for k, v in fields.items() if v is not None}}
        fields["name"] = str(fields.get("name") or project.root.name).strip().lower().replace(" ", "-")
        catalog = self._catalog()
        if fields.get("domain"):
            known = {d["id"]: d for d in catalog.call("list_domains")}
            if fields["domain"] not in known:
                raise GateError("save", [f"domain {fields['domain']} isn't one of your organization's domains"])
            if fields.get("subdomain") and known.get(fields["subdomain"], {}).get("parent_id") != fields["domain"]:
                raise GateError("save", [f"{fields['subdomain']} isn't a subdomain of {fields['domain']}"])
        mesh.write_project_file(project.root, fields)
        project.reload()
        out = catalog.call("register_project", project=fields | {"landing": mesh.landing_readers(project)})
        self._keep_catalog_domain(project, out)
        return self.mesh_overview() | {"domain_request": (out or {}).get("domain_request") if isinstance(out, dict) else None}

    def mesh_save_product(self, product: dict[str, Any], original: str = "") -> dict[str, Any]:
        project = self._project()  # type: ignore[attr-defined]
        path = project.root / "metadata" / "products" / f"{product.get('name', '')}.toml"
        old = path.read_text(encoding="utf-8") if path.exists() else None
        new_path = mesh.write_product(project.root, product)
        project.reload()
        problems = [e for e in mesh.validate(project) if e.startswith(f"products/{product.get('name')}")]
        if problems:
            if old is None:
                new_path.unlink()
            else:
                jsonfile.write_text(new_path, old)
            raise GateError("save", problems)
        if original and original != product.get("name"):
            (project.root / "metadata" / "products" / f"{original}.toml").unlink(missing_ok=True)
        return self.mesh_overview()

    def mesh_delete_product(self, name: str) -> dict[str, Any]:
        project = self._project()  # type: ignore[attr-defined]
        (project.root / "metadata" / "products" / f"{name}.toml").unlink(missing_ok=True)
        return self.mesh_overview()

    def _port_location(self, project: Any, env: str, entity: str) -> dict[str, Any]:
        """Where an entity's table is in an environment, as the catalog records a product port."""
        e = project.entities[entity]
        store = self._store(project, env)  # type: ignore[attr-defined]
        lh = project.layer(e.layer).lakehouse
        ws = getattr(store, "lakehouse_workspaces", {}).get(lh) or getattr(store, "workspace_id", "")
        cfg = project.environments.get(env, {})
        return {"entity": entity, "workspace_id": ws, "workspace_name": cfg.get("workspace_name", ""),
                "lakehouse_id": getattr(store, "lakehouse_ids", {}).get(lh, ""), "lakehouse_name": lh,
                "schema": e.schema, "table": e.table, "columns": e.columns}

    def mesh_publish(self, env: str) -> dict[str, Any]:
        """Record every published product's ports, with where they live in ``env``, in the catalog.
        Promote does this; it can be run by hand after changing a product. A product published as
        deprecated for the first time, or with a new date, tells the projects that read it in Teams;
        that notice never fails the publish."""
        project = self._project()  # type: ignore[attr-defined]
        catalog = self._catalog()
        saved = catalog.call("enrichment", project=mesh.project_name(project))
        problems = mesh.publish_problems(project, saved)
        if problems:
            raise GateError("promote", problems)
        name = mesh.project_name(project)
        before = self._published_before(catalog, project)
        done: list[str] = []
        problems = []
        events: list[dict[str, Any]] = []
        tell: list[dict[str, Any]] = []
        old_function = False
        try:
            for p in project.products.values():
                if p.status == "draft":
                    continue
                ports = [{"port": port["name"], **self._port_location(project, env, port["entity"])} for port in p.ports]
                info = p.summary() | {"project": name, "domain": project.info.get("domain", "")}
                out = catalog.call("register_product", product=info, ports=ports, env=env)
                done.append(p.name)
                # The port history came with versioned contracts: an older UDF_Platform publishes without it.
                old_function = old_function or not (isinstance(out, dict) and "history" in out)
                try:
                    self._publish_notices(p, info, out, before, name, env, events, tell, problems)
                except Exception as exc:  # noqa: BLE001 - the product is published; a notice never fails that
                    problem = f"products/{p.name} is published, but its Teams notice couldn't be made: {describe(exc)}"
                    _log.warning(problem)
                    problems.append(problem)
        except Exception:
            # A later product failed to publish: the notices for the ones already published still go,
            # since the next publish finds them unchanged and wouldn't send them again.
            if events:
                self._governance_notify(lambda: events)  # type: ignore[attr-defined]
            raise
        problems += self._sync_links(project, catalog)
        if old_function:
            problems.append("Published, but the catalog keeps no port history yet: UDF_Platform is older than "
                            "versioned contracts. A platform admin runs Platform > Settings > Finish or repair once.")
        if events:
            problem = self._governance_notify(lambda: events)  # type: ignore[attr-defined]
            if problem:
                problems.append(problem)
        # Who was told, and what didn't happen, only when there is something to say.
        return {"env": env, "published": done} | ({"told": tell} if tell else {}) | ({"problems": problems} if problems else {})

    def _publish_notices(self, p: Any, info: dict[str, Any], out: Any, before: dict[str, dict[str, Any]] | None,
                         name: str, env: str, events: list[dict[str, Any]], tell: list[dict[str, Any]],
                         problems: list[str]) -> None:
        """The Teams notices one published product calls for, added to ``events`` and ``tell``."""
        told: set[str] = set()  # the people the deprecation notice names
        if p.status == "deprecated":
            if before is None:
                problems.append(f"products/{p.name} is deprecated, but the catalog's readers couldn't be read, so "
                                "no Teams notice went to them. Publish again to try once more.")
            else:
                was = before.get(f"{name}/{p.name}")
                stops = str(info.get("deprecated_on") or "")
                if was and (was.get("status") != "deprecated" or str(was.get("deprecated_on") or "") != stops):
                    consumers = [c for c in was.get("consumers") or [] if isinstance(c, dict)]
                    event = notices.product_deprecated(was | {"deprecated_on": stops, "domain_id": info["domain"]}, consumers)
                    if event:
                        told = {u.lower() for u in event["to"]["people"]}
                        events.append(event)
                        tell += [{"product": was["id"], "project": c["consumer_project"], "alias": c.get("alias", ""),
                                  "deprecated_on": stops} for c in consumers
                                 if c.get("consumer_project") and c["consumer_project"] != name]
        # Readers hear about changes in Prod, the environment their own projects read. People the
        # deprecation notice just named don't hear about the deprecation twice; other followers do.
        changes = (out.get("changes") or []) if isinstance(out, dict) else []
        if env == "prod" and changes:
            me = (self._user() or "").lower()  # type: ignore[attr-defined]
            followers = [f for f in out.get("followers") or [] if isinstance(f, str) and f.lower() != me]  # the promoter knows
            rest = [c for c in changes if not str(c).startswith("Deprecated")]
            for people, said, suffix in (([f for f in followers if f.lower() not in told], changes, ""),
                                         ([f for f in followers if f.lower() in told], rest, ":told")):
                event = notices.product_changed(out, said, people, env)
                if event:
                    events.append(event | {"dedupe": event["dedupe"] + suffix})

    def _published_before(self, catalog: Any, project: Any) -> dict[str, dict[str, Any]] | None:
        """The catalog's products before a publish, read only when one of the project's is deprecated
        (to tell its readers once); None when they can't be read."""
        if not any(p.status == "deprecated" for p in project.products.values()):
            return {}
        try:
            return {p["id"]: p for p in catalog.call("list_products")}
        except Exception as exc:  # noqa: BLE001 - publishing doesn't need it; only the notice does, and says so
            _log.info("Products not read before publishing: %s", describe(exc))
            return None

    def mesh_save_link(self, link: dict[str, Any]) -> dict[str, Any]:
        """Write metadata/links/<alias>.toml and tell the producer's catalog entry who reads it, the
        versions it accepts and the columns it relies on. A new link without ``accepts`` accepts the
        product's current version, so a later breaking version doesn't reach it unnoticed."""
        project = self._project()  # type: ignore[attr-defined]
        reads = {env: (link.get("reads") or {}).get(env, env) for env in ENVIRONMENTS}
        alias = link.get("alias", "")
        kept = project.links.get(alias)
        if kept is not None and kept.product != link.get("product", ""):
            kept = None  # the alias now names another product: its versions and columns don't carry over
        # The New link dialog sends neither accepts nor columns: saving the same link again keeps the file's.
        if kept is not None and "accepts" not in link:
            link = link | {"accepts": kept.accepts}
        if kept is not None and "columns" not in link:
            link = link | {"columns": kept.columns}
        accepts = str(link.get("accepts") or "").strip()
        if accepts:
            try:
                mesh.parse_accepts(accepts)
            except ValueError as exc:
                raise GateError("save", [f"links/{alias}: {exc}"]) from None
        elif alias not in project.links:
            accepts = self._current_version(link.get("product", ""))
        columns = {str(k): str(v) for k, v in (link.get("columns") or {}).items()}
        problems = []
        if not link.get("pinned"):
            problems += self._register_link(consumer=mesh.project_name(project), alias=alias,
                                            product_id=link.get("product", ""), port=link.get("port", ""),
                                            reads=reads, accepts=accepts, columns=columns)
        mesh.write_link(project.root, link | {"reads": reads, "accepts": accepts})
        project.reload()
        errors = [e for e in mesh.validate(project) if e.startswith(f"links/{alias}")]
        if errors:
            raise GateError("save", errors)
        out = self.mesh_overview()
        out["problems"] = out.get("problems", []) + problems
        return out

    def _sync_links(self, project: Any, catalog: Any) -> list[str]:
        """Send each link's file to the catalog where the catalog has something else (accepts or columns
        edited in links/<alias>.toml, say), so the producer's promote checks what the file says. A link the
        catalog refuses is a problem to fix, never a failed publish."""
        name = mesh.project_name(project)
        try:
            recorded = {c["id"]: c for p in catalog.call("list_products") for c in p.get("consumers", [])}
        except Exception as exc:  # noqa: BLE001 - publishing is done; the links are sent on the next one
            return [f"The project's links weren't checked against the catalog: {describe(exc)}"]
        problems = []
        for link in project.links.values():
            if link.location("dev") or link.location("test") or link.location("prod"):
                continue  # pinned: not in the catalog
            reads = {env: link.reads(env) for env in ENVIRONMENTS}
            want = (link.product, link.port, reads, link.accepts, link.columns)
            have = recorded.get(f"{name}:{link.alias}")
            if have is not None and (have.get("product_id"), have.get("port"), have.get("reads") or {},
                                     have.get("accepts") or "", have.get("columns") or {}) == want:
                continue
            try:
                problems += self._register_link(consumer=name, alias=link.alias, product_id=link.product,
                                                port=link.port, reads=reads, accepts=link.accepts, columns=link.columns)
            except core.PlatformError as exc:
                problems.append(f"links/{link.alias} isn't in the catalog as its file says: {exc}")
        return list(dict.fromkeys(problems))

    def _current_version(self, product_id: str) -> str:
        """The product's version in the catalog, for a new link's accepts; "" (any) when it can't be read."""
        try:
            found = next((p for p in self._catalog().call("list_products") if p["id"] == product_id), None)
        except Exception as exc:  # noqa: BLE001 - the link still saves; it accepts any version, as links did before
            _log.info("Product version not read for a new link: %s", describe(exc))
            return ""
        version = str((found or {}).get("version") or "").strip()
        return version if version.isdigit() else ""

    def _register_link(self, **kwargs: Any) -> list[str]:
        """register_link with the link's accepts and columns. An older UDF_Platform has neither: if it
        refuses the call, the link is registered without them, and the answer says what to run. If the
        call fails again, that refusal is the real one and is raised."""
        catalog = self._catalog()
        repair = ("The link is saved, but the catalog doesn't keep the versions it accepts or the columns it "
                  "relies on yet: UDF_Platform is older than versioned contracts. A platform admin runs "
                  "Platform > Settings > Finish or repair once.")
        extra = {k: kwargs.pop(k) for k in ("accepts", "columns")}
        try:
            out = catalog.call("register_link", **kwargs, **extra)
        except core.PlatformError as exc:
            if not _unknown_argument(exc):
                raise  # the catalog's own refusal (deprecated, private, not published): never saved without it
            _log.info("register_link with accepts and columns: %s", describe(exc))
            catalog.call("register_link", **kwargs)
            return [repair]
        # A function that took the call but didn't know the new fields answers without them.
        return [] if isinstance(out, dict) and "accepts" in out else [repair]

    def mesh_delete_link(self, alias: str) -> dict[str, Any]:
        project = self._project()  # type: ignore[attr-defined]
        users = [e.name for e in project.entities.values() if e.spec.get("source", {}).get("link") == alias]
        if users:
            raise GateError("save", [f"can't delete link {alias}: {', '.join(users)} read it"])
        (project.root / "metadata" / "links" / f"{alias}.toml").unlink(missing_ok=True)
        return self.mesh_overview()

    def _link_table(self, project: Any, env: str):
        """For the engine: a link's alias to the OneLake address it reads in ``env``."""
        def resolve(alias: str) -> str:
            link = project.links.get(alias)
            if link is None:
                raise LookupError(f"link {alias} isn't in metadata/links")
            if link.location(env):
                return link.location(env)
            port = self._catalog().call("resolve_port", product_id=link.product, port=link.port, env=link.reads(env))
            if not port or not port.get("lakehouse_id"):
                raise LookupError(f"{link.product} hasn't published port {link.port} in {link.reads(env)} yet. "
                                  f"Its owner publishes it when they promote to {link.reads(env)}.")
            return TableRef(port["workspace_id"], port["lakehouse_id"], port["schema_name"] or "dbo",
                            port["table_name"]).uri
        return resolve

    def mesh_resolve_links(self, env: str = "dev") -> list[dict[str, Any]]:
        project = self._project()  # type: ignore[attr-defined]
        resolve = self._link_table(project, env)
        out = []
        for link in project.links.values():
            try:
                out.append({"alias": link.alias, "table": resolve(link.alias), "ok": True})
            except Exception as exc:  # noqa: BLE001
                out.append({"alias": link.alias, "ok": False, "message": str(exc)})
        return out

    # ---- catalog text (enrichment)

    def mesh_about(self, name: str) -> dict[str, Any]:
        """An entity's catalog text: what the catalog saved, else the starter text in its file."""
        project = self._project()  # type: ignore[attr-defined]
        e = project.entities[name]
        problem = None
        try:
            saved = self._catalog().call("enrichment", project=mesh.project_name(project))
            glossary = [t["term"] for t in self._catalog().call("list_glossary")]
        except Exception as exc:  # noqa: BLE001
            saved, glossary, problem = {}, [], str(exc)
        return mesh.merged_catalog(project, e, saved) | {
            "entity": name, "products": mesh.products_of(project, name), "owner": e.spec.get("owner", ""),
            "classifications": list(mesh.CLASSIFICATIONS), "glossary": glossary, "problem": problem,
            "publish_problems": [x for x in mesh.publish_problems(project, saved) if x.startswith(name + ":")]}

    def mesh_save_about(self, name: str, fields: dict[str, Any], column: str = "") -> dict[str, Any]:
        project = self._project()  # type: ignore[attr-defined]
        if name not in project.entities:
            raise LookupError(f"{name} isn't an entity in this project")
        target = f"{mesh.project_name(project)}:{name}" + (f"#{column}" if column else "")
        allowed = {"description", "tags", "classification", "terms", "steward", "certified"}
        self._catalog().call("save_enrichment", target=target, fields={k: v for k, v in fields.items() if k in allowed})
        return self.mesh_about(name)

    # ---- workspaces by designation

    def meta_set_designation(self, env: str, designation: str, workspace_id: str = "",
                             workspace_name: str = "") -> dict[str, Any]:
        """Point a workspace designation (ingest, product, ...) of an environment at a workspace.
        An empty workspace_id goes back to the environment's own workspace."""
        if env not in ENVIRONMENTS:
            raise ValueError(f"environment must be one of {', '.join(ENVIRONMENTS)}")
        if designation not in mesh.DESIGNATIONS:
            raise ValueError(f"designation must be one of {', '.join(mesh.DESIGNATIONS)}")
        project = self._project()  # type: ignore[attr-defined]
        envs = {k: dict(v) for k, v in project.environments.items()}
        spaces = dict(envs.get(env, {}).get("workspaces") or {})
        if workspace_id:
            spaces[designation] = {"id": workspace_id, "name": workspace_name}
        else:
            spaces.pop(designation, None)
        envs.setdefault(env, {})["workspaces"] = spaces
        if not spaces:
            envs[env].pop("workspaces")
        self._write_environments(project, envs)  # type: ignore[attr-defined]
        return self.mesh_overview()

    def meta_create_designation_workspace(self, env: str, designation: str, capacity_id: str = "") -> dict[str, Any]:
        project = self._project()  # type: ignore[attr-defined]
        i = mesh.identity(project)
        domain = (i["subdomain"] or i["domain"] or i["name"]).replace("-", " ").title()
        name = f"{domain} {mesh.DESIGNATION_LABELS[designation]} - {env.title()}"
        ws = self.fabric.create_workspace(name, capacity_id,  # type: ignore[attr-defined]
                                          f"{mesh.DESIGNATION_LABELS[designation]} workspace, managed by KratosForge")
        return self.meta_set_designation(env, designation, ws["id"], ws.get("displayName") or name)

    # ---- keys into SQL_Platform

    def meta_keys_to_platform(self, env: str) -> dict[str, Any]:
        """Copy this environment's keys from LH_Keys into the platform with the same numbers, check
        every row, and switch the environment to the platform's keys only if all of them match."""
        project = self._project()  # type: ignore[attr-defined]
        if self._keys_in_platform(project, env):  # type: ignore[attr-defined]
            return {"env": env, "ok": True, "already": True, "tables": []}
        lakehouse_keys = self._keys_store(project, env)  # type: ignore[attr-defined]
        if self._keys_config().get("shared") and env != "prod":
            # One keys database: Prod's numbers are already there, so Dev and Test keys Prod
            # doesn't have get new ones, and their tables need one re-run.
            report = migrate(project, lakehouse_keys, self._platform_keys(env), keep_numbers=False)
        else:
            report = migrate(project, lakehouse_keys, self._platform_keys(env))
        if report["ok"]:
            envs = {k: dict(v) for k, v in project.environments.items()}
            envs[env]["keys"] = "platform"
            envs[env]["keys_moved_at"] = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
            self._write_environments(project, envs)  # type: ignore[attr-defined]
        return {"env": env, **report}

    def meta_keys_to_lakehouse(self, env: str) -> dict[str, Any]:
        """Go back to LH_Keys. Refused while the platform holds keys LH_Keys doesn't have: going back
        then would hand out those numbers again to other business keys."""
        project = self._project()  # type: ignore[attr-defined]
        if self._keys_in_platform(project, env):  # type: ignore[attr-defined]
            platform = self._platform_keys(env)
            envs_now = {k: dict(v) for k, v in project.environments.items()}
            envs_now.setdefault(env, {})["keys"] = "lakehouse"
            project.environments = envs_now
            try:
                lakehouse_keys = self._keys_store(project, env)  # type: ignore[attr-defined]
            finally:
                project.reload()
            newer = []
            for kt in project.key_tables.values():
                have = keys_mod.key_stats(lakehouse_keys, kt)
                top = platform.stats(kt.name)
                if top.get("next", 1) > have.get("next", 1):
                    newer.append(f"{kt.name} (keys {have['next']} to {top['next'] - 1})")
            if newer:
                raise GateError("save", [f"The platform issued keys after the move that LH_Keys doesn't have: "
                                         f"{', '.join(newer)}. Going back would reuse those numbers, so keys stay "
                                         "in the platform."])
        envs = {k: dict(v) for k, v in project.environments.items()}
        envs.setdefault(env, {})["keys"] = "lakehouse"
        envs[env].pop("keys_moved_at", None)
        self._write_environments(project, envs)  # type: ignore[attr-defined]
        return {"env": env, "keys": "lakehouse"}

