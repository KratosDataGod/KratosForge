"""Architecture controls (FEA-203), and the evidence they read.

Every control here decides from reads the assessment already makes, or that the app already makes
for the same workspace with the signed-in person's own access. No new Fabric endpoint:

- ``workspace.naming`` (inferred): the workspace's name and its items' names, from
  ``GET /workspaces/{id}`` and ``GET /workspaces/{id}/items``, sorted by a name heuristic into
  medallion layer (bronze, silver, gold), environment (production or not) and left-over words (tmp,
  test, staging, copy, old ...). A name is what someone typed, not what the item is, so this evidence
  is **inferred**: controls on it are advice, shown but not scored (controls.evaluate), because none
  of them sets ``accepts_inferred``.
- ``lakehouse.properties`` (verified configuration): whether a lakehouse was created with schemas,
  from ``GET /workspaces/{id}/lakehouses/{id}``, the read the app already makes before it writes a
  table (service._schemas_enabled). Fabric gives ``defaultSchema`` only for schema-enabled lakehouses.
- ``item.binding`` (verified configuration): a notebook's default lakehouse, from the notebook
  definition the optimization engine already reads (getDefinition).

A read that failed gives no record, so its controls are not evaluated, never passed. The adapters
are here rather than in excellence.adapters so the architecture dimension stays in one module.

The medallion rule follows the KratosForge standing decision: one lakehouse per medallion layer,
schema-enabled. Microsoft's medallion guidance recommends a separate lakehouse (or warehouse) per
layer; "exactly one per layer" and "schema-enabled" are KratosForge's, so those controls are
``kratosforge_recommendation`` and a profile can turn them off.

Dev/Test/Prod separation is not checked here: deciding it needs deployment pipeline or Git
configuration the assessment doesn't read, and a name alone can't prove separation.
"""

from __future__ import annotations

import re
from typing import Any

from . import controls as ct
from . import evidence as ev

GET_WORKSPACE_AND_ITEMS = "Names from GET /v1/workspaces/{id} and GET /v1/workspaces/{id}/items (KratosForge name heuristic)"
GET_LAKEHOUSE = "GET /v1/workspaces/{id}/lakehouses/{lakehouseId}"
NOTEBOOK_DEFINITION = "Notebook definition (getDefinition), read by the KratosForge optimization engine"

LAYERS = ("bronze", "silver", "gold")
PROD_WORDS = frozenset({"prod", "prd", "production", "live"})
NONPROD_WORDS = frozenset({"dev", "development", "test", "tst", "uat", "qa", "sandbox", "sbx", "staging", "stg",
                           "preprod", "nonprod", "sit", "demo", "poc"})
LEFTOVER_WORDS = frozenset({"tmp", "temp", "test", "testing", "staging", "copy", "old", "backup", "bak", "scratch",
                            "sandbox", "deprecated", "draft"})
# Fabric creates these for Dataflow Gen2 staging; they are system items, not left-overs.
SYSTEM_PREFIXES = ("dataflowsstaging", "staginglakehousefordataflows", "stagingwarehousefordataflows")

_PREPROD = re.compile(r"(pre|non)[\s_.-]?prod", re.IGNORECASE)
_CAMEL = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")
_SPLIT = re.compile(r"[^a-z0-9]+")


def words(name: str) -> set[str]:
    """The lower-case words in a name: split on anything not a letter or digit, and on camelCase
    (``LH_Bronze``, ``SalesGold``, ``orders-copy`` -> lh, bronze, sales, gold, orders, copy). ``pre-prod``
    and ``non_prod`` stay one word, so they aren't read as ``prod``."""
    text = _PREPROD.sub(lambda m: m.group(1).lower() + "prod", str(name or ""))
    parts = _SPLIT.split(text.lower()) + _SPLIT.split(_CAMEL.sub(" ", text).lower())
    return {p for p in parts if p}


def environment(name: str) -> tuple[str | None, list[str]]:
    """``production``, ``non_production`` or None (no word says, or words say both), and the words."""
    w = words(name)
    prod, nonprod = sorted(w & PROD_WORDS), sorted(w & NONPROD_WORDS)
    if prod and not nonprod:
        return "production", prod
    if nonprod and not prod:
        return "non_production", nonprod
    return None, prod + nonprod


# ---- evidence


def _ws_subject(ws: dict[str, Any]) -> dict[str, Any]:
    return ev.subject("workspace", str(ws.get("id") or ""), ws.get("displayName") or ws.get("name"))


def naming(ws: dict[str, Any], items: list[dict[str, Any]] | None, *, collected_at: str) -> list[dict[str, Any]]:
    """One inferred ``workspace.naming`` record, when the items could be listed (``None``: they couldn't).
    The workspace name is used only when it was read; without it the environment is not known."""
    if items is None:
        return []
    ws_name = str(ws.get("displayName") or "")
    env, env_words = environment(ws_name) if ws_name else (None, [])
    lakehouses, leftovers = [], []
    for it in items:
        if not it.get("id") or not it.get("type"):
            continue
        name = str(it.get("displayName") or "")
        w = words(name)
        if it["type"] == "Lakehouse":
            lakehouses.append({"id": str(it["id"]), "name": name, "layers": [x for x in LAYERS if x in w]})
        system = name.replace(" ", "").lower().startswith(SYSTEM_PREFIXES)
        hit = sorted(w & LEFTOVER_WORDS)
        if hit and not system:
            leftovers.append({"id": str(it["id"]), "type": str(it["type"]), "name": name, "words": hit})
    return [ev.record("workspace.naming", _ws_subject(ws), source=GET_WORKSPACE_AND_ITEMS, confidence="inferred",
                      collected_at=collected_at,
                      payload={"workspace_name": ws_name, "environment": env, "environment_words": env_words,
                               "lakehouses": sorted(lakehouses, key=lambda x: x["id"]),
                               "leftovers": sorted(leftovers, key=lambda x: x["id"])})]


def lakehouse_properties(ws_id: str, lakehouses: list[dict[str, Any]], read: dict[str, dict[str, Any] | None], *,
                         collected_at: str) -> list[dict[str, Any]]:
    """One ``lakehouse.properties`` record per lakehouse whose ``GET .../lakehouses/{id}`` answer is in
    ``read`` (``None`` or absent: it wasn't read, so no record)."""
    out = []
    for lh in lakehouses:
        got = read.get(str(lh.get("id")))
        if got is None:
            continue
        props = got.get("properties") or {}
        subj = ev.subject("item", str(lh["id"]), lh.get("displayName") or got.get("displayName"),
                          workspace_id=ws_id, item_type="Lakehouse")
        out.append(ev.record("lakehouse.properties", subj, source=GET_LAKEHOUSE, confidence="verified_configuration",
                             collected_at=collected_at,
                             payload={"schemas_enabled": bool(props.get("defaultSchema")),
                                      "default_schema": props.get("defaultSchema") or None}))
    return out


def notebook_bindings(opt: dict[str, Any], *, collected_at: str) -> list[dict[str, Any]]:
    """One ``item.binding`` record per notebook the optimization engine tried to read: its default
    lakehouse (None: it has none) when the definition was read, ``insufficient`` when it wasn't."""
    out = []
    for w in opt.get("workloads") or []:
        if w.get("type") != "Notebook" or w.get("definition") not in ("read", "not_read"):
            continue
        read = w["definition"] == "read"
        subj = ev.subject("item", str(w["item_id"]), w.get("name"), workspace_id=str(w.get("workspace_id")),
                          item_type="Notebook")
        payload = {"default_lakehouse": (str(w["default_lakehouse"]) if w.get("default_lakehouse") else None)
                   if read else None, "definition": w["definition"]}
        out.append(ev.record("item.binding", subj, source=NOTEBOOK_DEFINITION,
                             confidence="verified_configuration" if read else "insufficient",
                             collected_at=collected_at, payload=payload))
    return out


def evidence(ws: dict[str, Any], items: list[dict[str, Any]] | None, opt: dict[str, Any],
             lakehouses_read: dict[str, dict[str, Any] | None], *, collected_at: str) -> list[dict[str, Any]]:
    """All the architecture evidence for one workspace."""
    lakehouses = [i for i in items or [] if i.get("type") == "Lakehouse" and i.get("id")]
    return (naming(ws, items, collected_at=collected_at)
            + lakehouse_properties(str(ws.get("id") or ""), lakehouses, lakehouses_read, collected_at=collected_at)
            + notebook_bindings(opt, collected_at=collected_at))


# ---- controls

_MEDALLION_DOC = {"title": "Implement medallion lakehouse architecture in Microsoft Fabric",
                  "url": "https://learn.microsoft.com/fabric/onelake/onelake-medallion-lakehouse-architecture"}
_SCHEMAS_DOC = {"title": "Lakehouse schemas", "url": "https://learn.microsoft.com/fabric/data-engineering/lakehouse-schemas"}
_LIFECYCLE_DOC = {"title": "What is lifecycle management in Microsoft Fabric?",
                  "url": "https://learn.microsoft.com/fabric/cicd/cicd-overview"}
_DEPLOY_DOC = {"title": "Introduction to deployment pipelines",
               "url": "https://learn.microsoft.com/fabric/cicd/deployment-pipelines/intro-to-deployment-pipelines"}
_NB_DEPLOY_DOC = {"title": "Notebook source control and deployment",
                  "url": "https://learn.microsoft.com/fabric/data-engineering/notebook-source-control-deployment"}


def _naming(ctx: ct.Context) -> tuple[str, dict[str, Any]]:
    r = ctx.one("workspace.naming")
    assert r is not None  # declared evidence is present before a control runs
    return r["id"], r["payload"]


def _names(rows: list[dict[str, Any]], limit: int = 5) -> str:
    names = [str(x.get("name") or x.get("id")) for x in rows][:limit]
    more = len(rows) - len(names)
    return ", ".join(names) + (f" and {more} more" if more > 0 else "")


def _has_lakehouses(ctx: ct.Context) -> bool | None:
    return bool(_naming(ctx)[1].get("lakehouses"))


def _medallion(ctx: ct.Context) -> dict[str, Any]:
    """FEA-ARCH-001, severity medium: layers mixed in one lakehouse, or one layer split over several,
    blur which tables are raw and which are curated and stop access and retention being set per
    layer. That is a design weakness with real consequences for who reads what, but it doesn't stop
    work running or expose data by itself, so it isn't high. The layer is read from the lakehouse
    name, so the evidence is inferred and the result is advice, not scored."""
    rid, p = _naming(ctx)
    lakehouses = list(p.get("lakehouses") or [])
    named = [lh for lh in lakehouses if lh.get("layers")]
    if not named:
        return ct.not_evaluated(f"None of the {len(lakehouses)} lakehouse name{'s' if len(lakehouses) != 1 else ''} "
                                "says bronze, silver or gold, so their layers can't be told.",
                                needs="lakehouse names that say their layer, or a layer setting")
    mixed = [lh for lh in named if len(lh["layers"]) > 1]
    per_layer = {layer: [lh for lh in named if lh["layers"] == [layer]] for layer in LAYERS}
    split = {layer: got for layer, got in per_layer.items() if len(got) > 1}
    if mixed or split:
        parts = []
        if mixed:
            parts.append(f"{_names(mixed)} name{'s' if len(mixed) == 1 else ''} more than one layer")
        for layer, got in split.items():
            parts.append(f"{len(got)} lakehouses are named {layer} ({_names(got)})")
        return ct.failed([rid], "; ".join(parts) + ".", detail={"mixed": len(mixed),
                                                                "split": {k: len(v) for k, v in split.items()}})
    present = [layer for layer in LAYERS if per_layer[layer]]
    return ct.passed([rid], "One lakehouse per layer named here (" + ", ".join(present) + "). Layers kept in other "
                            "workspaces aren't checked.")


def _schemas(ctx: ct.Context) -> dict[str, Any]:
    """FEA-ARCH-002, severity low: a lakehouse without schemas keeps every table in one flat list, so
    tables can't be grouped, secured or shortcut by schema. It works and loses no data; changing it
    means a new lakehouse and moving tables, which is why it is advice to plan rather than an urgent
    risk. Verified from the lakehouse's own properties."""
    r = ctx.one("lakehouse.properties")
    assert r is not None
    if r["payload"].get("schemas_enabled"):
        return ct.passed([r["id"]], f"Schema-enabled (default schema {r['payload'].get('default_schema') or 'dbo'}).")
    return ct.failed([r["id"]], "This lakehouse was created without schemas: its tables are one flat list.")


def _not_dev(ctx: ct.Context) -> bool | None:
    return _naming(ctx)[1].get("environment") != "non_production"


def _leftovers(ctx: ct.Context) -> dict[str, Any]:
    """FEA-ARCH-003, severity low: temporary, test or copied items left in production are clutter that
    someone can read, schedule or build on by mistake, and they make the workspace harder to review.
    Rarely harmful on their own, so low. Both "this is production" and "this is a left-over" come from
    names, so the evidence is inferred and the result is advice, not scored."""
    rid, p = _naming(ctx)
    if p.get("environment") != "production":
        said = ", ".join(p.get("environment_words") or [])
        return ct.not_evaluated("The workspace name doesn't say whether this is production"
                                + (f" (it says both: {said})." if said else "."),
                                needs="a workspace name that says its environment, or its deployment stage")
    left = list(p.get("leftovers") or [])
    if left:
        return ct.failed([rid], f"{len(left)} item{'s' if len(left) != 1 else ''} in this production workspace "
                                f"{'have' if len(left) != 1 else 'has'} a temporary or test name: {_names(left)}.",
                         detail={"items": len(left)})
    return ct.passed([rid], "No item in this production workspace is named tmp, temp, test, staging, copy, old, "
                            "backup or similar.")


def _bound(ctx: ct.Context) -> bool | None:
    r = ctx.one("item.binding")
    if r is None or "default_lakehouse" not in (r["payload"] or {}):
        return None
    return r["payload"]["default_lakehouse"] is not None


def _binding(ctx: ct.Context) -> dict[str, Any]:
    """FEA-ARCH-004, severity medium: a notebook whose default lakehouse is outside its workspace isn't
    rebound when it is deployed to the next stage, so a Prod notebook can keep reading or writing a
    Dev lakehouse. That can put wrong data in front of people, which is more than tidiness, but it
    needs a deployment to bite, so medium rather than high. Verified from the notebook definition
    and the workspace's item list."""
    b = ctx.one("item.binding")
    inv = ctx.one("workspace.inventory")
    assert b is not None and inv is not None
    lh = str(b["payload"]["default_lakehouse"])
    here = {str(i.get("id")).lower() for i in (inv["payload"].get("items") or []) if i.get("type") == "Lakehouse"}
    if lh.lower() in here:
        return ct.passed([b["id"], inv["id"]], "Its default lakehouse is in this workspace.")
    return ct.failed([b["id"], inv["id"]], f"Its default lakehouse ({lh}) isn't in this workspace: it is in another "
                                           "workspace, or it was deleted.", detail={"default_lakehouse": lh})


def _t(why: str, solution: str, steps: list[str], benefit: str, effort: str, risk: str, validation: str,
       rollback: str) -> dict[str, Any]:
    return {"why": why, "solution": solution, "steps": steps, "benefit": benefit, "effort": effort, "risk": risk,
            "validation": validation, "rollback": rollback}


_NAME_HEURISTIC = ("Decided from item names (inferred): shown as advice and not scored.")

CONTROLS = (
    ct.Control(
        id="FEA-ARCH-001", version="1.0.0", dimension="architecture", title="Not one lakehouse per medallion layer",
        severity="medium", requirement="kratosforge_recommendation", scope="workspace",
        evidence_kinds=("workspace.naming",), evaluate=_medallion, applies=_has_lakehouses,
        references=(_MEDALLION_DOC,),
        description="A lakehouse named for more than one of bronze, silver and gold, or two lakehouses named for the "
                    "same layer. " + _NAME_HEURISTIC,
        remediation=_t("Each medallion layer has its own readers, quality and retention. Mixing layers in one "
                       "lakehouse, or splitting one layer over several, blurs which tables are raw and which are "
                       "trusted, and stops access being set per layer.",
                       "Keep one schema-enabled lakehouse per layer (bronze, silver, gold), and name it for its layer. "
                       "If one layer is split by domain on purpose, turn this control off in your profile.",
                       ["Open the evidence: the lakehouses and the layers their names give.",
                        "Agree which lakehouse is each layer's.",
                        "Move or shortcut the tables of the others into it, and point notebooks and pipelines at it.",
                        "Retire the emptied lakehouses once nothing reads them.", "Assess again."],
                       "Each layer's tables are in one known place, with access set per layer.", "high", "medium",
                       "Every reader and writer uses the layer's lakehouse, and the control passes.",
                       "Point readers back at the old lakehouses; nothing is deleted until you retire them.")),
    ct.Control(
        id="FEA-ARCH-002", version="1.0.0", dimension="architecture", title="Lakehouse isn't schema-enabled",
        severity="low", requirement="kratosforge_recommendation", scope="item", item_types=("Lakehouse",),
        evidence_kinds=("lakehouse.properties",), evaluate=_schemas, references=(_SCHEMAS_DOC, _MEDALLION_DOC),
        description="The lakehouse was created without schemas (Fabric reports no default schema).",
        remediation=_t("Without schemas every table sits in one flat list: tables can't be grouped by source or "
                       "subject, or secured and shortcut a schema at a time.",
                       "Create new lakehouses with schemas, and plan a move for this one. An existing lakehouse "
                       "can't be switched to schemas in place.",
                       ["Create a schema-enabled lakehouse for the same layer.",
                        "Copy or shortcut the tables into schemas.", "Point notebooks, pipelines and models at it.",
                        "Compare row counts.", "Retire the old lakehouse when nothing reads it.", "Assess again."],
                       "Tables grouped and secured by schema.", "high", "medium",
                       "Readers return the same rows from the new lakehouse, and the control passes for it.",
                       "Point readers back at the old lakehouse; keep it until the move is confirmed.")),
    ct.Control(
        id="FEA-ARCH-003", version="1.0.0", dimension="architecture",
        title="Temporary or test items in a production workspace", severity="low",
        requirement="kratosforge_recommendation", scope="workspace", evidence_kinds=("workspace.naming",),
        evaluate=_leftovers, applies=_not_dev, references=(_LIFECYCLE_DOC, _DEPLOY_DOC),
        description="The workspace name says production and an item is named tmp, temp, test, staging, copy, old, "
                    "backup or similar (Dataflow staging items Fabric creates are left out). Not applicable when the "
                    "name says Dev or Test; not evaluated when it doesn't say. " + _NAME_HEURISTIC,
        remediation=_t("Left-over temporary, test and copied items in production can be read, scheduled or built on "
                       "by mistake, and make it hard to see what the workspace really runs.",
                       "Remove left-overs from production, and keep experiments in a Dev or sandbox workspace.",
                       ["Open the evidence: the items and the words in their names.",
                        "Ask each owner whether the item is still used.",
                        "Move what's still needed to a Dev workspace, or rename it if it is production work.",
                        "Delete the rest once nothing reads it.", "Assess again."],
                       "Production holds only what production runs.", "low", "medium",
                       "Nothing that ran before fails, and the control passes.",
                       "Restore a deleted item from Git or the Dev workspace, or rename it back.")),
    ct.Control(
        id="FEA-ARCH-004", version="1.0.0", dimension="architecture",
        title="Notebook's default lakehouse is outside its workspace", severity="medium",
        requirement="kratosforge_recommendation", scope="item", item_types=("Notebook",),
        evidence_kinds=("item.binding", "workspace.inventory"), evaluate=_binding, applies=_bound,
        references=(_NB_DEPLOY_DOC, _DEPLOY_DOC),
        description="A cross-workspace dependency: the notebook's default lakehouse isn't in its own workspace. "
                    "Not applicable to a notebook with no default lakehouse.",
        remediation=_t("A default lakehouse in another workspace isn't rebound when the notebook is deployed, so the "
                       "deployed notebook keeps using the other workspace's data, and the notebook breaks if that "
                       "workspace changes or the lakehouse is deleted.",
                       "Attach the notebook to a lakehouse in its own workspace, and reach other workspaces' tables "
                       "through a shortcut in that lakehouse.",
                       ["Open the evidence: the notebook and its default lakehouse.",
                        "Add a shortcut to the tables it needs in a lakehouse of this workspace.",
                        "Set that lakehouse as the notebook's default.", "Run the notebook once and compare outputs.",
                        "Assess again."],
                       "Deployments rebind the notebook, and it no longer depends on another workspace's lakehouse.",
                       "medium", "medium", "The notebook's run gives the same output, and the control passes.",
                       "Set the old default lakehouse again.")),
)
