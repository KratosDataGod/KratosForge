"""Who an incident reaches: the data products built on its workload and the projects that read them.

A metadata workload ("meta:<project>:<entity>") feeds every data product port that publishes that
entity in the incident's environment. The projects linked to those ports read the data, so a late
or failed run reaches them. This is one hop, from the catalog's ports and links: what a consumer
project builds from its link isn't traced further. Pure: the catalog's products come in, the
picture goes out.
"""

from __future__ import annotations

import re
from typing import Any

_UPN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def workload_entity(workload: Any) -> tuple[str, str] | None:
    """(project, entity) of a metadata workload id, else None."""
    kind, _, rest = str(workload or "").partition(":")
    project, _, entity = rest.partition(":")
    return (project, entity) if kind == "meta" and project and entity else None


def _people(value: Any) -> list[str]:
    return [x.strip().lower() for x in re.split(r"[,;]", str(value or "")) if x.strip()]


def blast_radius(products: list[dict[str, Any]], project: str, entity: str, env: str,
                 followers: dict[str, list[str]] | None = None, me: str = "") -> dict[str, Any]:
    """The published products whose ports in ``env`` carry ``project``'s ``entity``, the projects
    linked to those ports, and the people to tell, each with why. Names that aren't UPNs can't be
    sent a notice and are listed in ``unreachable``; ``me`` is left out of ``people``."""
    followers = followers or {}
    hit: list[dict[str, Any]] = []
    consumers: list[dict[str, Any]] = []
    why: dict[str, list[str]] = {}

    def add(person: str, reason: str) -> None:
        if person and reason not in why.setdefault(person, []):
            why[person].append(reason)

    for p in products:
        if p.get("status") == "draft" or p.get("project") != project:
            continue
        ports = sorted({x["port"] for x in p.get("ports") or [] if x.get("entity") == entity and x.get("env") == env})
        if not ports:
            continue
        reads = [c for c in p.get("consumers") or [] if c.get("port") in ports and c.get("consumer_project") != project]
        hit.append({"id": p["id"], "name": p.get("name") or p["id"], "owner": p.get("owner") or "",
                    "domain_id": p.get("domain_id") or "", "status": p.get("status") or "", "ports": ports,
                    "consumers": sorted({c["consumer_project"] for c in reads})})
        for owner in _people(p.get("owner")):
            add(owner, f"owns {p['id']}")
        for c in reads:
            consumers.append({"project": c["consumer_project"], "alias": c.get("alias") or "", "product_id": p["id"],
                              "port": c["port"], "linked_by": (c.get("updated_by") or "").lower()})
            add((c.get("updated_by") or "").lower(), f"linked {c['consumer_project']} to {p['id']}")
        for f in followers.get(p["id"]) or []:
            add(str(f).lower(), f"follows {p['id']}")
    me = (me or "").lower()
    people = [{"upn": k, "why": v} for k, v in sorted(why.items()) if _UPN.match(k) and k != me]
    return {"project": project, "entity": entity, "env": env, "products": hit,
            "consumers": sorted(consumers, key=lambda c: (c["project"], c["product_id"], c["port"])),
            "people": people, "unreachable": sorted(k for k in why if not _UPN.match(k))}
