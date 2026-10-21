"""Keeping one table family of the central lakehouse from everyone who reads through DefaultReader.

OneLake security adds up what every role permits, so a family's tables can't be hidden with a role
of their own: DefaultReader has to stop reaching them. ``restrict_plan`` works out the new role list
from the current one: DefaultReader keeps its members and every path an admin gave it, except that
"*" (or "/Tables") becomes every schema that isn't a family's own, and Files, so tables added later to
dbo stay readable. Every family schema is left out, not just this one: once "*" is gone, a schema
DefaultReader doesn't list is hidden from it, so leaving them all out keeps each family free to be
restricted later. The family gets one reader role on its schema; every other role is left as it is,
and a role that already reads the family's schema for the same people isn't rewritten. It refuses rather
than guess: no DefaultReader, a DefaultReader it can't edit safely, a path that grants part of the
family's schema, or a role name already used for something else. Pure: it calls nothing.
"""

from __future__ import annotations

import copy
from typing import Any

from .. import access
from . import schema

DEFAULT_READER = "DefaultReader"


def _path_rule(role: dict[str, Any]) -> dict[str, Any]:
    """The one Path permission of a role with one Permit rule; raises for anything else."""
    rules = role.get("decisionRules") or []
    if len(rules) != 1 or rules[0].get("effect", "Permit") != "Permit":
        raise PermissionError(f"{role.get('name')} has more than one rule, or a rule that isn't Permit, so it isn't "
                              "changed automatically. Change it in OneLake > Access instead.")
    paths = [p for p in rules[0].get("permission") or [] if p.get("attributeName") == "Path"]
    if len(paths) != 1:
        raise PermissionError(f"{role.get('name')} doesn't say which paths it reads in one place, so it isn't "
                              "changed automatically. Change it in OneLake > Access instead.")
    return paths[0]


def _same(a: dict[str, Any], b: dict[str, Any]) -> bool:
    """Do two roles read the same paths for the same people? Fields Fabric adds (id, eTag) don't count."""
    def key(role: dict[str, Any]) -> tuple[Any, ...]:
        members = role.get("members") or {}
        entra = sorted(m.get("objectId") or "" for m in members.get("microsoftEntraMembers") or [])
        items = sorted(repr(sorted(m.items())) for m in members.get("fabricItemMembers") or [])
        return sorted(access._paths(role)), entra, items
    return key(a) == key(b)


def restrict_plan(roles: list[dict[str, Any]], family: schema.Family, role_name: str, members: list[str],
                  tenant_id: str, schemas: list[str]) -> dict[str, Any]:
    """The roles after keeping ``family``'s schema from DefaultReader and giving ``members`` the
    ``role_name`` role on it. ``schemas`` are the lakehouse's schema folders. Returns {roles,
    changes, default_reader_paths, family_path}; ``changes`` is empty when nothing would change."""
    if family.schema == schema.SCHEMA:
        raise ValueError(f"{family.title}'s tables are in dbo with everyone else's. Give the family a schema "
                         "of its own first.")
    family_path = f"/Tables/{family.schema}"
    reader = next((r for r in roles if r.get("name") == DEFAULT_READER), None)
    if reader is None:
        raise PermissionError("This lakehouse has no DefaultReader role, so it isn't set up the way this expects. "
                              "Check its roles in OneLake > Access.")
    new_reader = copy.deepcopy(reader)
    rule = _path_rule(new_reader)
    family_schemas = (set(schema.schemas()) | {family.schema}) - {schema.SCHEMA}
    others = [f"/Tables/{s}" for s in sorted(set(schemas) | {schema.SCHEMA}) if s not in family_schemas]
    paths: list[str] = []
    for p in rule.get("attributeValueIncludedIn") or []:
        p = p if p == "*" else "/" + p.strip("/")
        if p == "*":
            paths += others + ["/Files"]
        elif p == "/Tables":
            paths += others
        elif p == family_path or p.startswith(family_path + "/"):
            raise PermissionError(f"DefaultReader was given {p} on purpose, so it isn't taken away automatically. "
                                  "Change DefaultReader in OneLake > Access first.")
        else:
            paths.append(p)
    paths = list(dict.fromkeys(paths))
    changes = []
    if paths != (rule.get("attributeValueIncludedIn") or []):
        rule["attributeValueIncludedIn"] = paths
        changes.append(f"DefaultReader no longer reads {family_path}; it keeps its members and everything else it "
                       "read, but schemas created later stay hidden from it until someone adds them")
    existing = next((r for r in roles if r.get("name") == role_name), None)
    if existing is not None and access._paths(existing) != [family_path]:
        raise ValueError(f"There is already a role named {role_name} that reads something else. Pick another name.")
    own = access.make_role(role_name, [family_path], members, tenant_id, existing)
    if existing is None:
        changes.append(f"New role {role_name} reads {family_path}")
    elif _same(own, existing):
        own = existing
    else:
        changes.append(f"{role_name} gets the members you chose")
    out = [new_reader if r is reader else own if r is existing else r for r in roles]
    if existing is None:
        out.append(own)
    return {"roles": out, "changes": changes, "default_reader_paths": paths, "family_path": family_path}
