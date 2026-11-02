"""Best practices: checks and templates (Preview), mixed into ``Service``.

Templates are reusable policy definitions (central/policy.py). Check now checks the open metadata
project against the policies in force for it: it resolves them as Explain does (the platform's
assignments plus the project's governance.toml), runs each policy's **Checked by** rules from the
registry (governance_checks.py) on this PC, and turns a finding an active exception covers into
"Excepted until <date>: <why>". Check now itself stops nothing.

The promote gate (GOV-003, ``governance_promote_gate``) runs the same checks for the project's Block
policies: a finding no exception covers is one of the promote gate's reasons. Warn and Advisory policies
never stop a promote. When the platform can't be reached, the latest package on this PC stands in; when
neither can be read, a promote to Prod stops and one to Test or Dev only warns.

What it reads: the project's files on this PC, the platform's catalog (policies, exceptions, catalog
text, product terms and endorsement) and the run logs the app already reads for Monitor; when a policy
names notebook or SQL rules, also this PC's notebooks and its schedules (so a notebook a schedule runs
gets the scheduled-only rules), and its SQL scripts: saved queries and the .sql files of the warehouse
projects in the repositories opened in Source control. What it writes: ``governance_checks.json`` in the app's data folder, the last result per project, so
"Checked on this PC 5 min ago" can show, and the project's policy package (policy_package.py) in
``governance_packages/<project>.json`` there, refreshed each time the platform answers. Checkers that run in
Fabric are never started here.

Offline (GOV-002): when the platform can't be reached, Check now resolves the policies from the latest
package on this PC instead, and says which package and how old it is. ``governance_check_from_package``
(the ``fabric-desktop governance check`` CLI) always does. Either way nothing calls the platform, Fabric or
OneLake: catalog text, product terms and endorsement, and the run logs aren't read, so the rules that need
them say unknown, with why.
"""

from __future__ import annotations

import datetime as dt
import logging
from pathlib import Path
from typing import Any

import requests

from .. import governance_checks as gc
from ..filenames import file_stem
from ..jsonfile import read_json, write_json
from ..meta import mesh
from ..redact import describe
from . import core
from . import policy_package as pp
from .governance_service import local_assignments
from .policy import BUILTIN_TEMPLATES
from .requests_service import call_or_repair

_log = logging.getLogger(__name__)

STATE_FILE = "governance_checks.json"
WARN_ONLY = "Check now stops nothing. A promote stops on a finding of a Block policy that no exception covers."
ENVS_HIGH_FIRST = ("prod", "test", "dev")
# A data product's checklist, from the contract template it follows.
CHECKLIST = (("ownership.product_owner", "Owner and steward"), ("contract.description", "Description"),
             ("contract.terms_linked", "Business terms linked"), ("contract.freshness", "Fresh within {within_hours} h"),
             ("contract.endorsed", "Endorsement"))
_TERMS_RULES = {"contract.terms_linked", "contract.endorsed"}
_LOAD_RULES = {"contract.freshness", "contract.row_count"}
OFFLINE_TRUST = "Checked offline from the policy package: the platform's catalog wasn't read."
OFFLINE_LOADS = "Checked offline from the policy package: the run logs weren't read."
OFFLINE_TEXT = "Checked offline: the platform's catalog text wasn't read, so only the text in the project's files was checked."
# The promote gate (GOV-003) falls back to the policy package only when the platform can't be reached at all.
UNREACHABLE = (requests.RequestException, ConnectionError, TimeoutError)
# Promotes to these only warn when the policies can't be read; any other target is treated as Prod (fail closed).
WARN_ONLY_TARGETS = ("dev", "test")
# A promote to Prod uses the policy package on this PC only when it was taken this recently: an old one may
# miss a Block policy assigned since. Test and Dev take any verified package (they only warn when there's none).
PROD_PACKAGE_MAX_DAYS = 1.0
GATE_LISTED = 10  # findings named per Block policy in the promote gate's reasons; Check now lists them all


class PoliciesUnreadable(Exception):
    """The policies in force can't be read: not from the platform, and not from a usable package."""


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def last_load(runs: list[dict[str, Any]], entities: list[str], unavailable: dict[str, str] | None = None) -> dict[str, Any]:
    """When a product's tables last loaded: in the highest environment where every port's table has a
    successful run, the oldest of those runs (a product is as fresh as its stalest table) and the fewest
    rows any of them wrote. Unknown, with the reason, when the run logs don't say."""
    if not entities:
        return {"last_load": None, "last_rows": None, "load_reason": "The product has no port, so nothing loads it."}
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    for r in runs:  # newest first
        if r.get("status") == "ok" and r.get("entity") in entities:
            latest.setdefault((r.get("env") or "dev", r["entity"]), r)
    partial = ""
    for env in ENVS_HIGH_FIRST:
        here = [latest.get((env, e)) for e in entities]
        loaded = [r for r in here if r]
        if len(loaded) == len(here):
            rows = [r.get("rows_written") for r in loaded]
            counts = [x for x in rows if x is not None]
            return {"last_load": min(str(r["started_at"]) for r in loaded), "load_env": env,
                    "last_rows": None if len(counts) != len(rows) else min(int(x) for x in counts)}
        if any(here) and not partial:
            missing = [e for e, r in zip(entities, here) if not r]
            partial = f"{', '.join(missing)} hasn't loaded in {env.title()} yet."
    why = partial or "No successful load is recorded in any run log this PC can read."
    if unavailable:
        why += " " + " ".join(f"{env.title()}: {text}." for env, text in sorted(unavailable.items()))
    return {"last_load": None, "last_rows": None, "load_reason": why}


def _markers(*sources: Any) -> list[str]:
    out: set[str] = set()
    for src in sources:
        if not isinstance(src, dict):
            continue
        tags = src.get("tags") or []
        tags = [t.strip() for t in tags.split(",")] if isinstance(tags, str) else list(tags)
        out |= {str(t).strip().lower() for t in tags if str(t).strip()}
        out |= {k for k in ("pii", "phi") if src.get(k) is True}
    return sorted(out)


def entity_items(project: Any, saved: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The project's entities as items for the checkers: name, layer, owner, classification and each
    column's label and PII/PHI markers (tags or pii = true / phi = true in [catalog.columns.<name>])."""
    pname = mesh.project_name(project)
    out = []
    for name, e in sorted(project.entities.items()):
        base = mesh.entity_catalog(e)
        merged = mesh.merged_catalog(project, e, saved)
        cols = [{"name": c["name"], "classification": c.get("classification") or "",
                 "markers": _markers(base["columns"].get(c["name"]), saved.get(f"{pname}:{name}#{c['name']}"))}
                for c in merged["columns"]]
        out.append({"kind": "entity", "id": name, "project": pname, "layer": e.layer, "table": e.table,
                    "owner": str(e.spec.get("owner") or ""), "classification": merged.get("classification") or "",
                    "columns": cols})
    return out


class ChecksMixin:

    # ---- templates

    def _gc_catalog(self) -> Any:
        return self._catalog()  # type: ignore[attr-defined]

    def governance_templates(self) -> dict[str, Any]:
        return call_or_repair(self._gc_catalog(), "list_policy_templates", "list policy templates")

    def governance_template_save(self, template: dict[str, Any], id: str = "") -> dict[str, Any]:
        return call_or_repair(self._gc_catalog(), "save_policy_template", "save policy templates", template=template, id=id)

    def governance_template_delete(self, id: str) -> dict[str, Any]:
        return call_or_repair(self._gc_catalog(), "delete_policy_template", "delete policy templates", id=id)

    def governance_template_use(self, template: str, id: str = "", name: str = "") -> dict[str, Any]:
        return call_or_repair(self._gc_catalog(), "use_policy_template", "make policies from templates",
                              template=template, id=id, name=name)

    def governance_template_from_policy(self, policy: str, id: str = "", name: str = "") -> dict[str, Any]:
        return call_or_repair(self._gc_catalog(), "save_policy_as_template", "save policies as templates",
                              policy=policy, id=id, name=name)

    def governance_template_authors(self, authors: str) -> dict[str, Any]:
        return call_or_repair(self._gc_catalog(), "set_template_authors", "choose who adds templates", authors=authors)

    # ---- the last result, on this PC

    def _gc_path(self) -> Any:
        return self.root / STATE_FILE  # type: ignore[attr-defined]

    def _gc_state(self) -> dict[str, Any]:
        state = read_json(self._gc_path(), {})
        return state if isinstance(state, dict) else {}

    def governance_last_check(self) -> dict[str, Any]:
        """The open project's last Check now result, or none yet."""
        try:
            project = self._project()  # type: ignore[attr-defined]
        except LookupError:
            return {"project": None, "result": None}
        pname = mesh.project_name(project)
        return {"project": pname, "result": self._gc_state().get("results", {}).get(pname)}

    def governance_practice_settings(self, kind: str) -> dict[str, Any]:
        """How the policies in force for the open project set one kind of best-practice rule ("notebook" or
        "sql"), for the editors' Best practices panels: ``{rule_id: {"enabled", "severity"}}`` for the rules a
        policy names. A rule no policy names keeps its own severity. Two policies naming one rule: it's on if
        either turns it on, at the worse severity. With no project open, or no catalog, every rule is as built."""
        if kind not in ("notebook", "sql"):
            raise ValueError('kind must be "notebook" or "sql"')
        try:
            resolved = self.governance_resolve()  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - the panel still checks with the rules as built
            return {"settings": {}, "policies": [], "note": f"The policies couldn't be read, so every rule is on as built: {describe(exc)}"}
        out: dict[str, dict[str, Any]] = {}
        names: list[str] = []
        for pol in resolved.get("policies") or []:
            named = False
            for rid in gc.rule_ids(pol.get("rule")):
                found = gc.find_rule(rid)
                if found is None or found[1]["kind"] != kind:
                    continue
                named = True
                s = gc.settings_for(found[1], pol.get("severity") or "", pol.get("params") or {})
                was = out.get(rid)
                if was is None or (s["enabled"] and not was["enabled"]):
                    out[rid] = {"enabled": s["enabled"], "severity": s["severity"]}
                elif s["enabled"] == was["enabled"]:
                    worse = min(was["severity"], s["severity"], key=gc.FINDING_SEVERITIES.index)
                    out[rid] = {"enabled": was["enabled"], "severity": worse}
            if named:
                names.append(str(pol.get("name") or pol.get("id") or ""))
        return {"settings": out, "policies": names, "note": ""}

    # ---- Check now

    def _gc_trust(self, product_id: str) -> tuple[dict[str, Any] | None, str]:
        """A product's terms and endorsement from the catalog, or why they can't be read."""
        try:
            return call_or_repair(self._gc_catalog(), "product_trust", "show endorsement and business terms",
                                  product_id=product_id), ""
        except LookupError as exc:
            return None, str(exc)
        except core.PlatformError:
            return None, "It isn't in the platform's catalog yet: a product is registered there when it's promoted."
        except Exception as exc:  # noqa: BLE001 - the platform can't be reached: unknown, never a pass
            _log.info("product_trust %s: %s", product_id, describe(exc))
            return None, f"The platform couldn't be read: {describe(exc)}"

    @staticmethod
    def _gc_apply_trust(item: dict[str, Any], trust: dict[str, Any] | None, why: str) -> dict[str, Any]:
        if trust is None:
            return item | {"terms": None, "terms_reason": why, "endorsement": None, "endorsement_reason": why}
        seen = [{"item_name": i.get("item_name") or i.get("item_id"), "endorsement": i.get("endorsement")}
                for i in trust.get("items") or []]
        return item | {"terms": [t["term"] for t in trust.get("terms") or []], "endorsement": seen}

    def _gc_runs(self) -> tuple[list[dict[str, Any]], dict[str, str]]:
        data = self.meta_runs_all(limit=5000)  # type: ignore[attr-defined]
        return data.get("runs") or [], data.get("unavailable") or {}

    def _gc_notebook_items(self, problems: list[str]) -> list[dict[str, Any]]:
        """This PC's notebooks, their code cells' sources, for the notebook best-practice rules. A notebook
        an enabled schedule on this PC runs (a notebook schedule, or a Notebook step of a pipeline
        schedule) is marked scheduled, so the scheduled-only rules check it."""
        out = []
        try:
            scheduled = self.pc_schedules.notebooks()  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - notebooks are still checked, without the scheduled-only rules
            scheduled = set()
            problems.append(f"This PC's schedules couldn't be read, so no notebook was checked as scheduled: {describe(exc)}")
        for nb in self.notebooks.list():  # type: ignore[attr-defined]
            try:
                cells = self.notebooks.load(nb["name"])["cells"]  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001 - one unreadable notebook doesn't stop the others
                problems.append(f"The notebook {nb['name']} couldn't be read, so it wasn't checked: {describe(exc)}")
                continue
            out.append({"kind": "notebook", "id": nb["name"], "scheduled": nb["name"] in scheduled,
                        "sources": [c.get("source") or "" for c in cells if c.get("type", "code") == "code"]})
        return out

    def _gc_sql_items(self, problems: list[str]) -> list[dict[str, Any]]:
        """This PC's SQL scripts, for the SQL best-practice rules: saved queries (My queries and the
        repositories' sql-queries folders) and the .sql files of the warehouse projects in the
        repositories opened in Source control. Nothing is run; the scripts are only read."""
        from ..warehouse_project_service import MAX_FILE

        out: list[dict[str, Any]] = []
        try:
            saved = self.sql_queries_list()  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - the warehouse projects are still checked
            problems.append(f"The saved queries couldn't be listed, so they weren't checked: {describe(exc)}")
            saved = {"queries": [], "notes": []}
        problems += [f"Saved queries: {n}" for n in saved.get("notes") or []]
        for q in saved["queries"]:
            name = f"{q['place_label']}/{q['name']}"
            if q.get("problem"):
                problems.append(f"The saved query {name} wasn't checked: {q['problem']}")
            else:
                out.append({"kind": "sql", "id": name, "query": q["sql"], "target": "warehouse"})
        try:
            projects = self.wh_projects_list()["projects"]  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 - the saved queries are still checked
            problems.append(f"The warehouse projects couldn't be listed, so they weren't checked: {describe(exc)}")
            projects = []
        for p in projects:
            folder = Path(p["repo"]).resolve() / p["folder"]
            files, cut = self._wh_walk(folder)  # type: ignore[attr-defined]
            if cut:
                problems.append(f"The warehouse project {p['name']} has more .sql files than are read; the first {len(files)} were checked.")
            for f in files:
                name = f"{p['name']}/{f.relative_to(folder).as_posix()}"
                try:
                    if f.stat().st_size > MAX_FILE:
                        raise ValueError(f"It is larger than {MAX_FILE // 1024} KB.")
                    text = f.read_bytes().decode("utf-8-sig")
                except (OSError, ValueError) as exc:  # UnicodeDecodeError is a ValueError
                    problems.append(f"The SQL file {name} couldn't be read, so it wasn't checked: {describe(exc)}")
                    continue
                out.append({"kind": "sql", "id": name, "query": text, "target": "warehouse"})
        return out

    def _gc_model_items(self, project: Any, saved: dict[str, dict[str, Any]],
                        problems: list[str]) -> list[dict[str, Any]]:
        """The model Update model writes for the project's model layer (the last one), for the model rules."""
        from .. import model_practices

        try:
            layer = self._model_layer(project, None)  # type: ignore[attr-defined]
            return [model_practices.model_item(project, layer, saved)]
        except Exception as exc:  # noqa: BLE001 - the other rules still run
            problems.append(f"The semantic model couldn't be planned, so it wasn't checked: {describe(exc)}")
            return []

    def _gc_product_items(self, project: Any, need_load: bool, need_trust: bool, offline: bool = False) -> list[dict[str, Any]]:
        pname = mesh.project_name(project)
        runs, unavailable = self._gc_runs() if need_load and project.products and not offline else ([], {})
        out = []
        for name, p in sorted(project.products.items()):
            entities = [str(x.get("entity") or "") for x in p.ports if x.get("entity")]
            item = {"kind": "product", "id": f"{pname}/{name}", "name": name, "project": pname, "status": p.status,
                    "owner": str(p.spec.get("owner") or ""), "steward": str(p.spec.get("steward") or ""),
                    "description": str(p.spec.get("description") or ""), "entities": entities}
            if need_load and offline:
                item |= {"last_load": None, "last_rows": None, "load_reason": OFFLINE_LOADS}
            elif need_load:
                item |= last_load(runs, entities, unavailable)
            if need_trust and p.status != "draft":
                item = self._gc_apply_trust(item, *((None, OFFLINE_TRUST) if offline else self._gc_trust(item["id"])))
            out.append(item)
        return out

    # ---- the policy package (GOV-002)

    def _gc_package_path(self, project: str) -> Path:
        return self.root / pp.FOLDER / f"{file_stem(project, 'project')}.json"  # type: ignore[attr-defined]

    def _gc_take_package(self, project: str, resolved: dict[str, Any]) -> tuple[dict[str, Any], Path]:
        """Take the project's policy package from the platform and keep it on this PC, replacing the last one."""
        cat = self._gc_catalog()
        snap = call_or_repair(cat, "policy_snapshot", "take a policy package")
        exceptions = call_or_repair(cat, "list_policy_exceptions", "list policy exceptions", active=True)
        package = pp.take(project, pp.platform_of(cat), resolved, snap, exceptions)
        path = self._gc_package_path(project)
        write_json(path, package)
        return package, path

    def governance_package_export(self, out: str = "") -> dict[str, Any]:
        """Take the open project's policy package from the platform now: kept on this PC (what Check now uses
        offline) and, with ``out``, also written there (for a CI job's ``governance check --package``)."""
        project = self._project()  # type: ignore[attr-defined]
        pname = mesh.project_name(project)
        package, path = self._gc_take_package(pname, self.governance_resolve())  # type: ignore[attr-defined]
        if out:
            write_json(Path(out), package)
        return pp.summary(package, Path(out) if out else path) | {"kept": str(path)}

    def _gc_offline_resolve(self, project: Any, package: str = "", expect_sha256: str = "",
                            max_age_days: float | None = None, environment: str = "") -> tuple[dict[str, Any], dict[str, Any]]:
        """The policies in force from a policy package (``package``, else the latest one on this PC), verified
        first: a package whose hashes don't match raises PackageError and is never used. ``expect_sha256`` (a CI
        job's pinned hash) is checked before anything else, so a package with recomputed hashes is refused too;
        ``max_age_days`` refuses a package taken longer ago."""
        pname = mesh.project_name(project)
        path = Path(package) if package else self._gc_package_path(pname)
        if not package and not path.exists():
            raise pp.PackageError(f"There's no policy package for {pname} on this PC yet, so it can't be checked "
                                  "offline. Run Check now once while the platform can be reached, or pass --package.")
        pkg = pp.read(path, expect_sha256=expect_sha256, max_age_days=max_age_days)
        if pkg["project"] != pname:
            raise pp.PackageError(f"The policy package {path} is for {pkg['project']}, not {pname}. It isn't used.")
        local, problems = local_assignments(project.root, pname)
        out = pp.resolve_offline(pkg, local, context={"environment": environment} if environment else None)
        return out | {"problems": problems}, pp.summary(pkg, path)

    def governance_check_from_package(self, package: str = "", remember: bool = False, expect_sha256: str = "",
                                      max_age_days: float | None = None) -> dict[str, Any]:
        """Check now without the platform: the policies come from a policy package (``package``, else the
        latest on this PC). No platform, Fabric or OneLake call. ``remember`` keeps the result as Check now's last;
        ``expect_sha256`` and ``max_age_days`` refuse a package that isn't the pinned one or is too old."""
        return self._gc_check(offline=True, package=package, remember=remember,
                              pin={"expect_sha256": expect_sha256, "max_age_days": max_age_days})

    def governance_check_now(self) -> dict[str, Any]:
        """Check the open project against the policies in force for it, on this PC. It stops nothing. When the platform
        answers, its policies are used and the project's policy package is refreshed; when it can't be reached,
        the latest package on this PC is used, and the result says which and how old."""
        return self._gc_check()

    def _gc_check(self, offline: bool = False, package: str = "", remember: bool = True,
                  pin: dict[str, Any] | None = None) -> dict[str, Any]:
        project = self._project()  # type: ignore[attr-defined]
        pname = mesh.project_name(project)
        used: dict[str, Any] | None = None
        unreachable = ""
        if offline:
            resolved, used = self._gc_offline_resolve(project, package, **(pin or {}))
        else:
            try:
                resolved = self.governance_resolve()  # type: ignore[attr-defined]
            except core.PlatformError:
                raise  # the platform answered: no package stands in for what it said
            except Exception as exc:  # noqa: BLE001 - not reached: the latest package on this PC stands in
                # Check now stops nothing, so any failure that isn't the platform's own answer may fall back to the
                # package. The promote gate (GOV-003) blocks, so it falls back on connection errors only
                # (UNREACHABLE): an older package must not let a promote through while the platform says otherwise.
                if not self._gc_package_path(pname).exists():
                    raise
                _log.info("Check now for %s uses the policy package: %s", pname, describe(exc))
                unreachable = describe(exc)
                resolved, used = self._gc_offline_resolve(project)
        result = self._gc_evaluate(project, resolved, used, unreachable)
        if remember:
            state = self._gc_state()
            state.setdefault("results", {})[pname] = result
            write_json(self._gc_path(), state)
        return result

    def _gc_evaluate(self, project: Any, resolved: dict[str, Any], used: dict[str, Any] | None = None,
                     unreachable: str = "", refresh: bool = True) -> dict[str, Any]:
        """Run the resolved policies' rules on this PC: Check now's result. ``used`` is the package they came
        from when offline; ``refresh`` takes a new package when they came from the platform."""
        pname = mesh.project_name(project)
        offline = bool(used)
        state = self._gc_state()
        stored = state.get("fabric", {}).get(pname, {})
        problems = list(resolved.get("problems") or [])
        if unreachable:
            problems.insert(0, f"The platform couldn't be reached, so the policies came from the policy package on this PC: {unreachable}")
        refreshed: dict[str, Any] | None = None
        if not offline and refresh:
            try:
                pkg, path = self._gc_take_package(pname, resolved)
                refreshed = pp.summary(pkg, path)
            except Exception as exc:  # noqa: BLE001 - the check itself still runs; the result says so
                _log.info("policy package for %s: %s", pname, describe(exc))
                problems.append(f"The policy package on this PC wasn't refreshed, so an offline check would use an older one: {describe(exc)}")
        wanted = {rid for p in resolved["policies"] for rid in gc.rule_ids(p.get("rule"))}
        if offline:
            saved: dict[str, Any] = {}
            problems.append(OFFLINE_TEXT)
        else:
            try:
                saved = self._gc_catalog().call("enrichment", project=pname)
            except Exception as exc:  # noqa: BLE001 - checks read the Git catalog text alone, and say so
                _log.info("enrichment for %s: %s", pname, describe(exc))
                saved = {}
                problems.append(f"The platform's catalog text couldn't be read, so only the text in the project's files was checked: {describe(exc)}")
        items = entity_items(project, saved) + self._gc_product_items(
            project, bool(wanted & _LOAD_RULES), bool(wanted & _TERMS_RULES), offline)
        kinds = {found[0].kind for found in map(gc.find_rule, wanted) if found}
        if "notebook" in kinds:
            items += self._gc_notebook_items(problems)
        if "sql" in kinds:
            items += self._gc_sql_items(problems)
        if "model" in kinds:
            items += self._gc_model_items(project, saved, problems)
        policies = []
        for pol in resolved["policies"]:
            ids = gc.rule_ids(pol.get("rule"))
            entry: dict[str, Any] = {k: pol.get(k) for k in ("id", "name", "category", "severity", "enforcement", "version")}
            entry |= {"rules": ids, "checked": 0, "passed": 0, "findings": [], "unknown": [], "not_run": [], "missing": [],
                      "exception": pol.get("exception")}
            for rid in ids:
                found = gc.find_rule(rid)
                if found is None:
                    entry["missing"].append(rid)
                    continue
                run = gc.run_rule(rid, items, gc.settings_for(found[1], pol.get("severity") or "", pol.get("params") or {}), stored)
                if run.get("not_run"):
                    entry["not_run"].append({"rule_id": rid, "name": run["name"], "last": run.get("last")})
                    continue
                entry["checked"] += run["checked"]
                entry["passed"] += run["passed"]
                entry["findings"] += run["findings"]
                entry["unknown"] += run["unknown"]
                if run.get("disabled"):
                    entry.setdefault("off", []).append(rid)
                elif not run["checked"]:
                    entry.setdefault("no_items", []).append(("scheduled " if found[1].get("scheduled_only") else "") + found[1]["kind"])
            note = (pol.get("exception") or {}).get("note")
            if note:
                for f in entry["findings"] + entry["unknown"]:
                    f["excepted"] = note
            entry["status"] = ("not_checked" if not ids or (entry["missing"] and not entry["checked"] and not entry["not_run"])
                               else "excepted" if note and entry["findings"]
                               else "findings" if entry["findings"]
                               else "unknown" if entry["unknown"]
                               else "not_run" if entry["not_run"] and not entry["checked"]
                               else "not_checked" if (entry.get("no_items") or entry.get("off")) and not entry["checked"]
                               else "pass")
            if not ids:
                entry["note"] = "Its Checked by field names no rule, so nothing checks it yet."
            elif entry["missing"]:
                entry["note"] = f"No checker on this PC has the rule {', '.join(entry['missing'])}."
            elif entry["status"] == "not_checked" and not entry.get("no_items"):
                entry["note"] = "Its parameters turn its rules off (enabled = false), so nothing checks it."
            elif entry["status"] == "not_checked":
                empty = ", ".join(sorted(set(entry["no_items"])))
                entry["note"] = f"Check now found nothing for its {empty} rules to check."
            entry.pop("no_items", None)
            entry.pop("off", None)
            policies.append(entry)
        order = {"findings": 0, "unknown": 1, "excepted": 2, "not_run": 3, "not_checked": 4, "pass": 5}
        policies.sort(key=lambda p: (order[p["status"]], str(p["name"]).lower()))
        if used:
            note = (f"Checked offline with the policy package taken {used['age']} ago ({used['taken_at'][:16].replace('T', ' ')} UTC) "
                    f"from {used['platform']}: {used['file']}.")
        elif refreshed:
            note = "Policies read from the platform; the policy package on this PC was refreshed."
        else:
            note = "Policies read from the platform."
        result = {"project": pname, "checked_at": _now(), "where": "this PC", "enforcement": WARN_ONLY,
                  "offline": bool(used), "package": used or refreshed, "package_note": note,
                  "policies": policies, "inactive": resolved.get("inactive") or [], "problems": problems,
                  "counts": {"to_look_at": sum(len(p["findings"]) for p in policies if p["status"] != "excepted"),
                             "unknown": sum(len(p["unknown"]) for p in policies if p["status"] != "excepted"),
                             "excepted": sum(len(p["findings"]) for p in policies if p["status"] == "excepted"),
                             "passing": len([p for p in policies if p["status"] == "pass"]),
                             "policies": len(policies)}}
        return result

    # ---- the promote gate (GOV-003)

    def _gc_promote_resolve(self, project: Any, target: str) -> tuple[dict[str, Any], dict[str, Any] | None, str]:
        """The policies in force for a promote to ``target``: from the platform, else, only when it can't be
        reached, from the latest package on this PC (for Prod, one taken within PROD_PACKAGE_MAX_DAYS).
        PoliciesUnreadable when neither can be used: a platform that answers with an error is never stood in
        for by an older package."""
        try:
            return self.governance_resolve(environment=target), None, ""  # type: ignore[attr-defined]
        except UNREACHABLE as exc:
            why = describe(exc)
        except Exception as exc:  # noqa: BLE001 - the platform answered, or failed another way: the gate says why
            raise PoliciesUnreadable(describe(exc)) from exc
        try:
            max_age = None if target in WARN_ONLY_TARGETS else PROD_PACKAGE_MAX_DAYS
            resolved, used = self._gc_offline_resolve(project, environment=target, max_age_days=max_age)
        except pp.PackageError as exc:
            raise PoliciesUnreadable(f"The platform couldn't be reached ({why}), and the policy package on this PC "
                                     f"can't be used: {exc}") from exc
        _log.info("The promote gate for %s uses the policy package: %s", mesh.project_name(project), why)
        return resolved, used, why

    def governance_promote_gate(self, target: str) -> dict[str, Any]:
        """What the open project's policies say about a promote to ``target``, for the promote gate's reasons.

        A finding of a Block policy that no exception covers is an error naming the policy and the finding. An
        active exception that covers it (as in Check now) lets the promote go ahead, and is named in ``excepted``
        and the notes. Warn and Advisory policies never stop a promote. Policies that can't be read stop a
        promote to Prod (fail closed) and are a note for Test and Dev. Reads only: no package is taken and
        Check now's last result is kept as it is."""
        project = self._project()  # type: ignore[attr-defined]
        out: dict[str, Any] = {"errors": [], "notes": [], "excepted": [], "offline": False}
        try:
            resolved, used, unreachable = self._gc_promote_resolve(project, target)
        except PoliciesUnreadable as exc:
            why = str(exc).rstrip(".")
            if target in WARN_ONLY_TARGETS:
                out["notes"].append(f"The governance policies couldn't be read, so they weren't checked: {why}. "
                                    "A promote to Prod stops until they can be read.")
            else:
                out["errors"].append(f"The governance policies couldn't be read, so {target.title()} can't be promoted to "
                                     f"until they can: {why}. Try again when the platform can be reached; Check now "
                                     "keeps a policy package on this PC each time it can.")
            return out
        blocking = [p for p in resolved["policies"] if p.get("enforcement") == "block"]
        if not blocking:
            return out  # Warn and Advisory policies never stop a promote: Check now lists their findings
        result = self._gc_evaluate(project, dict(resolved, policies=blocking), used, unreachable, refresh=False)
        out["offline"] = result["offline"]
        if used:
            out["notes"].append(f"Block policies: {result['package_note']}")
        for pol in result["policies"]:
            name = f'Block policy "{pol["name"]}"'
            found = [(f"{f['object']}: " if f.get("object") else "") + str(f.get("description") or f.get("rule") or "")
                     for f in pol["findings"]]
            if pol["status"] == "excepted":
                waiver = pol.get("exception") or {}
                by = f" Approved by {waiver['approved_by']}." if waiver.get("approved_by") else ""
                out["notes"].append(f"{name}: {len(found)} finding{'s' if len(found) != 1 else ''} let through by an "
                                    f"exception. {waiver.get('note') or ''}{by}".rstrip())
                out["excepted"].append({"policy": pol["id"], "name": pol["name"], "findings": found, "exception": waiver})
                continue
            out["errors"] += [f"{name}: {text}" for text in found[:GATE_LISTED]]
            if len(found) > GATE_LISTED:
                out["errors"].append(f"{name}: {len(found) - GATE_LISTED} more findings. Platform > Govern > Governance > "
                                     "Check this project lists them all.")
            if pol["unknown"] and not pol.get("exception"):
                out["notes"].append(f"{name}: {len(pol['unknown'])} couldn't be checked here, so they don't stop the "
                                    f"promote: {pol['unknown'][0].get('description')}")
            if pol["not_run"]:
                out["notes"].append(f"{name}: its rules that run in Fabric aren't run here, so they don't stop the promote.")
        return out

    # ---- a data product's checklist

    def _gc_contract(self, project: str) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
        """The contract the product's project follows: the first policy in force there with a contract rule,
        else the built-in Data contract template. Its parameters, where they come from, and problems."""
        problems: list[str] = []
        try:
            resolved = self.governance_resolve(project=project)  # type: ignore[attr-defined]
            for pol in resolved["policies"]:
                if any(r.startswith("contract.") for r in gc.rule_ids(pol.get("rule"))):
                    return dict(pol.get("params") or {}), {"policy": pol["id"], "name": pol["name"],
                                                           "severity": pol.get("severity")}, problems
        except Exception as exc:  # noqa: BLE001 - the built-in template still gives the checklist
            _log.info("resolve for %s: %s", project, describe(exc))
            problems.append(f"The project's policies couldn't be read, so the built-in template is used: {describe(exc)}")
        d = BUILTIN_TEMPLATES["data-contract"]
        return {k: v["value"] for k, v in d["params"].items()}, {"template": "data-contract", "name": d["name"],
                                                                 "builtin": True}, problems

    def governance_product_checklist(self, product_id: str) -> dict[str, Any]:
        """A data product's checklist from the contract template it follows: each line passes, fails or is
        unknown (with why), never guessed."""
        product = next((p for p in self._gc_catalog().call("list_products") if p["id"] == product_id), None)
        if product is None:
            raise LookupError(f"{product_id} isn't a published data product")
        params, source, problems = self._gc_contract(product["project"])
        try:
            project = self._project()  # type: ignore[attr-defined]
        except LookupError:
            project = None
        mine = project is not None and mesh.project_name(project) == product["project"] and product["name"] in project.products
        if mine:
            item = next(i for i in self._gc_product_items(project, True, True) if i["name"] == product["name"])
        else:
            elsewhere = f"It's kept in {product['project']}'s files. Open that project on this PC to check it."
            item = {"kind": "product", "id": product["id"], "name": product["name"], "project": product["project"],
                    "status": product.get("status") or "published", "owner": product.get("owner") or "",
                    "description": product.get("description") or "", "steward": None,
                    "steward_reason": f"The steward isn't in the platform's catalog. {elsewhere}",
                    "last_load": None, "last_rows": None,
                    "load_reason": f"Load times are in {product['project']}'s run log. Open that project on this PC to check them."}
            item = self._gc_apply_trust(item, *self._gc_trust(product["id"]))
        if item.get("status") == "draft":
            item["status"] = "published"  # the checklist is what publishing it would need
        rows = []
        for rid, label in CHECKLIST:
            known = gc.find_rule(rid)
            if known is None:
                continue
            checker, rule = known
            settings = gc.settings_for(rule, source.get("severity") or "", params)
            settings["thresholds"] = {k: v for k, v in settings["thresholds"].items() if k in rule.get("default_thresholds", {})}
            found = [f for f in (checker.check(item, {rid: settings}) or []) if f["rule_id"] == rid]
            fail = next((f for f in found if f.get("outcome") != "unknown"), None)
            unknown = next((f for f in found if f.get("outcome") == "unknown"), None)
            rows.append({"rule_id": rid, "label": label.format(**settings["thresholds"]) if "{" in label else label,
                         "mark": "fail" if fail else "unknown" if unknown else "pass",
                         "detail": (fail or unknown or {}).get("description") or _passed(rid, item, settings),
                         "fix": (fail or {}).get("fix")})
        return {"product_id": product["id"], "project": product["project"], "source": source, "rows": rows,
                "open_project": mine, "problems": problems}


def _passed(rule_id: str, item: dict[str, Any], settings: dict[str, Any]) -> str:
    if rule_id == "ownership.product_owner":
        return f"Owner {item['owner']}, steward {item.get('steward')}." if item.get("steward") else f"Owner {item['owner']}."
    if rule_id == "contract.terms_linked":
        return f"{len(item.get('terms') or [])} linked."
    if rule_id == "contract.freshness":
        return f"Last loaded {item['last_load'][:16].replace('T', ' ')} UTC" + (f" in {item['load_env'].title()}." if item.get("load_env") else ".")
    if rule_id == "contract.endorsed":
        return ", ".join(sorted({str(x.get("endorsement")) for x in item.get("endorsement") or []})) + " in Fabric."
    return "Written."
