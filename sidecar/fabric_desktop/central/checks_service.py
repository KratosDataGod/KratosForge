"""Best practices: checks and templates (Preview), mixed into ``Service``.

Templates are reusable policy definitions (central/policy.py). Check now checks the open metadata
project against the policies in force for it: it resolves them as Explain does (the platform's
assignments plus the project's governance.toml), runs each policy's **Checked by** rules from the
registry (governance_checks.py) on this PC, and turns a finding an active exception covers into
"Excepted until <date>: <why>". It is warn only: nothing is blocked.

What it reads: the project's files on this PC, the platform's catalog (policies, exceptions, catalog
text, product terms and endorsement) and the run logs the app already reads for Monitor. What it
writes: only ``governance_checks.json`` in the app's data folder, the last result per project, so
"Checked on this PC 5 min ago" can show. Checkers that run in Fabric are never started here.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any

from .. import governance_checks as gc
from ..jsonfile import read_json, write_json
from ..meta import mesh
from ..redact import describe
from . import core
from .policy import BUILTIN_TEMPLATES
from .requests_service import call_or_repair

_log = logging.getLogger(__name__)

STATE_FILE = "governance_checks.json"
WARN_ONLY = "Warn only: nothing is blocked."
ENVS_HIGH_FIRST = ("prod", "test", "dev")
# A data product's checklist, from the contract template it follows.
CHECKLIST = (("ownership.product_owner", "Owner and steward"), ("contract.description", "Description"),
             ("contract.terms_linked", "Business terms linked"), ("contract.freshness", "Fresh within {within_hours} h"),
             ("contract.endorsed", "Endorsement"))
_TERMS_RULES = {"contract.terms_linked", "contract.endorsed"}
_LOAD_RULES = {"contract.freshness", "contract.row_count"}


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
        if all(here):
            rows = [r.get("rows_written") for r in here]
            return {"last_load": min(str(r["started_at"]) for r in here), "load_env": env,
                    "last_rows": None if any(x is None for x in rows) else min(int(x) for x in rows)}
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
        """This PC's notebooks, their code cells' sources, for the notebook best-practice rules."""
        out = []
        for nb in self.notebooks.list():  # type: ignore[attr-defined]
            try:
                cells = self.notebooks.load(nb["name"])["cells"]  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001 - one unreadable notebook doesn't stop the others
                problems.append(f"The notebook {nb['name']} couldn't be read, so it wasn't checked: {describe(exc)}")
                continue
            out.append({"kind": "notebook", "id": nb["name"], "scheduled": False,
                        "sources": [c.get("source") or "" for c in cells if c.get("type", "code") == "code"]})
        return out

    def _gc_product_items(self, project: Any, need_load: bool, need_trust: bool) -> list[dict[str, Any]]:
        pname = mesh.project_name(project)
        runs, unavailable = self._gc_runs() if need_load and project.products else ([], {})
        out = []
        for name, p in sorted(project.products.items()):
            entities = [str(x.get("entity") or "") for x in p.ports if x.get("entity")]
            item = {"kind": "product", "id": f"{pname}/{name}", "name": name, "project": pname, "status": p.status,
                    "owner": str(p.spec.get("owner") or ""), "steward": str(p.spec.get("steward") or ""),
                    "description": str(p.spec.get("description") or ""), "entities": entities}
            item |= last_load(runs, entities, unavailable) if need_load else {}
            if need_trust and p.status != "draft":
                item = self._gc_apply_trust(item, *self._gc_trust(item["id"]))
            out.append(item)
        return out

    def governance_check_now(self) -> dict[str, Any]:
        """Check the open project against the policies in force for it, on this PC. Warn only."""
        project = self._project()  # type: ignore[attr-defined]
        pname = mesh.project_name(project)
        resolved = self.governance_resolve()  # type: ignore[attr-defined]
        state = self._gc_state()
        stored = state.get("fabric", {}).get(pname, {})
        problems = list(resolved.get("problems") or [])
        wanted = {rid for p in resolved["policies"] for rid in gc.rule_ids(p.get("rule"))}
        try:
            saved = self._gc_catalog().call("enrichment", project=pname)
        except Exception as exc:  # noqa: BLE001 - checks read the Git catalog text alone, and say so
            _log.info("enrichment for %s: %s", pname, describe(exc))
            saved = {}
            problems.append(f"The platform's catalog text couldn't be read, so only the text in the project's files was checked: {describe(exc)}")
        items = entity_items(project, saved) + self._gc_product_items(
            project, bool(wanted & _LOAD_RULES), bool(wanted & _TERMS_RULES))
        if "notebook" in {found[0].kind for found in map(gc.find_rule, wanted) if found}:
            items += self._gc_notebook_items(problems)
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
                if not run["checked"] and not run.get("disabled"):
                    entry.setdefault("no_items", []).append(found[1]["kind"])
            note = (pol.get("exception") or {}).get("note")
            if note:
                for f in entry["findings"] + entry["unknown"]:
                    f["excepted"] = note
            entry["status"] = ("not_checked" if not ids or (entry["missing"] and not entry["checked"] and not entry["not_run"])
                               else "excepted" if note and entry["findings"]
                               else "findings" if entry["findings"]
                               else "unknown" if entry["unknown"]
                               else "not_run" if entry["not_run"] and not entry["checked"]
                               else "not_checked" if entry.get("no_items") and not entry["checked"]
                               else "pass")
            if not ids:
                entry["note"] = "Its Checked by field names no rule, so nothing checks it yet."
            elif entry["missing"]:
                entry["note"] = f"No checker on this PC has the rule {', '.join(entry['missing'])}."
            elif entry["status"] == "not_checked":
                kinds = ", ".join(sorted(set(entry["no_items"])))
                entry["note"] = f"Check now found nothing its rules check ({kinds}) in this project."
            entry.pop("no_items", None)
            policies.append(entry)
        order = {"findings": 0, "unknown": 1, "excepted": 2, "not_run": 3, "not_checked": 4, "pass": 5}
        policies.sort(key=lambda p: (order[p["status"]], str(p["name"]).lower()))
        result = {"project": pname, "checked_at": _now(), "where": "this PC", "enforcement": WARN_ONLY,
                  "policies": policies, "inactive": resolved.get("inactive") or [], "problems": problems,
                  "counts": {"to_look_at": sum(len(p["findings"]) for p in policies if p["status"] != "excepted"),
                             "unknown": sum(len(p["unknown"]) for p in policies if p["status"] != "excepted"),
                             "excepted": sum(len(p["findings"]) for p in policies if p["status"] == "excepted"),
                             "passing": len([p for p in policies if p["status"] == "pass"]),
                             "policies": len(policies)}}
        state.setdefault("results", {})[pname] = result
        write_json(self._gc_path(), state)
        return result

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
            checker, rule = gc.find_rule(rid) or (None, None)
            if checker is None:
                continue
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
