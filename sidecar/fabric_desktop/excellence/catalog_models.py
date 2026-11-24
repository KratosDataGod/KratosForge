"""Semantic model controls (FEA-304), decided from the model's definition only (evidence ``item.model``,
excellence.inspect_models).

The definition is read as TMSL with the getDefinition call the Semantic model tab already makes
(model_ossie_service, model_directlake_service). The rule-based controls are the definition-only
semantic model rules (model_practices SM001, SM002, SM004, docs/semantic-model-rules.md) turned
into controls: the same rule decides here as on the Semantic model tab. Nothing that needs the
model's data is checked: high-cardinality columns, model size and the Direct Lake guardrails need
VertiPaq statistics or the Delta tables' numbers, which the definition doesn't hold. The full Best
Practice Analyzer runs in Fabric and only when asked (model_bpa); these controls don't start it.

Evidence keeps rule ids, table, column, relationship and role names, and counts. Never DAX, M, a
role's filter or members, or the definition text. A definition that wasn't read leaves these
controls not evaluated.

References are to be re-checked against Microsoft Learn under FEA-106 before these controls leave
Preview.
"""

from __future__ import annotations

from typing import Any

from . import controls as ct

KIND = "item.model"

_SM_DOCS = "https://kratosdatagod.github.io/kratosforge/semantic-model-rules/"
_FEA_DOC = {"title": "KratosForge Fabric Excellence Assessment",
            "url": "https://kratosdatagod.github.io/kratosforge/optimize/excellence-assessment/"}
_BIDI_DOC = {"title": "Bi-directional relationship guidance",
             "url": "https://learn.microsoft.com/power-bi/guidance/relationships-bidirectional-filtering"}
_RELATIONSHIPS_DOC = {"title": "Model relationships in Power BI Desktop",
                      "url": "https://learn.microsoft.com/power-bi/transform-model/desktop-relationships-understand"}
_RLS_DOC = {"title": "Row-level security (RLS) with Power BI",
            "url": "https://learn.microsoft.com/power-bi/enterprise/service-admin-rls"}
_RLS_GUIDANCE = {"title": "Row-level security (RLS) guidance in Power BI Desktop",
                 "url": "https://learn.microsoft.com/power-bi/guidance/rls-guidance"}
_DIRECT_LAKE_DOC = {"title": "Direct Lake overview",
                    "url": "https://learn.microsoft.com/fabric/fundamentals/direct-lake-overview"}
_STAR_DOC = {"title": "Understand star schema and the importance for Power BI",
             "url": "https://learn.microsoft.com/power-bi/guidance/star-schema"}


def _sm_doc(rule: str, title: str) -> dict[str, str]:
    return {"title": f"KratosForge semantic model rule {rule}: {title}", "url": f"{_SM_DOCS}#{rule.lower()}"}


def _payload(ctx: ct.Context) -> tuple[str, dict[str, Any]]:
    r = ctx.one(KIND)
    assert r is not None  # declared evidence is present before a control runs
    return r["id"], r["payload"] or {}


def _read(p: dict[str, Any]) -> bool:
    rel = p.get("relationships")
    return p.get("kind") == "model" and isinstance(rel, int) and not isinstance(rel, bool)


def _names(names: list[str], count: int) -> str:
    more = count - len(names[:5])
    return ", ".join(names[:5]) + (f" and {more} more" if more > 0 else "")


def _s(n: int, one: str, many: str) -> str:
    return one if n == 1 else many


def _not_read() -> dict[str, Any]:
    return ct.not_evaluated("The model's definition wasn't inspected.", needs="the semantic model's definition")


def _bidirectional(ctx: ct.Context) -> dict[str, Any]:
    """Medium: a relationship that filters both ways adds filter paths the model's author may not
    intend, can make results depend on which path the engine takes (ambiguity), and makes queries
    slower because every filter propagates further. Microsoft's guidance is to use it only where a
    many-to-many or a special case needs it. Not high: totals are wrong only in reports that rely on
    the extra path, and the fix is one setting per relationship."""
    rid, p = _payload(ctx)
    if not _read(p):
        return _not_read()
    count = int(p.get("bidirectional_count") or 0)
    names = list(p.get("bidirectional") or [])
    if count:
        return ct.failed([rid], f"{count} {_s(count, 'relationship filters', 'relationships filter')} in both "
                                f"directions: {_names(names, count)}.",
                         detail={"relationships": names, "count": count})
    return ct.passed([rid], f"None of its {p['relationships']} relationships filters in both directions.")


def _row_security(ctx: ct.Context) -> dict[str, Any]:
    """Informational: a model with no role that filters rows shows every reader every row. That is
    right for many models (company-wide figures), so it is noted, not scored; the finding says so,
    and a profile that requires RLS can raise it. Only the presence of a row filter is checked: what
    a filter does, and who is in a role, aren't read or kept."""
    rid, p = _payload(ctx)
    if not _read(p):
        return _not_read()
    roles = list(p.get("row_filter_roles") or [])
    tables = list(p.get("row_filter_tables") or [])
    if roles:
        return ct.passed([rid], f"{len(roles)} {_s(len(roles), 'role filters', 'roles filter')} rows "
                                f"({_names(roles, len(roles))}), on {_names(tables, len(tables))}.")
    total = int(p.get("roles") or 0)
    why = (f"Its {total} {_s(total, 'role filters', 'roles filter')} no rows" if total
           else "It has no security role")
    return ct.failed([rid], f"{why}, so everyone who can read the model sees every row. That is fine when every "
                            "reader may see all of it.", detail={"roles": total})


def _fallback_applies(ctx: ct.Context) -> bool | None:
    p = (ctx.one(KIND) or {}).get("payload") or {}
    if not _read(p):
        return None
    return int(p.get("direct_lake_tables") or 0) > 0


def _fallback(ctx: ct.Context) -> dict[str, Any]:
    """Low: with the default Direct Lake behavior (Automatic), a query that Direct Lake can't answer
    (a SQL view, a table past the capacity's guardrails, row-level security in the SQL endpoint)
    silently runs as DirectQuery instead, slower and on more capacity, and nobody is told. Setting
    DirectLakeOnly makes that an error you see. Low: results stay correct, only speed and capacity
    change, and leaving fallback on is a valid choice for some models. Direct Lake on OneLake never
    falls back, so it passes."""
    rid, p = _payload(ctx)
    n = int(p.get("direct_lake_tables") or 0)
    behavior = str(p.get("direct_lake_behavior") or "automatic")
    sources = list(p.get("direct_lake_sources") or [])
    tables = f"{n} Direct Lake {_s(n, 'table', 'tables')}"
    if sources == ["onelake"]:
        return ct.passed([rid], f"Its {tables} {_s(n, 'reads', 'read')} through OneLake, which doesn't fall back to DirectQuery.")
    if behavior.lower() == "directlakeonly":
        return ct.passed([rid], f"Its {tables} {_s(n, 'is', 'are')} set to Direct Lake only, so a query that can't use Direct "
                                "Lake fails visibly instead of falling back.")
    if behavior.lower() == "directqueryonly":
        return ct.failed([rid], f"Its {tables} {_s(n, 'is', 'are')} set to DirectQuery only, so no query uses Direct Lake.",
                         detail={"behavior": behavior, "tables": n})
    return ct.failed([rid], f"Its {tables} can fall back to DirectQuery without telling anyone (Direct Lake "
                            "behavior is Automatic).", detail={"behavior": behavior, "tables": n})


def _rule(ctx: ct.Context, rule: str, found: str, clean: str, unknown_key: str) -> dict[str, Any]:
    """Fail when semantic model rule ``rule`` matched, pass when every relationship could be checked
    and it didn't."""
    rid, p = _payload(ctx)
    if not _read(p) or not isinstance(p.get("rules"), dict):
        return _not_read()
    hit = p["rules"].get(rule)
    if hit:
        count = int(hit.get("count") or 0)
        objects = list(hit.get("objects") or [])
        return ct.failed([rid], f"{found} ({rule}). {count} found: {_names(objects, count)}.",
                         detail={"rule": rule, "count": count, "objects": objects})
    unknown = list(p.get(unknown_key) or [])
    if unknown:
        return ct.not_evaluated(f"The definition doesn't show what this checks for {len(unknown)} "
                                f"{_s(len(unknown), 'relationship', 'relationships')} ({_names(unknown, len(unknown))}).",
                                needs="the relationship columns' definitions")
    return ct.passed([rid], f"{clean} ({rule}, {p['relationships']} {_s(p['relationships'], 'relationship', 'relationships')} checked).")


def _types_differ(ctx: ct.Context) -> dict[str, Any]:
    """SM001. Medium: both columns of a relationship need the same data type. When they differ the
    relationship can't be used (Direct Lake rejects it; import models convert keys and match
    wrongly or slowly), so visuals across it show errors or blanks. Bounded to the reports that use
    that relationship, so not high."""
    return _rule(ctx, "SM001", "A relationship joins columns of different types",
                 "Every relationship joins columns of the same type", "types_unknown")


def _text_keys(ctx: ct.Context) -> dict[str, Any]:
    """SM002. Low: joins on whole numbers are smaller and faster than on text or dates. A
    performance cost, not a correctness one, and a model change to fix."""
    return _rule(ctx, "SM002", "A relationship key isn't a whole number",
                 "Every relationship key is a whole number", "types_unknown")


def _visible_keys(ctx: ct.Context) -> dict[str, Any]:
    """SM004. Low: a visible foreign key invites people to filter a fact by its key instead of the
    dimension's columns, which gives confusing field lists and, with two keys, inconsistent
    slicing. Usability, with a one-click fix."""
    return _rule(ctx, "SM004", "A relationship's key column is visible in the fact table",
                 "Every fact-side relationship key is hidden", "columns_unknown")


def _t(why: str, solution: str, steps: list[str], benefit: str, effort: str, risk: str, validation: str,
       rollback: str) -> dict[str, Any]:
    return {"why": why, "solution": solution, "steps": steps, "benefit": benefit, "effort": effort, "risk": risk,
            "validation": validation, "rollback": rollback}


def _sm(**kw: Any) -> ct.Control:
    return ct.Control(version="1.0.0", scope="item", item_types=("SemanticModel",), evidence_kinds=(KIND,),
                      dimension="semantic_models", **kw)


CONTROLS = (
    _sm(id="FEA-SM-001", title="Relationship filters in both directions", severity="medium",
        requirement="microsoft_recommendation", evaluate=_bidirectional, references=(_BIDI_DOC, _RELATIONSHIPS_DOC),
        description="An active relationship's cross-filter direction is Both.",
        remediation=_t("Filtering both ways adds filter paths, can make results ambiguous, and slows every query "
                       "that crosses the relationship.",
                       "Set the relationship to filter in one direction, and use CROSSFILTER in the one measure "
                       "that needs both, if any.",
                       ["Open the model and find the relationships named in the finding.",
                        "Set Cross filter direction to Single.",
                        "Check the reports that use them. Add CROSSFILTER to a measure that needs the other way.",
                        "Assess again."],
                       "Queries filter along the paths you designed, and run faster.", "medium", "medium",
                       "Report totals are unchanged where they were right, and the control passes.",
                       "Set the direction back to Both.")),
    _sm(id="FEA-SM-002", title="Model has no row-level security", severity="informational",
        requirement="kratosforge_recommendation", evaluate=_row_security, references=(_RLS_DOC, _RLS_GUIDANCE),
        description="No security role filters rows. Noted, not scored, because many models are meant for every reader.",
        remediation=_t("Without a role that filters rows, everyone who can read the model sees every row.",
                       "If some readers may see only part of the data, add a role with a row filter and assign "
                       "people to it; otherwise nothing needs to change.",
                       ["Decide whether every reader may see every row.",
                        "If not, add a role with a filter on the table that defines who sees what.",
                        "Assign groups to it in the model's security settings.",
                        "Test it with View as role.", "Assess again."],
                       "Each reader sees only the rows they're allowed to.", "medium", "medium",
                       "View as role shows the expected rows for each role.",
                       "Remove the role.")),
    _sm(id="FEA-SM-003", title="Direct Lake model falls back to DirectQuery silently", severity="low",
        requirement="kratosforge_recommendation", evaluate=_fallback, applies=_fallback_applies,
        references=(_DIRECT_LAKE_DOC, _FEA_DOC),
        description="The model's Direct Lake behavior is Automatic (or DirectQuery only). Not applicable to a "
                    "model with no Direct Lake table.",
        remediation=_t("With Automatic, a query Direct Lake can't answer runs as DirectQuery instead, which is "
                       "slower and uses more capacity, and nobody is told.",
                       "Set the model's Direct Lake behavior to Direct Lake only, so fallback shows as an error you "
                       "can fix (compact the table, replace the view).",
                       ["Run the Direct Lake check on the Semantic model tab to see what would fall back.",
                        "Fix what it finds.", "Set the Direct Lake behavior to Direct Lake only.",
                        "Open the main reports to confirm they load.", "Assess again."],
                       "Queries stay on Direct Lake, or fail where you can see them.", "low", "medium",
                       "The main reports load, and the control passes.",
                       "Set the Direct Lake behavior back to Automatic.")),
    _sm(id="FEA-SM-004", title="Relationship joins columns of different types", severity="medium",
        requirement="microsoft_recommendation", evaluate=_types_differ,
        references=(_RELATIONSHIPS_DOC, _sm_doc("SM001", "Relationship columns have the same type")),
        description="Semantic model rule SM001: a relationship's two columns have different data types.",
        remediation=_t("A relationship between columns of different types can't be used as designed, so visuals "
                       "across it show errors or blanks.",
                       "Give both columns the same type, at the source when you can.",
                       ["Find the relationships named in the finding.",
                        "Change the fact's key type to the dimension key's type (in the table, or the model).",
                        "Refresh or reframe the model.", "Assess again."],
                       "The relationship filters as designed.", "low", "low",
                       "Visuals across the relationship show values, and the control passes.",
                       "Put the previous type back.")),
    _sm(id="FEA-SM-005", title="Relationship key isn't a whole number", severity="low",
        requirement="kratosforge_recommendation", evaluate=_text_keys,
        references=(_STAR_DOC, _sm_doc("SM002", "Relationship keys are integers")),
        description="Semantic model rule SM002: a relationship joins on text, dates or decimals.",
        remediation=_t("Joins on text or dates are bigger and slower than on whole numbers.",
                       "Join on an integer surrogate key.",
                       ["Add an integer key to the dimension and the fact (KratosForge keys or a lookup).",
                        "Relate the tables on it and hide it.", "Refresh or reframe the model.", "Assess again."],
                       "Smaller model and faster joins.", "medium", "low",
                       "Report totals are unchanged, and the control passes.",
                       "Relate the tables on the previous columns again.")),
    _sm(id="FEA-SM-006", title="Relationship key visible in a fact table", severity="low",
        requirement="kratosforge_recommendation", evaluate=_visible_keys,
        references=(_STAR_DOC, _sm_doc("SM004", "Foreign keys are hidden")),
        description="Semantic model rule SM004: the many-side column of a relationship isn't hidden.",
        remediation=_t("People filter by a fact's key instead of the dimension's columns, which clutters the "
                       "field list and slices inconsistently.",
                       "Hide the key column in the fact table.",
                       ["Find the columns named in the finding.", "Set each to hidden.", "Assess again."],
                       "A cleaner field list, and slicing through the dimension.", "low", "low",
                       "The field list no longer shows the key, and the control passes.",
                       "Unhide the column.")),
)
